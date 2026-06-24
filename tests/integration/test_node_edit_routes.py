"""F08 direct-edit route: request parsing, response shape, status codes, and the
version + audit side effects.

The DB is faked (a connection that returns canned node records and records its
execute() calls); record_event is stubbed to capture the audit event. TestClient
is used without its context manager so the app lifespan never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import nodes as nodes_api
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_edit
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
        body="Original body.",
        table_data=None,
        plain_text="Original body.",
        role="clause",
        has_placeholder=False,
    )
    base.update(kw)
    return base


class _FakeConn:
    def __init__(self, load: dict[str, Any] | None, updated: dict[str, Any] | None = None) -> None:
        self._records = [load, updated if updated is not None else load]
        self._fetches = 0
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        record = self._records[min(self._fetches, len(self._records) - 1)]
        self._fetches += 1
        return record

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
    monkeypatch.setattr(node_edit, "record_event", _record)


def test_patch_persists_new_text_and_writes_version(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(_node_record(), _node_record(body="New body.", plain_text="New body."))
    _install(monkeypatch, conn, captured)

    resp = client.patch("/contracts/c1/nodes/n1", json={"text": "New body."})

    assert resp.status_code == 200
    assert resp.json()["body"] == "New body."  # persists on re-fetch
    version_sql, version_args = conn.executes[1]
    assert "INSERT INTO node_versions" in version_sql
    assert version_args == ("n1", "Original body.", "New body.", "user")  # before/after/actor


def test_patch_records_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(_node_record(), _node_record(body="New body."))
    _install(monkeypatch, conn, captured)

    resp = client.patch("/contracts/c1/nodes/n1", json={"text": "New body."})

    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "node_edited"
    assert event.entity_type == "node"
    assert event.entity_id == "n1"
    assert event.actor == "operator"  # audit actor = settings.operator_actor (matches issues)


def test_noop_edit_writes_no_version_or_audit(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(_node_record(body="Same."))
    _install(monkeypatch, conn, captured)

    resp = client.patch("/contracts/c1/nodes/n1", json={"text": "Same."})

    assert resp.status_code == 200
    assert conn.executes == []  # no node update, no version row
    assert "event" not in captured  # no audit


def test_patch_unknown_node_returns_404(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(None)
    _install(monkeypatch, conn, captured)

    resp = client.patch("/contracts/c1/nodes/missing", json={"text": "x"})
    assert resp.status_code == 404


def test_patch_table_node_returns_422(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    conn = _FakeConn(_node_record(content_type="table", body=None, table_data=[["a"]]))
    _install(monkeypatch, conn, captured)

    resp = client.patch("/contracts/c1/nodes/n1", json={"text": "x"})
    assert resp.status_code == 422


def test_patch_rejects_missing_text() -> None:
    resp = client.patch("/contracts/c1/nodes/n1", json={})
    assert resp.status_code == 422
