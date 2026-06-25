"""Clause-delete route: response shape, status codes, the per-node soft-delete +
deletion version side effects, and the single audit event.

The DB is faked (a connection that returns canned subtree rows for the recursive
fetch and records its execute() calls); record_event is stubbed to capture the
audit event. TestClient is used without its context manager so the app lifespan
never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import nodes as nodes_api
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_delete
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(nodes_api.router)
client = TestClient(app)

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


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
    monkeypatch.setattr(node_delete, "record_event", _record)


def test_delete_returns_deleted_ids_for_subtree(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(
        [
            _subtree_row(id="p", body="Parent."),
            _subtree_row(id="c1n", parent_id="p", body="Child."),
        ]
    )
    _install(monkeypatch, conn, captured)

    resp = client.delete("/contracts/c1/nodes/p")

    assert resp.status_code == 200
    assert resp.json() == {"deleted_ids": ["p", "c1n"]}
    # target + child both soft-deleted.
    delete_targets = [args[0] for sql, args in conn.executes if "is_deleted = true" in sql]
    assert delete_targets == ["p", "c1n"]


def test_delete_records_single_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn([_subtree_row(id="p"), _subtree_row(id="c1n", parent_id="p")])
    _install(monkeypatch, conn, captured)

    resp = client.delete("/contracts/c1/nodes/p")

    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "node_deleted"
    assert event.entity_type == "node"
    assert event.entity_id == "p"
    assert event.actor == "operator"  # audit actor = settings.operator_actor (matches issues)
    assert event.payload == {"deleted_ids": ["p", "c1n"], "count": 2}


def test_delete_unknown_node_returns_404(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn([])  # nothing matched the scoped CTE base
    _install(monkeypatch, conn, captured)

    resp = client.delete("/contracts/c1/nodes/missing")

    assert resp.status_code == 404
    assert conn.executes == []
    assert "event" not in captured


def test_delete_already_deleted_node_returns_404(monkeypatch: Any) -> None:
    # an already soft-deleted node is excluded by the CTE's is_deleted filter → empty.
    captured: dict[str, Any] = {}
    conn = _FakeConn([])
    _install(monkeypatch, conn, captured)

    resp = client.delete("/contracts/c1/nodes/n1")

    assert resp.status_code == 404
