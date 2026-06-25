"""Pure grounding assembly for Donna Q&A (F10 / SPEC §7 grounding): turn the retrieved
clause subtree and the issue ledger into id-tagged text blocks the prompt injects. No I/O
— so it is unit-testable and reused verbatim by the eval. Every clause line and ledger
line still carries its id in [brackets] for the hallucinated-id citation guard, but ALSO
a legible label (derived clause number or content-type label) so the prose never has to
echo a raw id. Labels reuse the export renderer's single numbering path (`_plan`, DD-43)
so a clause number here matches the number the same clause carries everywhere else."""

from __future__ import annotations

from backend.models.imports import StoredNode
from backend.models.insights import StoredPattern
from backend.models.issues import StoredIssue
from backend.services.export.render_docx import _plan

# F30 / DD-76: how a learned-pattern block must be framed wherever it is injected. Patterns
# are a RETRIEVAL INPUT — visibly distinct from grounded citations, operator-overridable,
# NEVER authoritative, NEVER cited, NEVER exported (§2.4). This header restates that invariant
# inside the prompt so the model treats patterns as background heuristics, not contract facts.
_PATTERN_HEADER = (
    "--- LEARNED NEGOTIATION PATTERNS (background heuristics — NOT authoritative, NOT "
    "citable, NEVER exported) ---\n"
    "These are heuristics Donna distilled from PAST closed issues, about how this operator / "
    "counterparty / deal type tends to negotiate. They are NOT grounded facts about THIS "
    "contract: do not cite them, do not treat them as binding, and never reproduce them in "
    "any drafted or exported language. The operator may override any of them. Use them only "
    "as soft prior context; the cited clauses and issue ledger are the authoritative grounding."
)

_PATTERN_LABELS = {
    "operator_style": "operator style",
    "counterparty_behavior": "counterparty",
    "deal_type_norm": "deal-type norm",
    "legal_team_tendency": "legal-team tendency",
}

_BODY_CHARS = 600


def _node_text(node: StoredNode) -> str:
    parts = [p for p in (node.heading, node.body or node.plain_text) if p]
    return " — ".join(parts)[:_BODY_CHARS] if parts else ""


def _content_label(node: StoredNode) -> str:
    """A legible content/role label for a non-clause node (or a clause with no derived
    number), mirroring the cockpit's `nonClauseLabel` vocabulary: an `appendix` node
    reads by its kind ("Appendix heading"/"Appendix body"/"Appendix table"), every other
    role as its title-cased name ("Recital", "Appendix title", "Signature block")."""
    if node.role == "appendix":
        kind = "table" if node.content_type == "table" else ("heading" if node.heading else "body")
        base = f"Appendix {kind}"
    else:
        base = node.role.replace("_", " ").capitalize()
    return f"{base} ({node.heading})" if node.heading else base


def _node_label(node: StoredNode, number: str | None) -> str:
    """Clause-role node with a derived number -> "clause <number>" (+ heading); anything
    else -> a content/role label. Never a raw id."""
    if node.role == "clause" and number is not None:
        return f"clause {number} ({node.heading})" if node.heading else f"clause {number}"
    return _content_label(node)


def build_label_map(nodes: list[StoredNode]) -> dict[str, str]:
    """node_id -> legible label for every node, reusing the export renderer's `_plan`
    numbering so clause numbers are consistent app-wide (one numbering source)."""
    numbers = {node.id: number for node, number in _plan(nodes) if number is not None}
    return {node.id: _node_label(node, numbers.get(node.id)) for node in nodes}


def _subtree_ids(nodes: list[StoredNode], root_id: str) -> list[str]:
    """root_id plus its descendants, in document (order_index) order, so a matched
    heading carries the clause's body and sub-clauses, not just the title."""
    children: dict[str, list[StoredNode]] = {}
    for node in nodes:
        if node.parent_id is not None:
            children.setdefault(node.parent_id, []).append(node)
    ordered: list[str] = []

    def _walk(node_id: str) -> None:
        ordered.append(node_id)
        for child in sorted(children.get(node_id, []), key=lambda n: n.order_index):
            _walk(child.id)

    _walk(root_id)
    return ordered


def build_clause_grounding(
    nodes: list[StoredNode], matched_node_id: str | None, labels: dict[str, str]
) -> str:
    """The matched clause subtree as `[id] <label> — <text>` lines, or empty when
    retrieval missed. The bracketed id stays for the citation guard; the label after it
    is what the answer refers to in prose."""
    if matched_node_id is None:
        return ""
    by_id = {n.id: n for n in nodes}
    if matched_node_id not in by_id:
        return ""
    lines = [
        f"[{node_id}] {labels.get(node_id, node_id)} — {_node_text(by_id[node_id])}"
        for node_id in _subtree_ids(nodes, matched_node_id)
        if _node_text(by_id[node_id])
    ]
    return "\n".join(lines)


def build_issue_focus(issue: StoredIssue, labels: dict[str, str]) -> str:
    """The single issue under recommendation (F11) as a labelled block — the focal
    grounding the recommendation prompt resolves. The anchor is the clause's legible
    label (never a raw id); `initiator` is spelled out because it drives propose-vs-counter
    (operator = we propose; counterparty = we counter their change)."""
    if issue.node_id is not None:
        anchor = labels.get(issue.node_id, issue.node_id)
    else:
        anchor = "contract-level (free-floating)"
    stance = (
        "counterparty (their_position is their proposed change — we are countering)"
        if issue.initiator == "counterparty"
        else f"{issue.initiator} (we raised this — we are proposing)"
    )
    return (
        f"Title: {issue.title}\n"
        f"Raised by: {stance}\n"
        f"Status: {issue.status}\n"
        f"Anchored clause: {anchor}\n"
        f"Our position: {issue.our_position or '—'}\n"
        f"Their position: {issue.their_position or '—'}\n"
        f"Options on table: {issue.options_on_table or '—'}"
    )


def build_pattern_grounding(patterns: list[StoredPattern]) -> str:
    """The learned-pattern retrieval block (F30 tier 8) for injection into Donna's prompt,
    or empty when there are none. NO ids in the lines (patterns are never cited) — each line
    is `- [<subject label>] <insight>`, under the non-authoritative header. Returned as a
    self-contained block the caller appends to the rendered prompt, so no prompt template
    gains a new slot (and existing callers/evals are unaffected)."""
    if not patterns:
        return ""
    lines = [
        f"- [{_PATTERN_LABELS.get(p.subject_type, p.subject_type)}] {p.insight}"
        for p in patterns
    ]
    return f"{_PATTERN_HEADER}\n" + "\n".join(lines)


def build_issue_ledger(issues: list[StoredIssue], labels: dict[str, str]) -> str:
    """The issue ledger as id-tagged status lines — the grounding for status-briefing
    questions. The clause reference is the legible label in a bare parenthetical
    ("(clause 6.1 (Confidentiality))", "(Date)", "(contract-level)") — never "anchored to
    …", never a raw node id; the bracketed issue id stays for the guard."""
    lines: list[str] = []
    for issue in issues:
        if issue.node_id is not None:
            anchor = labels.get(issue.node_id, issue.node_id)
        else:
            anchor = "contract-level"
        lines.append(
            f"[{issue.id}] {issue.title} — status: {issue.status} ({anchor}); "
            f"our position: {issue.our_position or '—'}; "
            f"their position: {issue.their_position or '—'}"
        )
    return "\n".join(lines)
