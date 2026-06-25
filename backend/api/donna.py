"""Donna single-contract Q&A + brainstorm routes (F10/F10b) — thin (CLAUDE.md): validate,
call the service, return. All grounding/citation/deflection logic lives in services/donna/.
A provider rate limit maps to a clean 429 (mirrors the clause-search route). NOT registered
in main.py here — router registration is done centrally after merge."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from backend.db import acquire
from backend.models.brainstorm import (
    BrainstormCloseRequest,
    BrainstormSummariesResponse,
    BrainstormTurnRequest,
    BrainstormTurnResponse,
    StoredBrainstormSummary,
)
from backend.models.donna import (
    DonnaAskRequest,
    DonnaChatResponse,
    DonnaClearResponse,
    DonnaThreadResponse,
)
from backend.services.donna.advise import chat
from backend.services.donna.brainstorm import (
    brainstorm_turn,
    close_brainstorm,
    list_brainstorm_summaries,
)
from backend.services.donna.qa import clear_thread, get_thread
from backend.services.llm import LLMRateLimitError

router = APIRouter()


@router.post("/contracts/{contract_id}/donna/ask", response_model=DonnaChatResponse)
async def donna_ask(contract_id: str, payload: DonnaAskRequest) -> DonnaChatResponse:
    # F10b: context-aware chat. No anchor -> F10 read-and-explain (preserved via chat);
    # a grounded anchor -> advise/draft. The boundary lives entirely in services/donna/.
    try:
        return await chat(contract_id, payload.question, payload.context)
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc


@router.post("/contracts/{contract_id}/donna/brainstorm", response_model=BrainstormTurnResponse)
async def donna_brainstorm(
    contract_id: str, payload: BrainstormTurnRequest
) -> BrainstormTurnResponse:
    # F10b/DD-73: one stateless brainstorm turn. The client holds the running transcript and
    # replays it; the backend persists nothing here (DD-77).
    try:
        return await brainstorm_turn(contract_id, payload)
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc


@router.post(
    "/contracts/{contract_id}/donna/brainstorm/close",
    response_model=StoredBrainstormSummary,
    responses={204: {"description": "Nothing substantive to distil; no summary stored"}},
)
async def donna_brainstorm_close(
    contract_id: str, payload: BrainstormCloseRequest
) -> StoredBrainstormSummary | Response:
    # F10b/DD-73: distil the transcript into one stored summary on the issue, or 204 when
    # there was nothing substantive to distil (no row written).
    try:
        summary = await close_brainstorm(contract_id, payload.issue_id, payload.turns)
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc
    if summary is None:
        return Response(status_code=204)
    return summary


@router.get("/issues/{issue_id}/brainstorm-summaries", response_model=BrainstormSummariesResponse)
async def donna_brainstorm_summaries(issue_id: str) -> BrainstormSummariesResponse:
    # F10b/DD-73: an issue's brainstorm history (issue-detail), newest first.
    async with acquire() as conn:
        summaries = await list_brainstorm_summaries(conn, issue_id)
    return BrainstormSummariesResponse(summaries=summaries)


@router.get("/contracts/{contract_id}/donna/thread", response_model=DonnaThreadResponse)
async def donna_thread(contract_id: str) -> DonnaThreadResponse:
    return await get_thread(contract_id)


@router.delete("/contracts/{contract_id}/donna/thread", response_model=DonnaClearResponse)
async def donna_clear_thread(contract_id: str) -> DonnaClearResponse:
    return await clear_thread(contract_id)
