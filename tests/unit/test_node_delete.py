"""Soft-delete service (clause delete): subtree collection, per-node soft-delete +
deletion version row, single audit event, and the not-found guard.

No live DB: a fake connection returns canned subtree rows for the recursive-CTE
fetch and records its execute() calls, so the per-node UPDATE/version args and the
deleted-id set are exercised without Postgres. record_event is stubbed to capture
the single audit event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_delete


def _subtree_row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(id="n1", parent_id=None, body="Clause body.", heading=None)
    base.update(kw)
    return base


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return self._rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "UPDATE 1"


def _capture_record(captured: dict[str, Any]) -> Any:
    async def _record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return StoredAuditEvent(
            id="a1",
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            actor=event.actor,
            payload=event.payload,
            created_at=datetime(2026, 6, 24, tzinfo=UTC),
        )

    return _record


async def test_delete_leaf_sets_deleted_and_writes_deletion_version(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_delete, "record_event", _capture_record(captured))
    conn = _FakeConn([_subtree_row()])

    deleted = await node_delete.delete_node(conn, "c1", "n1")

    assert deleted == ["n1"]
    delete_sql, delete_args = conn.executes[0]
    assert "is_deleted = true" in delete_sql and "deleted_at = now()" in delete_sql
    assert delete_args == ("n1",)
    version_sql, version_args = conn.executes[1]
    assert "INSERT INTO node_versions" in version_sql
    assert version_args == ("n1", "Clause body.", "user")  # body_before=text, after=NULL, actor

    event = captured["event"]
    assert event.event_type == "node_deleted"
    assert event.entity_type == "node"
    assert event.entity_id == "n1"
    assert event.actor == "operator"  # audit actor = settings.operator_actor (matches issues)
    assert event.payload == {"deleted_ids": ["n1"], "count": 1}


async def test_delete_uses_heading_when_body_null(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_delete, "record_event", _capture_record(captured))
    conn = _FakeConn([_subtree_row(body=None, heading="Confidentiality")])

    await node_delete.delete_node(conn, "c1", "n1")

    _, version_args = conn.executes[1]
    assert version_args == ("n1", "Confidentiality", "user")


async def test_delete_parent_soft_deletes_whole_subtree(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_delete, "record_event", _capture_record(captured))
    conn = _FakeConn(
        [
            _subtree_row(id="p", body="Parent."),
            _subtree_row(id="c1n", parent_id="p", body="Child one."),
            _subtree_row(id="c2n", parent_id="p", body="Child two."),
        ]
    )

    deleted = await node_delete.delete_node(conn, "c1", "p")

    assert deleted == ["p", "c1n", "c2n"]
    # one soft-delete + one version row per node, interleaved.
    delete_targets = [args[0] for sql, args in conn.executes if "is_deleted = true" in sql]
    assert delete_targets == ["p", "c1n", "c2n"]
    version_rows = [args for sql, args in conn.executes if sql.strip().startswith("INSERT")]
    assert version_rows == [
        ("p", "Parent.", "user"),
        ("c1n", "Child one.", "user"),
        ("c2n", "Child two.", "user"),
    ]
    # exactly one audit event for the whole operation.
    assert captured["event"].payload == {"deleted_ids": ["p", "c1n", "c2n"], "count": 3}


async def test_unknown_or_already_deleted_node_raises_not_found(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_delete, "record_event", _capture_record(captured))
    conn = _FakeConn([])  # scoped CTE base returns nothing → missing/deleted/foreign

    with pytest.raises(node_delete.NodeNotFound):
        await node_delete.delete_node(conn, "c1", "missing")
    assert conn.executes == []
    assert "event" not in captured
