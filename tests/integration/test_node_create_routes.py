"""F08b create-node route: request parsing, response shape, status codes, and the
version + audit side effects.

The DB is faked (a connection dispatching the scoped node fetch by id and a canned
sibling set, recording its execute()/fetchval() calls); record_event is stubbed to
capture the audit event. TestClient is used without its context manager so the app
lifespan never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import nodes as nodes_api
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_create
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(nodes_api.router)
client = TestClient(app)

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


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
    monkeypatch.setattr(node_create, "record_event", _record)


def test_post_creates_node_with_parent_and_order_index(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    parent = _node_record(id="p1")
    after = _node_record(id="a", order_index=100, parent_id="p1")
    conn = _FakeConn(
        nodes={"p1": parent, "a": after},
        siblings=[{"id": "a", "order_index": 100}, {"id": "b", "order_index": 200}],
    )
    _install(monkeypatch, conn, captured)

    resp = client.post(
        "/contracts/c1/nodes",
        json={"parent_id": "p1", "after_node_id": "a", "text": "New clause."},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "new1"
    assert body["parent_id"] == "p1"
    assert body["order_index"] == 150  # midpoint(100, 200)


def test_post_writes_insertion_version_row(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(siblings=[])
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes", json={"parent_id": None, "text": "Fresh."})

    assert resp.status_code == 201
    version = next((sql, args) for sql, args in conn.executes if "INSERT INTO node_versions" in sql)
    _, version_args = version
    assert version_args == ("new1", "Fresh.", "user")  # body_after, actor; body_before is NULL


def test_post_records_node_created_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(siblings=[])
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes", json={"text": "Fresh.", "role": "clause"})

    assert resp.status_code == 201
    event = captured["event"]
    assert event.event_type == "node_created"
    assert event.entity_type == "node"
    assert event.entity_id == "new1"
    assert event.actor == "operator"


def test_post_unknown_parent_returns_404(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(nodes={}, siblings=[])
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes", json={"parent_id": "missing", "text": "x"})
    assert resp.status_code == 404


def test_post_invalid_role_returns_422(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(siblings=[])
    _install(monkeypatch, conn, captured)

    resp = client.post("/contracts/c1/nodes", json={"text": "x", "role": "bogus"})
    assert resp.status_code == 422


def test_post_rejects_missing_text() -> None:
    resp = client.post("/contracts/c1/nodes", json={"parent_id": None})
    assert resp.status_code == 422
