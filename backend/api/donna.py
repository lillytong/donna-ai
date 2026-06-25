"""Donna single-contract Q&A routes (F10) — thin (CLAUDE.md): validate, call the
service, return. All grounding/citation/deflection logic lives in services/donna/. A
provider rate limit maps to a clean 429 (mirrors the clause-search route). NOT registered
in main.py here — router registration is done centrally after merge."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.models.donna import (
    DonnaAskRequest,
    DonnaAskResponse,
    DonnaClearResponse,
    DonnaThreadResponse,
)
from backend.services.donna.qa import ask, clear_thread, get_thread
from backend.services.llm import LLMRateLimitError

router = APIRouter()


@router.post("/contracts/{contract_id}/donna/ask", response_model=DonnaAskResponse)
async def donna_ask(contract_id: str, payload: DonnaAskRequest) -> DonnaAskResponse:
    try:
        return await ask(contract_id, payload.question)
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc


@router.get("/contracts/{contract_id}/donna/thread", response_model=DonnaThreadResponse)
async def donna_thread(contract_id: str) -> DonnaThreadResponse:
    return await get_thread(contract_id)


@router.delete("/contracts/{contract_id}/donna/thread", response_model=DonnaClearResponse)
async def donna_clear_thread(contract_id: str) -> DonnaClearResponse:
    return await clear_thread(contract_id)
