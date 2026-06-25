"""Donna single-contract Q&A routes (F10) — thin (CLAUDE.md): validate, call the
service, return. All grounding/citation/deflection logic lives in services/donna/. A
provider rate limit maps to a clean 429 (mirrors the clause-search route). NOT registered
in main.py here — router registration is done centrally after merge."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.models.donna import (
    DonnaAskRequest,
    DonnaChatResponse,
    DonnaClearResponse,
    DonnaSeedBrainstormRequest,
    DonnaSeedBrainstormResponse,
    DonnaThreadResponse,
)
from backend.services.donna.advise import chat, seed_brainstorm
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


@router.post(
    "/contracts/{contract_id}/donna/seed-brainstorm",
    response_model=DonnaSeedBrainstormResponse,
)
async def donna_seed_brainstorm(
    contract_id: str, payload: DonnaSeedBrainstormRequest
) -> DonnaSeedBrainstormResponse:
    # F10b: persist the server-composed Brainstorm opening so a reloaded thread shows it.
    # No recommendation draft on the issue -> no-op (seeded=False, message=None).
    message = await seed_brainstorm(contract_id, payload.issue_id)
    return DonnaSeedBrainstormResponse(seeded=message is not None, message=message)


@router.get("/contracts/{contract_id}/donna/thread", response_model=DonnaThreadResponse)
async def donna_thread(contract_id: str) -> DonnaThreadResponse:
    return await get_thread(contract_id)


@router.delete("/contracts/{contract_id}/donna/thread", response_model=DonnaClearResponse)
async def donna_clear_thread(contract_id: str) -> DonnaClearResponse:
    return await clear_thread(contract_id)
