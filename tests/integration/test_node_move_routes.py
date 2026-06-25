"""Move route (general reposition): request parsing, response shape, status codes,
the parent/order UPDATEs, cycle rejection, and the single audit event (no
node_versions row).

The DB is faked by an in-memory node tree: fetchrow resolves a node by id, fetch
serves either the recursive descendant set or the child siblings of a parent, and
the parent/order UPDATEs mutate the tree so a re-read reflects the new structure.
record_event is stubbed to capture the audit event. TestClient is used without its
context manager so the app lifespan never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import nodes as nodes_api
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_move
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(nodes_api.router)
client = TestClient(app)

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _norm(value: Any) -> str | None:
    return str(value) if value is not None else None


class _FakeConn:
    """In-memory node tree: {id: {parent_id, order_index}}. The parent/order UPDATEs
    mutate it so re-reading reflects the reposition."""

    def __init__(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.nodes = {k: dict(v) for k, v in nodes.items()}
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any] | None:
        node = self.nodes.get(args[0])
        if node is None:
            return None
        return {"id": args[0], "parent_id": node["parent_id"], "order_index": node["order_index"]}

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "RECURSIVE" in sql:
            return self._descendants(args[0])
        parent_id = args[1]
        children = [
            {"id": nid, "order_index": n["order_index"]}
            for nid, n in self.nodes.items()
            if _norm(n["parent_id"]) == _norm(parent_id)
        ]
        return sorted(children, key=lambda r: r["order_index"])

    def _descendants(self, node_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for nid, n in self.nodes.items():
                if _norm(n["parent_id"]) == _norm(cur):
                    out.append({"id": nid})
                    stack.append(nid)
        return out

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        stripped = sql.strip()
        if stripped.startswith("UPDATE nodes SET parent_id"):
            self.nodes[args[0]]["parent_id"] = args[1]
            self.nodes[args[0]]["order_index"] = args[2]
        elif stripped.startswith("UPDATE nodes SET order_index"):
            self.nodes[args[0]]["order_index"] = args[1]
        return "UPDATE 1"


def _install(monkeypatch: Any, conn: _FakeConn, captured: dict[str, Any]) -> None:
    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    async def _record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return StoredAuditEvent(
            id="a1",
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            actor=event.actor,
            payload=event.payload,
            created_at=_NOW,
        )

    monkeypatch.setattr(nodes_api, "acquire", _fake_acquire)
    monkeypatch.setattr(node_move, "record_event", _record)


def test_reorder_after_anchor(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "a": {"parent_id": None, "order_index": 100},
            "b": {"parent_id": None, "order_index": 200},
            "c": {"parent_id": None, "order_index": 300},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/c/move", json={"after_node_id": "a"})

    assert resp.status_code == 200
    assert resp.json() == {"moved": True, "node_id": "c", "parent_id": None}
    assert conn.nodes["c"]["order_index"] == 150  # between a (100) and b (200)
    assert conn.nodes["c"]["parent_id"] is None


def test_reorder_before_anchor(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "a": {"parent_id": None, "order_index": 100},
            "b": {"parent_id": None, "order_index": 200},
            "c": {"parent_id": None, "order_index": 300},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/a/move", json={"before_node_id": "c"})

    assert resp.status_code == 200
    assert conn.nodes["a"]["order_index"] == 250  # between b (200) and c (300)


def test_reparent_to_different_parent(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "p1": {"parent_id": None, "order_index": 100},
            "p2": {"parent_id": None, "order_index": 200},
            "x": {"parent_id": "p1", "order_index": 100},
            "y": {"parent_id": "p2", "order_index": 100},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/x/move", json={"parent_id": "p2"})

    assert resp.status_code == 200
    assert resp.json() == {"moved": True, "node_id": "x", "parent_id": "p2"}
    assert conn.nodes["x"]["parent_id"] == "p2"
    assert conn.nodes["x"]["order_index"] == 200  # appended past y (100)


def test_subtree_follows_on_reparent(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "p1": {"parent_id": None, "order_index": 100},
            "p2": {"parent_id": None, "order_index": 200},
            "x": {"parent_id": "p1", "order_index": 100},
            "x_child": {"parent_id": "x", "order_index": 100},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/x/move", json={"parent_id": "p2"})

    assert resp.status_code == 200
    # only x is re-pointed; its child still references x, so the sub-tree rides along.
    assert conn.nodes["x"]["parent_id"] == "p2"
    assert conn.nodes["x_child"]["parent_id"] == "x"


def test_append_no_anchor(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "a": {"parent_id": None, "order_index": 100},
            "b": {"parent_id": None, "order_index": 200},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/a/move", json={})

    assert resp.status_code == 200
    assert conn.nodes["a"]["order_index"] == 300  # appended past b (200)


def test_move_into_own_child_returns_422(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "p": {"parent_id": None, "order_index": 100},
            "c": {"parent_id": "p", "order_index": 100},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/p/move", json={"parent_id": "c"})

    assert resp.status_code == 422
    assert "event" not in captured
    assert conn.nodes["p"]["parent_id"] is None  # unchanged


def test_both_anchors_returns_422(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn({"a": {"parent_id": None, "order_index": 100}})
    _install(monkeypatch, conn, captured)

    resp = client.post(
        "/contracts/c1/nodes/a/move", json={"after_node_id": "b", "before_node_id": "c"}
    )

    assert resp.status_code == 422
    assert "event" not in captured


def test_unknown_parent_returns_404(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn({"a": {"parent_id": None, "order_index": 100}})
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/a/move", json={"parent_id": "missing"})

    assert resp.status_code == 404


def test_unknown_anchor_returns_404(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn({"a": {"parent_id": None, "order_index": 100}})
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/a/move", json={"after_node_id": "missing"})

    assert resp.status_code == 404


def test_noop_returns_moved_false_and_no_audit(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "a": {"parent_id": None, "order_index": 100},
            "b": {"parent_id": None, "order_index": 200},
        }
    )
    _install(monkeypatch, conn, captured)

    # b already sits immediately after a → no write, no audit.
    resp = client.post("/contracts/c1/nodes/b/move", json={"after_node_id": "a"})

    assert resp.status_code == 200
    assert resp.json() == {"moved": False, "node_id": "b", "parent_id": None}
    assert conn.executes == []
    assert "event" not in captured


def test_records_node_moved_audit_and_no_version(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        {
            "a": {"parent_id": None, "order_index": 100},
            "b": {"parent_id": None, "order_index": 200},
            "c": {"parent_id": None, "order_index": 300},
        }
    )
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/c/move", json={"after_node_id": "a"})

    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "node_moved"
    assert event.entity_type == "node"
    assert event.entity_id == "c"
    assert event.actor == "operator"  # audit actor = settings.operator_actor (matches issues)
    assert event.payload == {"parent_id": None, "anchor": {"after": "a", "before": None}}
    # move is structure-only: never a node_versions write.
    assert not any("node_versions" in sql for sql, _ in conn.executes)


def test_unknown_node_returns_404(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn({"a": {"parent_id": None, "order_index": 100}})
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes/missing/move", json={})

    assert resp.status_code == 404
    assert "event" not in captured
