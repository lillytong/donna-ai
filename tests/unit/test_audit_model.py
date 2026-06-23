"""Audit models (F19): input/output shape, optional fields, query bounds."""

from __future__ import annotations

import pytest
from backend.models.audit import (
    EVENT_COMMENT_ADDED,
    EVENT_COMMITTED,
    EVENT_CREATED,
    EVENT_STATUS_CHANGED,
    EVENT_UPDATED,
    AuditEvent,
    AuditQuery,
    StoredAuditEvent,
)
from pydantic import ValidationError


def test_audit_event_minimal() -> None:
    event = AuditEvent(event_type="created", entity_type="contract", actor="operator")
    assert event.entity_id is None
    assert event.payload is None


def test_audit_event_full_passthrough_payload() -> None:
    event = AuditEvent(
        event_type="status_changed",
        entity_type="issue",
        entity_id="11111111-1111-1111-1111-111111111111",
        actor="operator",
        payload={"from": "open", "to": "resolved", "nested": {"k": 1}},
    )
    assert event.payload == {"from": "open", "to": "resolved", "nested": {"k": 1}}


def test_audit_event_requires_actor() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(event_type="created", entity_type="contract")  # type: ignore[call-arg]


def test_event_type_constants_are_free_form_strings() -> None:
    assert EVENT_CREATED == "created"
    assert EVENT_UPDATED == "updated"
    assert EVENT_STATUS_CHANGED == "status_changed"
    assert EVENT_COMMITTED == "committed"
    assert EVENT_COMMENT_ADDED == "comment_added"
    # constants are advisory: arbitrary event types are still accepted
    event = AuditEvent(event_type="exported", entity_type="contract", actor="operator")
    assert event.event_type == "exported"


def test_stored_audit_event_roundtrip() -> None:
    from datetime import UTC, datetime

    stored = StoredAuditEvent(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        event_type="committed",
        entity_type="contract_version",
        entity_id=None,
        actor="donna",
        payload=None,
        created_at=datetime(2026, 6, 23, tzinfo=UTC),
    )
    assert stored.entity_id is None
    assert stored.payload is None


def test_audit_query_defaults_and_bounds() -> None:
    assert AuditQuery().limit == 100
    with pytest.raises(ValidationError):
        AuditQuery(limit=0)
    with pytest.raises(ValidationError):
        AuditQuery(limit=1001)
