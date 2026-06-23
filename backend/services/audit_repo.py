"""Persistence for the audit_log table (F19, asyncpg). Append + read only.

The table is APPEND-ONLY: this module exposes record_event (a single INSERT) and
list_events (a filtered read). There is deliberately no update or delete — the
audit trail is immutable. The payload column is JSONB: written via json.dumps +
::jsonb, read back via json.loads when asyncpg hands it back as a str.
"""

from __future__ import annotations

import json
from typing import Any

from backend.models.audit import AuditEvent, StoredAuditEvent

_INSERT_EVENT = """
INSERT INTO audit_log (event_type, entity_type, entity_id, actor, payload)
VALUES ($1, $2, $3, $4, $5::jsonb)
RETURNING id, event_type, entity_type, entity_id, actor, payload, created_at
"""

_LIST_EVENTS = """
SELECT id, event_type, entity_type, entity_id, actor, payload, created_at
FROM audit_log
WHERE ($1::text IS NULL OR entity_type = $1)
  AND ($2::uuid IS NULL OR entity_id = $2)
ORDER BY created_at DESC
LIMIT $3
"""


def _to_event(record: Any) -> StoredAuditEvent:
    payload = record["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    entity_id = record["entity_id"]
    return StoredAuditEvent(
        id=str(record["id"]),
        event_type=record["event_type"],
        entity_type=record["entity_type"],
        entity_id=str(entity_id) if entity_id is not None else None,
        actor=record["actor"],
        payload=payload,
        created_at=record["created_at"],
    )


async def record_event(conn: Any, event: AuditEvent) -> StoredAuditEvent:
    record = await conn.fetchrow(
        _INSERT_EVENT,
        event.event_type,
        event.entity_type,
        event.entity_id,
        event.actor,
        json.dumps(event.payload) if event.payload is not None else None,
    )
    return _to_event(record)


async def list_events(
    conn: Any,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
) -> list[StoredAuditEvent]:
    records = await conn.fetch(_LIST_EVENTS, entity_type, entity_id, limit)
    return [_to_event(r) for r in records]
