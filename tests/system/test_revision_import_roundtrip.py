"""F03b system round-trip on a real .docx (sample-contract.docx, repo root,
gitignored). Parses the sample as the baseline snapshot AND as a trivially-edited
incoming revision, runs the production adapters + the wired matcher, and asserts the
staged change list is sane and the Layer-A mechanical oracle holds. Skips cleanly
when the fixture is absent (CI / fresh clone)."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.models.contract_tree import ParsedTree
from backend.services.import_.docx_reader import read_docx
from backend.services.import_.revision_import import (
    baseline_to_clause_nodes,
    extract_hunks,
    incoming_to_clause_nodes,
    incoming_to_snapshot_nodes,
)
from backend.services.import_.revision_match import layer_a_invariants, match_revision
from backend.services.import_.tree_builder import build_tree

_FIXTURE = Path(__file__).resolve().parents[2] / "sample-contract.docx"

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="sample-contract.docx absent")


def _edit_first_prose(tree: ParsedTree) -> tuple[ParsedTree, int]:
    """Append a token to the first non-trivial prose node — a minimal counterparty edit."""
    for n in tree.nodes:
        if n.kind == "prose" and len((n.text or "").split()) >= 4:
            edited = n.model_copy(update={"text": (n.text or "") + " (as amended)"})
            nodes = [edited if x.index == n.index else x for x in tree.nodes]
            return ParsedTree(nodes=nodes), n.index
    raise pytest.skip.Exception("no suitable prose node to edit")


def test_roundtrip_one_edit_is_sane() -> None:
    base_tree = build_tree(read_docx(_FIXTURE))
    incoming_tree, edited_index = _edit_first_prose(base_tree)

    baseline = baseline_to_clause_nodes(incoming_to_snapshot_nodes(base_tree))
    incoming = incoming_to_clause_nodes(incoming_tree)

    result = match_revision(baseline, incoming)

    # Layer-A mechanical invariants must hold unconditionally.
    report = layer_a_invariants(baseline, incoming, result)
    assert report.passed, report.model_dump()

    # A single trivial edit must not shatter the tree: the edited node still matches
    # (no mass new/deleted churn), and counts partition both sides.
    assert len(result.matches) >= len(baseline) - 2
    assert len(result.new) <= 2 and len(result.deleted) <= 2

    # The edited node should match its baseline counterpart with a body that differs,
    # and extract_hunks should surface at least one hunk for it.
    edited_pairs = [m for m in result.matches if m.incoming_index == edited_index]
    assert edited_pairs, "edited node lost its match"
    base_by_id = {n.id: n for n in baseline if n.id is not None}
    pair = edited_pairs[0]
    hunks = extract_hunks(base_by_id[pair.baseline_id].body, incoming[edited_index].body)
    assert any(h.proposed_text and "amended" in h.proposed_text for h in hunks)
