"""F03b unit coverage: the two ClauseNode adapters, deterministic difflib hunk
extraction, and parse-path (tracked vs clean) branching. Pure logic — no DB, no
real .docx (synthetic trees + fake conn)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from backend.models.contract_tree import ParsedTree, TreeNode
from backend.models.revision_import import RevisionImportRequest
from backend.models.snapshots import SnapshotNode
from backend.services.import_ import revision_import as svc


def _tn(index: int, parent: int | None, order: int, text: str, kind: str = "prose") -> TreeNode:
    return TreeNode(
        index=index,
        parent_index=parent,
        depth=0 if parent is None else 1,
        order_index=order,
        kind=kind,  # type: ignore[arg-type]
        text=text,
    )


def _sn(
    node_id: str, parent: str | None, order: int, body: str, deleted: bool = False
) -> SnapshotNode:
    return SnapshotNode(
        id=node_id,
        parent_id=parent,
        order_index=order,
        content_type="prose",
        heading=None,
        body=body,
        is_deleted=deleted,
    )


# --- adapters ---------------------------------------------------------------


def test_incoming_adapter_keys_on_index_and_parent_index() -> None:
    tree = ParsedTree(
        nodes=[
            _tn(0, None, 100, "Article one"),
            _tn(1, 0, 100, "child clause body"),
        ]
    )
    nodes = svc.incoming_to_clause_nodes(tree)

    assert [n.order for n in nodes] == [0, 1]
    assert nodes[0].id is None and nodes[0].parent is None
    assert nodes[1].parent == 0  # parent's order/index
    assert nodes[0].body == "Article one" and nodes[0].heading == ""


def test_incoming_adapter_flattens_table_rows() -> None:
    table = TreeNode(
        index=0,
        parent_index=None,
        depth=0,
        order_index=100,
        kind="table",
        text="",
        rows=[["A", "B"], ["c", "d"]],
    )
    nodes = svc.incoming_to_clause_nodes(ParsedTree(nodes=[table]))
    assert nodes[0].body == "A B c d"


def test_baseline_adapter_dfs_order_and_drops_deleted() -> None:
    tree = [
        _sn("b0", None, 100, "root one"),
        _sn("b1", "b0", 100, "first child"),
        _sn("b2", "b0", 200, "second child"),
        _sn("bX", None, 300, "gone", deleted=True),
    ]
    nodes = svc.baseline_to_clause_nodes(tree)

    by_id = {n.id: n for n in nodes}
    assert "bX" not in by_id  # soft-deleted dropped
    # pre-order DFS: root(0) -> first child(1) -> second child(2)
    assert by_id["b0"].order == 0
    assert by_id["b1"].order == 1
    assert by_id["b2"].order == 2
    assert by_id["b1"].parent == "b0"


def test_baseline_adapter_prefers_heading_text() -> None:
    sn = SnapshotNode(
        id="b0",
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading="Confidentiality",
        body="ignored when heading present",
        is_deleted=False,
    )
    nodes = svc.baseline_to_clause_nodes([sn])
    assert nodes[0].body == "Confidentiality"


# --- difflib hunk extraction ------------------------------------------------


def test_extract_hunks_replacement() -> None:
    hunks = svc.extract_hunks("the price is 10 dollars", "the price is 20 dollars")
    assert len(hunks) == 1
    h = hunks[0]
    assert h.hunk_type == "replacement"
    assert h.original_text == "10" and h.proposed_text == "20"
    assert h.significance == "substantive"
    # offset points at the "10" token in the baseline
    assert "the price is ".__len__() == h.position_in_body


def test_extract_hunks_pure_insertion() -> None:
    hunks = svc.extract_hunks("alpha beta", "alpha gamma beta")
    assert len(hunks) == 1
    assert hunks[0].hunk_type == "insertion"
    assert hunks[0].original_text is None
    assert hunks[0].proposed_text == "gamma"


def test_extract_hunks_pure_deletion() -> None:
    hunks = svc.extract_hunks("alpha gamma beta", "alpha beta")
    assert len(hunks) == 1
    assert hunks[0].hunk_type == "deletion"
    assert hunks[0].original_text == "gamma"
    assert hunks[0].proposed_text is None


def test_extract_hunks_identical_is_empty() -> None:
    assert svc.extract_hunks("same text", "same text") == []


# --- parse-path detection (tracked vs clean) --------------------------------


class _GuardConn:
    """Minimal fake: an open `reviewing` session already exists, so a CLEAN doc
    advances past tracked-change detection and hits the single-session 409."""

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, sql: str, *_args: Any) -> Any:
        if "status = 'reviewing'" in sql:
            return 1
        return None


@pytest.mark.asyncio
async def test_tracked_changes_doc_is_rejected_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "count_tracked_changes", lambda _p: (3, 1))
    with pytest.raises(svc.TrackedChangesNotSupported):
        await svc.import_revision(
            _GuardConn(), "c1", "/tmp/x.docx", RevisionImportRequest(source="counterparty")
        )


@pytest.mark.asyncio
async def test_clean_doc_passes_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    # (0, 0) = clean → detection passes → reaches the open-session guard (409), proving
    # the clean branch was taken (not the 422 tracked branch).
    monkeypatch.setattr(svc, "count_tracked_changes", lambda _p: (0, 0))
    with pytest.raises(svc.SessionAlreadyOpen):
        await svc.import_revision(
            _GuardConn(), "c1", "/tmp/x.docx", RevisionImportRequest(source="counterparty")
        )
