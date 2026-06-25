"""Mode B revision review + decision routes (F03c) — thin (CLAUDE.md): validate,
call the service, map its typed `RevisionReviewError`s to HTTP, return.

Read:
  GET  /contracts/{contract_id}/revisions/sessions      → open/recent sessions
  GET  /revisions/sessions/{session_id}                 → full two-phase payload
Decision:
  POST /revisions/changes/{change_id}/confirm-match     → 6b abstain resolution
  POST /revisions/hunks/{hunk_id}/decide                → DD-27 four-action verdict
  POST /revisions/changes/{change_id}/decide-node       → whole-node (new/deleted)
  POST /revisions/sessions/{session_id}/apply           → apply to working copy
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.revision_import import StoredRevisionSession
from backend.models.revision_review import (
    ApplyResult,
    ConfirmMatchRequest,
    HunkDecideRequest,
    NodeDecideRequest,
    ReviewChange,
    ReviewPayload,
)
from backend.services.import_ import revision_review as svc

router = APIRouter()


@router.get(
    "/contracts/{contract_id}/revisions/sessions",
    response_model=list[StoredRevisionSession],
)
async def list_sessions(contract_id: str) -> list[StoredRevisionSession]:
    async with acquire() as conn:
        return await svc.list_sessions(conn, contract_id)


@router.get("/revisions/sessions/{session_id}", response_model=ReviewPayload)
async def get_session(session_id: str) -> ReviewPayload:
    async with acquire() as conn:
        try:
            return await svc.get_review_payload(conn, session_id)
        except svc.RevisionReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/revisions/changes/{change_id}/confirm-match", response_model=ReviewChange)
async def confirm_match(change_id: str, payload: ConfirmMatchRequest) -> ReviewChange:
    async with acquire() as conn:
        try:
            return await svc.confirm_match(conn, change_id, payload)
        except svc.RevisionReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/revisions/hunks/{hunk_id}/decide", response_model=ReviewChange)
async def decide_hunk(hunk_id: str, payload: HunkDecideRequest) -> ReviewChange:
    async with acquire() as conn:
        try:
            return await svc.decide_hunk(conn, hunk_id, payload)
        except svc.RevisionReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/revisions/changes/{change_id}/decide-node", response_model=ReviewChange)
async def decide_node(change_id: str, payload: NodeDecideRequest) -> ReviewChange:
    async with acquire() as conn:
        try:
            return await svc.decide_node(conn, change_id, payload)
        except svc.RevisionReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/revisions/sessions/{session_id}/apply", response_model=ApplyResult)
async def apply_session(session_id: str) -> ApplyResult:
    async with acquire() as conn:
        try:
            return await svc.apply_session(conn, session_id)
        except svc.RevisionReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
