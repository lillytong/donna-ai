"""Donna per-change revision recommendation route (F03c, DD-78) — thin (CLAUDE.md): validate,
call the service, return. All grounding/parse/invariant logic lives in services/donna/. A
provider rate limit maps to a clean 429 (mirrors the Q&A / F11 routes).

  * POST /revisions/sessions/{session_id}/recommend -> analyze every not-yet-decided change in
        the session and write Donna's advisory verdict / significance / counter-language onto
        each hunk (idempotent; decided changes skipped). Returns the analyzed-count + tally.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.models.revision_recommend import RevisionRecommendSummary
from backend.services.donna.revision_recommend import SessionNotFound, recommend_session
from backend.services.llm import LLMRateLimitError

router = APIRouter()


@router.post(
    "/revisions/sessions/{session_id}/recommend",
    response_model=RevisionRecommendSummary,
)
async def recommend(session_id: str) -> RevisionRecommendSummary:
    try:
        return await recommend_session(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="revision session not found") from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc
