"""Pure logic for F36 / DD-93 reference-graph grounding: longest-match-first defined-term
detection with the two validation-spike guards (word-boundary + short-acronym), depth-1
resolution to definitions + cross-ref target bodies, and the ≤8 / ≤6 caps. No LLM, no DB."""

from __future__ import annotations

from backend.models.cross_references import StoredCrossReference
from backend.models.defined_terms import StoredDefinedTerm
from backend.models.imports import StoredNode
from backend.services.donna.grounding import build_reference_grounding


def _node(node_id: str, body: str, order: int = 0) -> StoredNode:
    return StoredNode(id=node_id, order_index=order, content_type="paragraph", body=body)


def _term(term: str, definition: str | None, source: str | None) -> StoredDefinedTerm:
    return StoredDefinedTerm(
        id=f"t-{term}", deal_id="d1", term=term, definition=definition, source_node_id=source
    )


def _ref(source: str, target: str | None) -> StoredCrossReference:
    return StoredCrossReference(
        id=f"r-{source}-{target}",
        source_node_id=source,
        source_contract_id="c1",
        target_node_id=target,
        target_contract_id="c1" if target is not None else None,
    )


def _nodes_by_id(*nodes: StoredNode) -> dict[str, StoredNode]:
    return {n.id: n for n in nodes}


# --- term used in focal clause resolves to its definition + source ----------


def test_used_term_resolves_to_definition_and_source_clause() -> None:
    focal = _node("n-focal", "The Royalty is payable quarterly.")
    src = _node("n-def", '"Royalty" means 5% of Net Sales.')
    terms = [_term("Royalty", "5% of Net Sales", "n-def")]
    out = build_reference_grounding(focal, _nodes_by_id(focal, src), terms, [])
    assert "[n-def]" in out  # the bracketed id is the DEFINING clause (citable)
    assert '"Royalty" means 5% of Net Sales' in out
    assert "DEFINED TERMS USED" in out


# --- short-acronym guard: bare acronym suppressed when a longer term wins ----


def test_bare_acronym_suppressed_when_longer_registered_term_wins() -> None:
    # "Licensed IP" is registered and present; the bare "IP" inside it must NOT mis-map to the
    # generic "IP" definition (the spike's 68-mis-map failure mode).
    focal = _node("n-focal", "Ownership of the Licensed IP remains with the Licensor.")
    terms = [
        _term("Licensed IP", "the patents and know-how licensed under this agreement", "n-a"),
        _term("IP", "intellectual property generally", "n-b"),
    ]
    by_id = _nodes_by_id(focal, _node("n-a", "x"), _node("n-b", "y"))
    out = build_reference_grounding(focal, by_id, terms, [])
    assert "Licensed IP" in out
    assert "intellectual property generally" not in out  # bare IP suppressed


def test_standalone_acronym_still_accepted_when_longer_term_absent() -> None:
    focal = _node("n-focal", "All IP created under the project vests in us.")
    terms = [
        _term("Licensed IP", "the licensed patents", "n-a"),  # registered but NOT present in body
        _term("IP", "intellectual property generally", "n-b"),
    ]
    by_id = _nodes_by_id(focal, _node("n-a", "x"), _node("n-b", "y"))
    out = build_reference_grounding(focal, by_id, terms, [])
    assert "intellectual property generally" in out  # genuine standalone use accepted


# --- word-boundary gate -----------------------------------------------------


def test_trailing_plural_s_allowed() -> None:
    focal = _node("n-focal", "Subject to all Applicable Laws of the jurisdiction.")
    terms = [_term("Applicable Law", "any statute or regulation in force", "n-a")]
    by_id = _nodes_by_id(focal, _node("n-a", "x"))
    out = build_reference_grounding(focal, by_id, terms, [])
    assert "any statute or regulation in force" in out  # "Laws" matches "Applicable Law"


def test_midword_match_rejected() -> None:
    # "Control" must NOT match inside "Controlled" (the trailing letter breaks the boundary).
    focal = _node("n-focal", "A Controlled subsidiary of the parent.")
    terms = [_term("Control", "direct or indirect power to direct management", "n-a")]
    by_id = _nodes_by_id(focal, _node("n-a", "x"))
    out = build_reference_grounding(focal, by_id, terms, [])
    assert out == ""  # no spurious mid-word match


# --- depth-1: a definition's own terms are NOT recursively pulled -----------


def test_depth_one_does_not_pull_definitions_own_terms() -> None:
    # Focal uses "Royalty"; the Royalty DEFINITION mentions "Net Sales" (itself a registered term),
    # but Net Sales must NOT be resolved — the scan reads the focal body only.
    focal = _node("n-focal", "The Royalty accrues monthly.")
    terms = [
        _term("Royalty", "5% of Net Sales for the period", "n-roy"),
        _term("Net Sales", "gross sales less returns and allowances", "n-ns"),
    ]
    by_id = _nodes_by_id(focal, _node("n-roy", "x"), _node("n-ns", "y"))
    out = build_reference_grounding(focal, by_id, terms, [])
    assert "5% of Net Sales for the period" in out
    assert "gross sales less returns and allowances" not in out  # not recursed


# --- caps -------------------------------------------------------------------


def test_definition_cap_is_eight() -> None:
    body = " ".join(f"Term{i}" for i in range(12))
    focal = _node("n-focal", body)
    terms = [_term(f"Term{i}", f"definition number {i}", f"n-{i}") for i in range(12)]
    by_id = _nodes_by_id(focal, *(_node(f"n-{i}", "x") for i in range(12)))
    out = build_reference_grounding(focal, by_id, terms, [])
    assert out.count('" means ') == 8  # capped at 8 definitions


def test_cross_ref_cap_is_six() -> None:
    focal = _node("n-focal", "See the referenced clauses.")
    targets = [_node(f"n-t{i}", f"target body {i}") for i in range(9)]
    refs = [_ref("n-focal", f"n-t{i}") for i in range(9)]
    by_id = _nodes_by_id(focal, *targets)
    out = build_reference_grounding(focal, by_id, [], refs)
    assert sum(f"target body {i}" in out for i in range(9)) == 6  # capped at 6 cross-refs


# --- cross-ref resolution ---------------------------------------------------


def test_cross_ref_resolves_target_body_only_for_focal_source() -> None:
    focal = _node("n-focal", "As set out in clause 9.")
    target = _node("n-target", "Indemnity: each party indemnifies the other.")
    other = _node("n-other", "Unrelated clause body.")
    refs = [
        _ref("n-focal", "n-target"),  # this clause's ref -> resolved
        _ref("n-other", "n-target"),  # a different clause's ref -> ignored here
        _ref("n-focal", None),  # unresolved -> skipped
    ]
    by_id = _nodes_by_id(focal, target, other)
    out = build_reference_grounding(focal, by_id, [], refs)
    assert "[n-target]" in out
    assert "Indemnity: each party indemnifies the other." in out
    assert "CROSS-REFERENCED CLAUSES" in out


def test_empty_when_nothing_resolves() -> None:
    focal = _node("n-focal", "Plain text with no defined terms or references.")
    assert build_reference_grounding(focal, _nodes_by_id(focal), [], []) == ""
