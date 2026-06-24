"""Conceptual clause-search route — thin (CLAUDE.md): validate, call the service,
return. All logic (candidate building, the LLM call, the hallucinated-id guard)
lives in services/clause_search.py. A provider rate limit is mapped to a clean 429
so it never surfaces as an unhandled 500."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.models.clause_search import ClauseSearchRequest, ClauseSearchResult
from backend.services.clause_search import search_clause
from backend.services.llm import LLMRateLimitError

router = APIRouter()


@router.post("/contracts/{contract_id}/clause-search", response_model=ClauseSearchResult)
async def clause_search(contract_id: str, payload: ClauseSearchRequest) -> ClauseSearchResult:
    try:
        return await search_clause(contract_id, payload.query)
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc
