"""Donna issue-recommendation routes (F11, DD-68) — thin (CLAUDE.md): validate, call the
service, return. All grounding/citation/draft-vs-confirmed logic lives in
services/donna/. A provider rate limit maps to a clean 429 (mirrors the Q&A route).

  * POST   /contracts/{cid}/issues/{iid}/recommendation         -> generate + persist a draft
                                                                    (regenerate replaces it).
  * GET    /contracts/{cid}/issues/{iid}/recommendation         -> the current draft (404 if none).
  * POST   /contracts/{cid}/issues/{iid}/recommendation/confirm -> copy draft -> issues.* (DD-68).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from backend.models.recommendations import (
    RecommendationConfirmRequest,
    RecommendationConfirmResponse,
    StoredRecommendation,
)
from backend.services.donna.recommendations import (
    IssueNotFound,
    confirm_recommendation,
    generate_recommendation,
    get_recommendation,
)
from backend.services.llm import LLMRateLimitError

router = APIRouter()


@router.post(
    "/contracts/{contract_id}/issues/{issue_id}/recommendation",
    response_model=StoredRecommendation,
)
async def create_recommendation(contract_id: str, issue_id: str) -> StoredRecommendation:
    try:
        return await generate_recommendation(contract_id, issue_id)
    except IssueNotFound as exc:
        raise HTTPException(status_code=404, detail="issue not found") from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc


@router.get(
    "/contracts/{contract_id}/issues/{issue_id}/recommendation",
    response_model=StoredRecommendation,
)
async def read_recommendation(contract_id: str, issue_id: str) -> StoredRecommendation:
    stored = await get_recommendation(issue_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="no recommendation for this issue")
    return stored


@router.post(
    "/contracts/{contract_id}/issues/{issue_id}/recommendation/confirm",
    response_model=RecommendationConfirmResponse,
)
async def confirm(
    contract_id: str,
    issue_id: str,
    edit: Annotated[RecommendationConfirmRequest | None, Body()] = None,
) -> RecommendationConfirmResponse:
    result = await confirm_recommendation(issue_id, edit)
    if result is None:
        raise HTTPException(status_code=404, detail="no recommendation for this issue")
    return result
