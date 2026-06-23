"""Audit log routes (F19) — thin (CLAUDE.md): validate, call a service, return.

Read-only surface. The audit trail is append-only and writes happen server-side
via audit_repo.record_event from within mutations, so there is deliberately no
POST here — clients cannot write arbitrary audit entries.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.db import acquire
from backend.models.audit import StoredAuditEvent
from backend.services import audit_repo

router = APIRouter()


@router.get("/audit", response_model=list[StoredAuditEvent])
async def list_audit_events(
    entity_type: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[StoredAuditEvent]:
    async with acquire() as conn:
        return await audit_repo.list_events(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )
