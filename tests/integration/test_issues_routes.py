"""Issue + comment routes: request parsing, response shape, status codes.

The DB and repo boundaries are mocked — no live database. TestClient is used
without its context manager so the app lifespan (pool open/close) never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import issues as issues_api
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.models.issues import StoredComment, StoredIssue
from fastapi import FastAPI
from fastapi.testclient import TestClient

# The issues router is registered into backend.main by the orchestrator after this
# lands; to keep these tests self-contained (and free of the app lifespan), mount
# it on a local app.
app = FastAPI()
app.include_router(issues_api.router)
client = TestClient(app)

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def _fake_acquire() -> AsyncIterator[object]:
    yield object()


async def _noop_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
    return StoredAuditEvent(
        id="audit-1",
        event_type=event.event_type,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        actor=event.actor,
        payload=event.payload,
        created_at=_NOW,
    )


def _stored_issue(issue_id: str, **kw: Any) -> StoredIssue:
    base: dict[str, Any] = dict(
        id=issue_id,
        contract_id="c1",
        node_id=None,
        title="Royalty rate",
        status="open",
        initiator="operator",
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        created_at=_NOW,
    )
    base.update(kw)
    return StoredIssue(**base)


def test_create_issue_returns_stored(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_create(_conn: Any, payload: Any) -> str:
        captured["payload"] = payload
        return "issue-1"

    async def fake_get(_conn: Any, issue_id: str) -> StoredIssue:
        return _stored_issue(issue_id)

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", _noop_record)
    monkeypatch.setattr(issues_api.issue_repo, "create_issue", fake_create)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.post(
        "/contracts/c1/issues",
        json={"contract_id": "ignored", "title": "Royalty rate", "their_position": "5%"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "issue-1"
    assert body["status"] == "open"
    assert body["initiator"] == "operator"
    # path contract_id wins over body
    assert captured["payload"].contract_id == "c1"


def test_create_issue_captures_counterparty_initiator(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_create(_conn: Any, payload: Any) -> str:
        captured["payload"] = payload
        return "issue-3"

    async def fake_get(_conn: Any, issue_id: str) -> StoredIssue:
        return _stored_issue(issue_id, initiator="counterparty")

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", _noop_record)
    monkeypatch.setattr(issues_api.issue_repo, "create_issue", fake_create)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.post(
        "/contracts/c1/issues",
        json={"title": "Royalty rate", "initiator": "counterparty"},
    )
    assert resp.status_code == 200
    assert resp.json()["initiator"] == "counterparty"
    assert captured["payload"].initiator == "counterparty"


def test_create_issue_rejects_bad_category() -> None:
    resp = client.post("/contracts/c1/issues", json={"title": "x", "category": "bogus"})
    assert resp.status_code == 422


def test_create_issue_rejects_donna_initiator() -> None:
    resp = client.post("/contracts/c1/issues", json={"title": "x", "initiator": "donna"})
    assert resp.status_code == 422


def test_create_free_floating_issue_has_null_node(monkeypatch: Any) -> None:
    async def fake_create(_conn: Any, payload: Any) -> str:
        assert payload.node_id is None  # F08c free-floating
        return "issue-2"

    async def fake_get(_conn: Any, issue_id: str) -> StoredIssue:
        return _stored_issue(issue_id, node_id=None)

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", _noop_record)
    monkeypatch.setattr(issues_api.issue_repo, "create_issue", fake_create)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.post("/contracts/c1/issues", json={"title": "General concern"})
    assert resp.status_code == 200
    assert resp.json()["node_id"] is None


def test_list_issues_passes_status_filter(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_list(
        _conn: Any, contract_id: str, status: str | None = None
    ) -> list[StoredIssue]:
        captured["contract_id"] = contract_id
        captured["status"] = status
        return [_stored_issue("issue-1", status=status or "open")]

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api.issue_repo, "list_issues", fake_list)

    resp = client.get("/contracts/c1/issues?status=agreed")
    assert resp.status_code == 200
    assert captured["contract_id"] == "c1"
    assert captured["status"] == "agreed"


def test_get_issue_not_found(monkeypatch: Any) -> None:
    async def fake_get(_conn: Any, _issue_id: str) -> None:
        return None

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.get("/issues/missing")
    assert resp.status_code == 404


def test_update_status_returns_resolved_issue(monkeypatch: Any) -> None:
    async def fake_update(_conn: Any, issue_id: str, payload: Any) -> str:
        return issue_id

    async def fake_get(_conn: Any, issue_id: str) -> StoredIssue:
        return _stored_issue(issue_id, status="agreed", resolved_at=_NOW)

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", _noop_record)
    monkeypatch.setattr(issues_api.issue_repo, "update_issue_status", fake_update)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.patch(
        "/issues/issue-1/status",
        json={"status": "agreed", "decision": {"verdict": "accept_theirs"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "agreed"
    assert body["resolved_at"] is not None


def test_update_status_not_found(monkeypatch: Any) -> None:
    async def fake_update(_conn: Any, _issue_id: str, _payload: Any) -> None:
        return None

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api.issue_repo, "update_issue_status", fake_update)

    resp = client.patch("/issues/missing/status", json={"status": "kicked"})
    assert resp.status_code == 404


def test_update_status_rejects_bad_status() -> None:
    resp = client.patch("/issues/issue-1/status", json={"status": "closed"})
    assert resp.status_code == 422


def test_create_comment_overrides_issue_id_from_path(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_add(_conn: Any, payload: Any) -> str:
        captured["payload"] = payload
        return "comment-1"

    async def fake_list(_conn: Any, issue_id: str) -> list[StoredComment]:
        return [
            StoredComment(
                id="comment-1",
                issue_id=issue_id,
                actor="user",
                content="note",
                created_at=_NOW,
            )
        ]

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", _noop_record)
    monkeypatch.setattr(issues_api.issue_repo, "add_comment", fake_add)
    monkeypatch.setattr(issues_api.issue_repo, "list_comments", fake_list)

    resp = client.post(
        "/issues/issue-1/comments",
        json={"issue_id": "ignored", "actor": "user", "content": "note"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "comment-1"
    assert captured["payload"].issue_id == "issue-1"


def test_create_issue_records_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_create(_conn: Any, payload: Any) -> str:
        return "issue-9"

    async def fake_get(_conn: Any, issue_id: str) -> StoredIssue:
        return _stored_issue(issue_id)

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return await _noop_record(_conn, event)

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", capture_record)
    monkeypatch.setattr(issues_api.issue_repo, "create_issue", fake_create)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.post("/contracts/c1/issues", json={"title": "Royalty rate"})
    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "created"
    assert event.entity_type == "issue"
    assert event.entity_id == "issue-9"


def test_update_status_records_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_update(_conn: Any, issue_id: str, payload: Any) -> str:
        return issue_id

    async def fake_get(_conn: Any, issue_id: str) -> StoredIssue:
        return _stored_issue(issue_id, status="agreed", resolved_at=_NOW)

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return await _noop_record(_conn, event)

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", capture_record)
    monkeypatch.setattr(issues_api.issue_repo, "update_issue_status", fake_update)
    monkeypatch.setattr(issues_api.issue_repo, "get_issue", fake_get)

    resp = client.patch("/issues/issue-1/status", json={"status": "agreed"})
    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "status_changed"
    assert event.entity_type == "issue"
    assert event.entity_id == "issue-1"
    assert event.payload == {"status": "agreed"}


def test_create_comment_records_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_add(_conn: Any, payload: Any) -> str:
        return "comment-1"

    async def fake_list(_conn: Any, issue_id: str) -> list[StoredComment]:
        return [
            StoredComment(
                id="comment-1", issue_id=issue_id, actor="user", content="note", created_at=_NOW
            )
        ]

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return await _noop_record(_conn, event)

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api, "record_event", capture_record)
    monkeypatch.setattr(issues_api.issue_repo, "add_comment", fake_add)
    monkeypatch.setattr(issues_api.issue_repo, "list_comments", fake_list)

    resp = client.post("/issues/issue-1/comments", json={"actor": "user", "content": "note"})
    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "comment_added"
    assert event.entity_type == "issue"
    assert event.entity_id == "issue-1"


def test_create_comment_rejects_bad_actor() -> None:
    resp = client.post("/issues/issue-1/comments", json={"actor": "robot", "content": "hi"})
    assert resp.status_code == 422


def test_list_comments_chronological(monkeypatch: Any) -> None:
    async def fake_list(_conn: Any, issue_id: str) -> list[StoredComment]:
        return [
            StoredComment(id="c1", issue_id=issue_id, actor="user", content="a", created_at=_NOW),
            StoredComment(id="c2", issue_id=issue_id, actor="ai", content="b", created_at=_NOW),
        ]

    monkeypatch.setattr(issues_api, "acquire", _fake_acquire)
    monkeypatch.setattr(issues_api.issue_repo, "list_comments", fake_list)

    resp = client.get("/issues/issue-1/comments")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["id"] for c in body] == ["c1", "c2"]
