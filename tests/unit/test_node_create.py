"""Create-node service (F08b): order_index computation (append max+gap, insert
midpoint, OQ-07 no-gap re-space), role validation, and anchor lookup.

No live DB: a fake connection dispatches the scoped node fetch by id, returns a
canned sibling set, and records execute()/fetchval() calls so the order_index
math, the re-space UPDATEs, and the version/audit INSERT args are exercised
without Postgres. record_event is stubbed to capture the audit event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_create


def _node_record(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="n1",
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body="Body.",
        table_data=None,
        plain_text="Body.",
        role="clause",
        has_placeholder=False,
    )
    base.update(kw)
    return base


class _FakeConn:
    def __init__(
        self,
        *,
        nodes: dict[str, dict[str, Any]] | None = None,
        siblings: list[dict[str, Any]] | None = None,
        new_id: str = "new1",
    ) -> None:
        self._nodes = dict(nodes or {})
        self._siblings = siblings or []
        self._new_id = new_id
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchvals: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any] | None:
        return self._nodes.get(args[0])

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return self._siblings

    async def fetchval(self, sql: str, *args: Any) -> str:
        self.fetchvals.append((sql, args))
        self._nodes[self._new_id] = _node_record(
            id=self._new_id, order_index=args[2], parent_id=args[1], role=args[3], body=args[4]
        )
        return self._new_id

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


def _insert_args(conn: _FakeConn) -> tuple[Any, ...]:
    return conn.fetchvals[0][1]


async def test_append_computes_max_order_plus_gap(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    conn = _FakeConn(siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}])

    result = await node_create.create_node(conn, "c1", None, None, "New clause.")

    assert result.order_index == 300  # max(200) + gap(100)
    assert _insert_args(conn)[2] == 300


async def test_append_first_child_uses_gap(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    conn = _FakeConn(siblings=[])

    result = await node_create.create_node(conn, "c1", None, None, "First.")

    assert result.order_index == 100  # gap


async def test_insert_after_computes_midpoint(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    after = _node_record(id="a", order_index=100, parent_id=None)
    conn = _FakeConn(
        nodes={"a": after},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}],
    )

    result = await node_create.create_node(conn, "c1", None, "a", "Between.")

    assert result.order_index == 150  # midpoint(100, 200)


async def test_insert_after_last_sibling_appends_gap(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    after = _node_record(id="b", order_index=200, parent_id=None)
    conn = _FakeConn(
        nodes={"b": after},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}],
    )

    result = await node_create.create_node(conn, "c1", None, "b", "Last.")

    assert result.order_index == 300  # no next sibling: after(200) + gap


async def test_no_gap_fallback_respaces_then_inserts(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    after = _node_record(id="a", order_index=100, parent_id=None)
    # adjacent integers — midpoint(100, 101) == 100 == after: no room.
    conn = _FakeConn(
        nodes={"a": after},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 101}],
    )

    result = await node_create.create_node(conn, "c1", None, "a", "Squeeze.")

    # siblings re-spaced to 100, 200 (bump-then-set), new node lands in the gap.
    assert any("order_index = order_index +" in sql for sql, _ in conn.executes)
    set_orders = [
        args
        for sql, args in conn.executes
        if sql.strip().startswith("UPDATE nodes SET order_index = $2")
    ]
    assert ("a", 100) in set_orders and ("b", 200) in set_orders
    assert result.order_index == 150  # after_new(100) + gap//2


async def test_prepend_before_first_child_uses_half_slot(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    before = _node_record(id="a", order_index=100, parent_id=None)
    conn = _FakeConn(
        nodes={"a": before},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}],
    )

    result = await node_create.create_node(conn, "c1", None, None, "Top.", before_node_id="a")

    assert result.order_index == 50  # no prev: before(100) // 2
    assert _insert_args(conn)[2] == 50


async def test_insert_before_middle_child_computes_midpoint(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    before = _node_record(id="b", order_index=200, parent_id=None)
    conn = _FakeConn(
        nodes={"b": before},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}],
    )

    result = await node_create.create_node(conn, "c1", None, None, "Between.", before_node_id="b")

    assert result.order_index == 150  # midpoint(prev 100, before 200)


async def test_before_no_gap_fallback_respaces_then_inserts(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    before = _node_record(id="b", order_index=101, parent_id=None)
    # adjacent integers — midpoint(100, 101) == 100 == prev: no room below before.
    conn = _FakeConn(
        nodes={"b": before},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 101}],
    )

    result = await node_create.create_node(conn, "c1", None, None, "Squeeze.", before_node_id="b")

    assert any("order_index = order_index +" in sql for sql, _ in conn.executes)
    set_orders = [
        args
        for sql, args in conn.executes
        if sql.strip().startswith("UPDATE nodes SET order_index = $2")
    ]
    assert ("a", 100) in set_orders and ("b", 200) in set_orders
    assert result.order_index == 150  # just below re-spaced before(200): 200 - gap//2


async def test_before_first_child_no_room_respaces(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    before = _node_record(id="a", order_index=1, parent_id=None)
    # before is first child at order_index 1: 1 // 2 == 0, no room — re-space.
    conn = _FakeConn(nodes={"a": before}, siblings=[{"id": "a", "order_index": 1}])

    result = await node_create.create_node(conn, "c1", None, None, "Top.", before_node_id="a")

    set_orders = [
        args
        for sql, args in conn.executes
        if sql.strip().startswith("UPDATE nodes SET order_index = $2")
    ]
    assert ("a", 100) in set_orders
    assert result.order_index == 50  # below re-spaced before(100): 100 - gap//2


async def test_both_anchors_rejected() -> None:
    after = _node_record(id="a", order_index=100, parent_id=None)
    before = _node_record(id="b", order_index=200, parent_id=None)
    conn = _FakeConn(nodes={"a": after, "b": before}, siblings=[])
    with pytest.raises(node_create.ConflictingAnchors):
        await node_create.create_node(conn, "c1", None, "a", "x", before_node_id="b")
    assert conn.fetchvals == []


async def test_before_node_not_found_rejected() -> None:
    conn = _FakeConn(nodes={}, siblings=[])
    with pytest.raises(node_create.BeforeNodeNotFound):
        await node_create.create_node(conn, "c1", None, None, "x", before_node_id="missing")
    assert conn.fetchvals == []


async def test_before_node_wrong_parent_rejected() -> None:
    before = _node_record(id="a", order_index=100, parent_id="otherparent")
    conn = _FakeConn(nodes={"a": before}, siblings=[])
    with pytest.raises(node_create.BadPlacement):
        await node_create.create_node(conn, "c1", None, None, "x", before_node_id="a")
    assert conn.fetchvals == []


async def test_invalid_role_rejected() -> None:
    conn = _FakeConn(siblings=[])
    with pytest.raises(node_create.InvalidRole):
        await node_create.create_node(conn, "c1", None, None, "x", role="bogus")
    assert conn.fetchvals == []


async def test_parent_not_found_rejected() -> None:
    conn = _FakeConn(nodes={}, siblings=[])
    with pytest.raises(node_create.ParentNotFound):
        await node_create.create_node(conn, "c1", "missing", None, "x")
    assert conn.fetchvals == []


async def test_after_node_not_found_rejected() -> None:
    conn = _FakeConn(nodes={}, siblings=[])
    with pytest.raises(node_create.AfterNodeNotFound):
        await node_create.create_node(conn, "c1", None, "missing", "x")
    assert conn.fetchvals == []


async def test_after_node_wrong_parent_rejected() -> None:
    after = _node_record(id="a", order_index=100, parent_id="otherparent")
    conn = _FakeConn(nodes={"a": after}, siblings=[])
    with pytest.raises(node_create.BadPlacement):
        await node_create.create_node(conn, "c1", None, "a", "x")
    assert conn.fetchvals == []


async def test_version_and_audit_written(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_create, "record_event", _capture_record(captured))
    conn = _FakeConn(siblings=[])

    await node_create.create_node(conn, "c1", None, None, "New clause.", role="clause")

    version = next((sql, args) for sql, args in conn.executes if "INSERT INTO node_versions" in sql)
    _, version_args = version
    assert version_args == ("new1", "New clause.", "user")  # body_after, actor (body_before NULL)
    event = captured["event"]
    assert event.event_type == "node_created"
    assert event.entity_type == "node"
    assert event.entity_id == "new1"
    assert event.actor == "operator"
    assert event.payload == {"parent_id": None, "role": "clause"}
