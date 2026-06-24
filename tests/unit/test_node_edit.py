"""Direct inline edit service (F08): field selection, no-op guard, rejection of
non-prose / derived-only nodes.

No live DB: a fake connection returns canned node records and records the
execute() calls, so the field selection, the version INSERT args, and the no-op
short-circuit are exercised without Postgres. record_event is stubbed to capture
the audit event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.services import node_edit


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


async def test_edit_persists_writes_update_version_and_audit(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_edit, "record_event", _capture_record(captured))
    conn = _FakeConn(_node_record(), _node_record(body="New body.", plain_text="New body."))

    result = await node_edit.edit_node(conn, "c1", "n1", "New body.")

    assert result.body == "New body."
    update_sql, update_args = conn.executes[0]
    assert "body =" in update_sql and update_args == ("n1", "New body.")
    version_sql, version_args = conn.executes[1]
    assert "INSERT INTO node_versions" in version_sql
    assert version_args == ("n1", "Original body.", "New body.", "user")  # before, after, actor

    event = captured["event"]
    assert event.event_type == "node_edited"
    assert event.entity_type == "node"
    assert event.entity_id == "n1"
    assert event.actor == "operator"  # audit actor = settings.operator_actor (matches issues)
    assert event.payload == {"field": "body"}


async def test_noop_edit_skips_version_and_audit(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_edit, "record_event", _capture_record(captured))
    conn = _FakeConn(_node_record(body="Same."))

    result = await node_edit.edit_node(conn, "c1", "n1", "Same.")

    assert result.body == "Same."
    assert conn.executes == []  # no update, no version
    assert "event" not in captured  # no audit


async def test_heading_edited_when_body_null(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(node_edit, "record_event", _capture_record(captured))
    load = _node_record(body=None, heading="Confidentiality")
    updated = _node_record(body=None, heading="Confidential Information")
    conn = _FakeConn(load, updated)

    result = await node_edit.edit_node(conn, "c1", "n1", "Confidential Information")

    assert result.heading == "Confidential Information"
    update_sql, update_args = conn.executes[0]
    assert "heading =" in update_sql and update_args == ("n1", "Confidential Information")
    _, version_args = conn.executes[1]
    assert version_args == ("n1", "Confidentiality", "Confidential Information", "user")
    assert captured["event"].payload == {"field": "heading"}


async def test_table_node_rejected() -> None:
    conn = _FakeConn(_node_record(content_type="table", body=None, table_data=[["a", "b"]]))
    with pytest.raises(node_edit.NodeNotEditable):
        await node_edit.edit_node(conn, "c1", "n1", "x")
    assert conn.executes == []


async def test_derived_only_node_rejected() -> None:
    # prose node with neither body nor heading — only the derived plain_text.
    conn = _FakeConn(_node_record(body=None, heading=None, plain_text="x"))
    with pytest.raises(node_edit.NodeNotEditable):
        await node_edit.edit_node(conn, "c1", "n1", "x")
    assert conn.executes == []


async def test_missing_node_raises_not_found() -> None:
    conn = _FakeConn(None)
    with pytest.raises(node_edit.NodeNotFound):
        await node_edit.edit_node(conn, "c1", "missing", "x")
