"""DD-86 "Start over" route (F03c) — thin (CLAUDE.md): reset the staging session, then
return the freshly re-rendered ReviewPayload so the client re-renders all-pending.

  POST /contracts/{cid}/revisions/sessions/{sid}/reset → reset + fresh ReviewPayload

Confirm-gating is client-side (the frontend shows the confirm dialog); this endpoint
just executes. 404 if the session is missing / not this contract's; 409 if already
applied.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.revision_review import ReviewPayload
from backend.services.import_ import revision_reset, revision_review

router = APIRouter()


@router.post(
    "/contracts/{contract_id}/revisions/sessions/{session_id}/reset",
    response_model=ReviewPayload,
)
async def reset_session(contract_id: str, session_id: str) -> ReviewPayload:
    async with acquire() as conn:
        try:
            await revision_reset.reset_session(conn, contract_id, session_id)
            return await revision_review.get_review_payload(conn, session_id)
        except revision_review.RevisionReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
