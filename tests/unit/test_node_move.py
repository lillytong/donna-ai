"""Move service (general reposition): reorder under the same parent (after/before),
reparent to a different parent, append, the cycle-safety guard, anchor/parent
validation, the no-op short-circuit, and the single audit event (no node_versions).

No live DB: a fake connection dispatches the scoped node fetch by id, the recursive
descendant fetch and the sibling fetch by SQL shape, and records execute() calls so
the order/parent UPDATEs are observable without Postgres. record_event is stubbed to
capture the audit event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_move


class _FakeConn:
    def __init__(
        self,
        *,
        nodes: dict[str, dict[str, Any]] | None = None,
        siblings: list[dict[str, Any]] | None = None,
        descendants: list[dict[str, Any]] | None = None,
    ) -> None:
        self._nodes = dict(nodes or {})
        self._siblings = siblings or []
        self._descendants = descendants or []
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any] | None:
        return self._nodes.get(args[0])

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        if "RECURSIVE" in sql:
            return self._descendants
        return self._siblings

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


def _parent_orders(conn: _FakeConn) -> list[tuple[Any, ...]]:
    prefix = "UPDATE nodes SET parent_id"
    return [args for sql, args in conn.executes if sql.strip().startswith(prefix)]


def _temp_vacates(conn: _FakeConn) -> list[tuple[Any, ...]]:
    prefix = "UPDATE nodes SET order_index"
    return [args for sql, args in conn.executes if sql.strip().startswith(prefix)]


async def test_reorder_same_parent_after_anchor(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "c": {"id": "c", "parent_id": None, "order_index": 300},
            "a": {"id": "a", "parent_id": None, "order_index": 100},
        },
        siblings=[
            {"id": "a", "order_index": 100},
            {"id": "b", "order_index": 200},
            {"id": "c", "order_index": 300},
        ],
    )

    result = await node_move.move_node(conn, "c1", "c", None, "a", None)

    assert result.moved is True
    assert result.parent_id is None
    # vacate c, then drop it between a (100) and b (200) → midpoint 150, parent unchanged.
    assert _temp_vacates(conn)[0] == ("c", node_move._TEMP_ORDER_INDEX)
    assert _parent_orders(conn) == [("c", None, 150)]


async def test_reorder_same_parent_before_anchor(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "a": {"id": "a", "parent_id": None, "order_index": 100},
            "c": {"id": "c", "parent_id": None, "order_index": 300},
        },
        siblings=[
            {"id": "a", "order_index": 100},
            {"id": "b", "order_index": 200},
            {"id": "c", "order_index": 300},
        ],
    )

    result = await node_move.move_node(conn, "c1", "a", None, None, "c")

    assert result.moved is True
    # excluding a, before c (300) sits above b (200) → midpoint 250.
    assert _parent_orders(conn) == [("a", None, 250)]


async def test_reparent_to_different_parent(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "x": {"id": "x", "parent_id": "p1", "order_index": 100},
            "p2": {"id": "p2", "parent_id": None, "order_index": 200},
        },
        siblings=[{"id": "y", "order_index": 100}],  # existing children of p2
        descendants=[],  # x has no descendants
    )

    result = await node_move.move_node(conn, "c1", "x", "p2", None, None)

    assert result.moved is True
    assert result.parent_id == "p2"
    # appended under p2 past its last child (100) → 200; parent_id flips to p2.
    assert _parent_orders(conn) == [("x", "p2", 200)]
    assert captured["event"].payload["parent_id"] == "p2"


async def test_append_no_anchor_same_parent(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={"a": {"id": "a", "parent_id": None, "order_index": 100}},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}],
    )

    result = await node_move.move_node(conn, "c1", "a", None, None, None)

    assert result.moved is True
    # excluding a, append past b (200) → 300.
    assert _parent_orders(conn) == [("a", None, 300)]


async def test_move_into_own_descendant_is_rejected(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "p": {"id": "p", "parent_id": None, "order_index": 100},
            "c": {"id": "c", "parent_id": "p", "order_index": 100},
        },
        descendants=[{"id": "c"}],  # c is a descendant of p
    )

    with pytest.raises(node_move.InvalidMove):
        await node_move.move_node(conn, "c1", "p", "c", None, None)
    assert conn.executes == []
    assert "event" not in captured


async def test_move_into_self_is_rejected(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(nodes={"p": {"id": "p", "parent_id": None, "order_index": 100}})

    with pytest.raises(node_move.InvalidMove):
        await node_move.move_node(conn, "c1", "p", "p", None, None)
    assert conn.executes == []
    assert "event" not in captured


async def test_both_anchors_rejected(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(nodes={"a": {"id": "a", "parent_id": None, "order_index": 100}})

    with pytest.raises(node_move.ConflictingAnchors):
        await node_move.move_node(conn, "c1", "a", None, "b", "c")
    assert conn.executes == []


async def test_unknown_parent_raises_parent_not_found(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(nodes={"a": {"id": "a", "parent_id": None, "order_index": 100}})

    with pytest.raises(node_move.ParentNotFound):
        await node_move.move_node(conn, "c1", "a", "missing", None, None)


async def test_unknown_after_anchor_raises_not_found(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={"a": {"id": "a", "parent_id": None, "order_index": 100}},
        descendants=[],
    )

    with pytest.raises(node_move.AfterNodeNotFound):
        await node_move.move_node(conn, "c1", "a", None, "missing", None)


async def test_unknown_before_anchor_raises_not_found(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(nodes={"a": {"id": "a", "parent_id": None, "order_index": 100}})

    with pytest.raises(node_move.BeforeNodeNotFound):
        await node_move.move_node(conn, "c1", "a", None, None, "missing")


async def test_anchor_under_wrong_parent_is_bad_placement(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "x": {"id": "x", "parent_id": None, "order_index": 100},
            "p": {"id": "p", "parent_id": None, "order_index": 200},
            # anchor 'b' is a root child, not a child of requested parent p.
            "b": {"id": "b", "parent_id": None, "order_index": 300},
        },
        descendants=[],
    )

    with pytest.raises(node_move.BadPlacement):
        await node_move.move_node(conn, "c1", "x", "p", "b", None)


async def test_noop_when_already_at_position(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "b": {"id": "b", "parent_id": None, "order_index": 200},
            "a": {"id": "a", "parent_id": None, "order_index": 100},
        },
        siblings=[
            {"id": "a", "order_index": 100},
            {"id": "b", "order_index": 200},
            {"id": "c", "order_index": 300},
        ],
    )

    # b already sits immediately after a → no write, no audit.
    result = await node_move.move_node(conn, "c1", "b", None, "a", None)

    assert result.moved is False
    assert result.node_id == "b"
    assert conn.executes == []
    assert "event" not in captured


async def test_records_single_audit_event_and_no_version(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(
        nodes={
            "c": {"id": "c", "parent_id": None, "order_index": 300},
            "a": {"id": "a", "parent_id": None, "order_index": 100},
        },
        siblings=[
            {"id": "a", "order_index": 100},
            {"id": "b", "order_index": 200},
            {"id": "c", "order_index": 300},
        ],
    )

    await node_move.move_node(conn, "c1", "c", None, "a", None)

    # move is structure-only: no node_versions row is ever written.
    assert not any("node_versions" in sql for sql, _ in conn.executes)
    event = captured["event"]
    assert event.event_type == "node_moved"
    assert event.entity_type == "node"
    assert event.entity_id == "c"
    assert event.actor == "operator"  # audit actor = settings.operator_actor (matches issues)
    assert event.payload == {
        "parent_id": None,
        "anchor": {"after": "a", "before": None},
    }


async def test_unknown_node_raises_not_found(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_move, "record_event", _capture_record(captured))
    conn = _FakeConn(nodes={}, siblings=[])

    with pytest.raises(node_move.NodeNotFound):
        await node_move.move_node(conn, "c1", "missing", None, None, None)
    assert conn.executes == []
    assert "event" not in captured
