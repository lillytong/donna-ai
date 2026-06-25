"""F31 issue-list export — row mapping, filter, and ordering (pure, no DB).

All issues here are synthetic and generic (no real contract content) — the unit
under test is the projection from StoredIssue → IssueRow plus the unresolved
filter and the priority/free-floating ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.models.imports import StoredNode
from backend.models.issues import StoredIssue
from backend.services.export.issue_export import (
    EM_DASH,
    build_export,
    render_issue_list_docx,
)

_NOW = datetime(2026, 6, 24, tzinfo=UTC)
_DOCX_MAGIC = b"PK\x03\x04"


def _issue(
    issue_id: str,
    *,
    title: str = "Generic point",
    status: str = "open",
    initiator: str = "operator",
    node_id: str | None = None,
    priority: int | None = None,
    our_position: str | None = None,
    their_position: str | None = None,
    recommended_position: str | None = None,
    donna_counter_language: str | None = None,
    created_at: datetime = _NOW,
) -> StoredIssue:
    return StoredIssue(
        id=issue_id,
        contract_id="c1",
        node_id=node_id,
        title=title,
        our_position=our_position,
        their_position=their_position,
        recommended_position=recommended_position,
        donna_counter_language=donna_counter_language,
        status=status,
        initiator=initiator,
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        priority=priority,
        created_at=created_at,
    )


def _nodes() -> list[StoredNode]:
    # Two root clauses ("1", "2") and one non-clause recital (no number).
    return [
        StoredNode(id="n1", parent_id=None, order_index=100, content_type="prose", role="clause"),
        StoredNode(id="n2", parent_id=None, order_index=200, content_type="prose", role="clause"),
        StoredNode(id="r1", parent_id=None, order_index=50, content_type="prose", role="recital"),
    ]


def test_filter_excludes_closed() -> None:
    issues = [
        _issue("a", status="open", node_id="n1"),
        _issue("b", status="closed", node_id="n1"),
        _issue("c", status="closed", node_id="n2"),
        _issue("e", status="open"),
    ]
    export = build_export(issues, _nodes())
    titles = [r.issue for r in export.anchored] + [r.issue for r in export.floating]
    assert len(titles) == 2  # open only (DD-65)


def test_priority_desc_nulls_last_then_document_order() -> None:
    issues = [
        _issue("low", title="low", node_id="n1", priority=1),
        _issue("none", title="none", node_id="n1", priority=None),
        _issue("high", title="high", node_id="n2", priority=9),
        # Same priority as "high" but on an earlier node → document-order tie-break.
        _issue("high_early", title="high_early", node_id="n1", priority=9),
    ]
    export = build_export(issues, _nodes())
    # Raw priority drives the sort (desc, ties → document order, NULLs last) but is
    # never printed; the tie-break puts n1 (doc position 1) before n2 (position 2).
    assert [r.issue for r in export.anchored] == ["high_early", "high", "low", "none"]
    # The printed `#` is the 1..n render sequence (DD-61), not the raw priority.
    assert [r.number for r in export.anchored] == ["1", "2", "3", "4"]
    assert export.anchored[0].clause == "1"
    assert export.anchored[1].clause == "2"


def test_null_priority_row_still_gets_sequence_position() -> None:
    # A null-priority issue sorts last but is still numbered in render order.
    issues = [
        _issue("hi", title="hi", node_id="n1", priority=9),
        _issue("null", title="null", node_id="n1", priority=None),
    ]
    export = build_export(issues, _nodes())
    assert [r.issue for r in export.anchored] == ["hi", "null"]
    assert [r.number for r in export.anchored] == ["1", "2"]
    assert EM_DASH not in [r.number for r in export.anchored]


def test_free_floating_continues_single_sequence() -> None:
    issues = [
        _issue("anchored", title="anchored", node_id="n1", priority=2),
        _issue("floating_hi", title="floating_hi", node_id=None, priority=5),
        _issue("floating_lo", title="floating_lo", node_id=None, priority=1),
    ]
    export = build_export(issues, _nodes())
    assert [r.issue for r in export.anchored] == ["anchored"]
    assert [r.number for r in export.anchored] == ["1"]
    # Free-floating is priority-desc within the group AND continues the single
    # 1..n count (2, 3) rather than restarting at 1 (DD-61).
    assert [r.issue for r in export.floating] == ["floating_hi", "floating_lo"]
    assert [r.number for r in export.floating] == ["2", "3"]
    assert all(r.clause == EM_DASH for r in export.floating)


def test_status_to_label() -> None:
    # Only open issues reach the export (DD-65); each renders the "Open" label.
    issues = [
        _issue("o", status="open", node_id="n1"),
        _issue("o2", status="open", node_id="n2"),
    ]
    rows = build_export(issues, _nodes()).anchored
    assert {r.status for r in rows} == {"Open"}
    assert len(rows) == 2


def test_initiator_to_raised_by() -> None:
    mapping = {"operator": "Us", "counterparty": "Them", "donna": "Us"}
    for initiator, expected in mapping.items():
        export = build_export([_issue("x", initiator=initiator, node_id="n1")], _nodes())
        assert export.anchored[0].raised_by == expected


def test_proposed_resolution_fallback_chain() -> None:
    recommended = _issue("a", node_id="n1", recommended_position="Use the standard cap")
    counter = _issue("b", node_id="n1", donna_counter_language="Propose a mutual cap")
    neither = _issue("c", node_id="n1")
    assert build_export([recommended], _nodes()).anchored[0].proposed_resolution == (
        "Use the standard cap"
    )
    assert build_export([counter], _nodes()).anchored[0].proposed_resolution == (
        "Propose a mutual cap"
    )
    assert build_export([neither], _nodes()).anchored[0].proposed_resolution == EM_DASH


def test_clause_number_dash_for_nonclause_and_floating() -> None:
    on_recital = _issue("a", node_id="r1")  # anchored to a non-clause node
    floating = _issue("b", node_id=None)
    assert build_export([on_recital], _nodes()).anchored[0].clause == EM_DASH
    assert build_export([floating], _nodes()).floating[0].clause == EM_DASH


def test_positions_fallback_for_unknown_anchored_node() -> None:
    # node_id present but not in the tree (e.g. deleted) → clause "—", sorts last.
    issues = [
        _issue("ghost", node_id="gone", priority=9),
        _issue("real", node_id="n1", priority=9),
    ]
    export = build_export(issues, _nodes())
    assert export.anchored[0].issue == "Generic point"
    assert export.anchored[0].clause == "1"
    assert export.anchored[1].clause == EM_DASH


def test_render_empty_is_valid_docx() -> None:
    data = render_issue_list_docx("Generic Agreement", build_export([], _nodes()))
    assert data.startswith(_DOCX_MAGIC)


def test_render_populated_is_valid_docx() -> None:
    issues = [
        _issue("a", node_id="n1", priority=3, our_position="Hold", their_position="Push"),
        _issue("b", node_id=None, priority=1),
    ]
    data = render_issue_list_docx("Generic Agreement", build_export(issues, _nodes()))
    assert data.startswith(_DOCX_MAGIC)
