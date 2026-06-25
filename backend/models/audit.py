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
EVENT_NODE_EDITED = "node_edited"  # F08 direct inline edit of a node's text
EVENT_NODE_CREATED = "node_created"  # F08b new node created mid-negotiation
EVENT_NODE_DELETED = "node_deleted"  # clause soft-deleted (with its sub-tree)
EVENT_NODE_MOVED = "node_moved"  # clause reordered up/down among its siblings
EVENT_SNAPSHOT_CUT = "snapshot_cut"  # F14 point-in-time capture (e.g. send to counterparty)
EVENT_MARK_SENT = "mark_sent"  # DD-71 boundary event: snapshot cut + shared pointer(s) advanced
EVENT_RECOMMENDATION_CONFIRMED = "recommendation_confirmed"  # F11 draft -> issues.* (DD-68)
EVENT_REVISION_IMPORTED = "revision_imported"  # F03b Mode B counterparty/legal revision import
EVENT_REVISION_MATCH_CONFIRMED = "revision_match_confirmed"  # F03c 6b abstain resolution
EVENT_REVISION_SESSION_APPLIED = "revision_session_applied"  # F03c apply → working copy + issues


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
