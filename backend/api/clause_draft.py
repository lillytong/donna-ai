"""Donna-assisted clause-drafting route (F08d) — thin (CLAUDE.md): validate, call the
service, return. All grounding/citation logic lives in services/donna/drafting.py. The draft
is transient — the operator commits it via the F08b create route. A provider rate limit maps
to a clean 429 (mirrors the Q&A / recommendation routes).

  * POST /contracts/{cid}/nodes/draft -> a grounded clause draft to pre-fill the insert editor.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.models.clause_draft import ClauseDraft, ClauseDraftRequest
from backend.services.donna.drafting import ContractNotFound, draft_clause
from backend.services.llm import LLMRateLimitError

router = APIRouter()


@router.post("/contracts/{contract_id}/nodes/draft", response_model=ClauseDraft)
async def create_clause_draft(contract_id: str, req: ClauseDraftRequest) -> ClauseDraft:
    try:
        return await draft_clause(contract_id, req)
    except ContractNotFound as exc:
        raise HTTPException(status_code=404, detail="contract not found") from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail="LLM rate limit; retry shortly") from exc
