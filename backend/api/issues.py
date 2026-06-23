"""Issue + comment routes (F06/F07/F08c/F09) — thin (CLAUDE.md): validate, call a
service, return. All logic lives in services/issue_repo.py.

Create endpoints insert then read the row back on the same connection so the
response carries server-populated defaults (status, initiator, created_at). The FK
chain is enforced by the schema; a bad parent id surfaces as a DB error. The issue
id in a comment body is overridden by the path so the URL is authoritative.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.audit import (
    EVENT_COMMENT_ADDED,
    EVENT_CREATED,
    EVENT_STATUS_CHANGED,
    AuditEvent,
)
from backend.models.issues import (
    CommentCreate,
    IssueCreate,
    IssueStatusUpdate,
    StoredComment,
    StoredIssue,
)
from backend.services import issue_repo
from backend.services.audit_repo import record_event

router = APIRouter()


@router.post("/contracts/{contract_id}/issues", response_model=StoredIssue)
async def create_issue(contract_id: str, payload: IssueCreate) -> StoredIssue:
    payload = payload.model_copy(update={"contract_id": contract_id})
    async with acquire() as conn:
        new_id = await issue_repo.create_issue(conn, payload)
        stored = await issue_repo.get_issue(conn, new_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_CREATED,
                entity_type="issue",
                entity_id=new_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
    assert stored is not None  # just inserted
    return stored


@router.get("/contracts/{contract_id}/issues", response_model=list[StoredIssue])
async def list_issues(contract_id: str, status: str | None = None) -> list[StoredIssue]:
    async with acquire() as conn:
        return await issue_repo.list_issues(conn, contract_id, status)


@router.get("/issues/{issue_id}", response_model=StoredIssue)
async def get_issue(issue_id: str) -> StoredIssue:
    async with acquire() as conn:
        stored = await issue_repo.get_issue(conn, issue_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="issue not found")
    return stored


@router.patch("/issues/{issue_id}/status", response_model=StoredIssue)
async def update_issue_status(issue_id: str, payload: IssueStatusUpdate) -> StoredIssue:
    async with acquire() as conn:
        updated_id = await issue_repo.update_issue_status(conn, issue_id, payload)
        if updated_id is None:
            raise HTTPException(status_code=404, detail="issue not found")
        stored = await issue_repo.get_issue(conn, updated_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_STATUS_CHANGED,
                entity_type="issue",
                entity_id=issue_id,
                actor=get_settings().operator_actor,
                payload={"status": payload.status},
            ),
        )
    assert stored is not None  # just updated
    return stored


@router.post("/issues/{issue_id}/comments", response_model=StoredComment)
async def create_comment(issue_id: str, payload: CommentCreate) -> StoredComment:
    payload = payload.model_copy(update={"issue_id": issue_id})
    async with acquire() as conn:
        new_id = await issue_repo.add_comment(conn, payload)
        comments = await issue_repo.list_comments(conn, issue_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_COMMENT_ADDED,
                entity_type="issue",
                entity_id=issue_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
    stored = next((c for c in comments if c.id == new_id), None)
    assert stored is not None  # just inserted
    return stored


@router.get("/issues/{issue_id}/comments", response_model=list[StoredComment])
async def list_comments(issue_id: str) -> list[StoredComment]:
    async with acquire() as conn:
        return await issue_repo.list_comments(conn, issue_id)
