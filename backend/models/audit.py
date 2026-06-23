"""Audit log (F19) — append-only trail; every mutation logged, never updated.

`AuditEvent` is the server-side input recorded from within a mutation (callers
never write via a public route). `StoredAuditEvent` is the row read back. The
schema's event_type/entity_type/actor are free-form TEXT (no CHECK constraints),
so they stay plain str — the EVENT_* constants below are the common values for
callers to reuse, not a closed enum that rejects others. `payload` is an open
JSONB blob, kept as a passthrough dict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

EVENT_CREATED = "created"
EVENT_UPDATED = "updated"
EVENT_STATUS_CHANGED = "status_changed"
EVENT_COMMITTED = "committed"
EVENT_COMMENT_ADDED = "comment_added"


class AuditEvent(BaseModel):
    event_type: str
    entity_type: str
    entity_id: str | None = None
    actor: str
    payload: dict[str, Any] | None = None


class StoredAuditEvent(BaseModel):
    id: str
    event_type: str
    entity_type: str
    entity_id: str | None = None
    actor: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class AuditQuery(BaseModel):
    entity_type: str | None = None
    entity_id: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
