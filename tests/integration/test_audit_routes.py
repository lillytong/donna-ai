"""Audit routes (F19): read-only surface, query params, response shape.

The DB and repo boundaries are mocked — no live database. TestClient is used
without its context manager so the app lifespan (pool open/close) never runs.
Also asserts the read-only contract: no POST/PUT/PATCH/DELETE on /audit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import audit as audit_api
from backend.main import app
from backend.models.audit import StoredAuditEvent
from fastapi.testclient import TestClient

# The orchestrator wires audit_api.router into main.py on integration; until then
# register it here so these route tests exercise the real router.
if not any(getattr(r, "path", None) == "/audit" for r in app.routes):
    app.include_router(audit_api.router)

client = TestClient(app)

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def _fake_acquire() -> AsyncIterator[object]:
    yield object()


def _sample() -> StoredAuditEvent:
    return StoredAuditEvent(
        id="event-1",
        event_type="status_changed",
        entity_type="issue",
        entity_id="issue-1",
        actor="operator",
        payload={"from": "open", "to": "resolved"},
        created_at=_NOW,
    )


def test_list_audit_events(monkeypatch: Any) -> None:
    async def fake_list(
        _conn: Any,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[StoredAuditEvent]:
        return [_sample()]

    monkeypatch.setattr(audit_api, "acquire", _fake_acquire)
    monkeypatch.setattr(audit_api.audit_repo, "list_events", fake_list)

    resp = client.get("/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == "event-1"
    assert body[0]["payload"] == {"from": "open", "to": "resolved"}


def test_list_audit_events_passes_filters(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(
        _conn: Any,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[StoredAuditEvent]:
        captured["entity_type"] = entity_type
        captured["entity_id"] = entity_id
        captured["limit"] = limit
        return []

    monkeypatch.setattr(audit_api, "acquire", _fake_acquire)
    monkeypatch.setattr(audit_api.audit_repo, "list_events", fake_list)

    resp = client.get("/audit?entity_type=contract&entity_id=c-1&limit=5")
    assert resp.status_code == 200
    assert captured == {"entity_type": "contract", "entity_id": "c-1", "limit": 5}


def test_list_audit_events_rejects_bad_limit() -> None:
    assert client.get("/audit?limit=0").status_code == 422
    assert client.get("/audit?limit=99999").status_code == 422


def test_audit_is_read_only() -> None:
    # append-only: no public write/mutate routes
    assert client.post("/audit", json={}).status_code == 405
    assert client.put("/audit", json={}).status_code == 405
    assert client.patch("/audit", json={}).status_code == 405
    assert client.delete("/audit").status_code == 405
