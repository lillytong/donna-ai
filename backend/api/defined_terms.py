"""Defined-terms routes (F16) — thin (CLAUDE.md): validate, call the service, map
its domain errors to HTTP, return.

Extraction is a STANDALONE trigger here, deliberately additive: wiring it to run
automatically on import-commit is a queued follow-up (see DEV_TODO / PM_TODO), kept
out of the import pipeline so this lands without touching shared flow.

  POST /contracts/{contract_id}/defined-terms/extract  → run extraction, return terms
  GET  /deals/{deal_id}/defined-terms                   → the deal's registry
  GET  /contracts/{contract_id}/defined-terms           → resolves deal, returns registry
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.defined_terms import DefinedTermsResponse, ExtractResponse
from backend.services import defined_terms

router = APIRouter()


@router.post("/contracts/{contract_id}/defined-terms/extract", response_model=ExtractResponse)
async def extract_defined_terms(contract_id: str) -> ExtractResponse:
    async with acquire() as conn:
        try:
            deal_id, terms = await defined_terms.extract_and_store(conn, contract_id)
        except defined_terms.ContractNotFound:
            raise HTTPException(status_code=404, detail="contract not found") from None
    return ExtractResponse(
        contract_id=contract_id, deal_id=deal_id, terms_found=len(terms), terms=terms
    )


@router.get("/deals/{deal_id}/defined-terms", response_model=DefinedTermsResponse)
async def list_deal_defined_terms(deal_id: str) -> DefinedTermsResponse:
    async with acquire() as conn:
        terms = await defined_terms.list_terms_for_deal(conn, deal_id)
    return DefinedTermsResponse(deal_id=deal_id, terms=terms)


@router.get("/contracts/{contract_id}/defined-terms", response_model=DefinedTermsResponse)
async def list_contract_defined_terms(contract_id: str) -> DefinedTermsResponse:
    async with acquire() as conn:
        try:
            deal_id = await defined_terms.resolve_deal_id(conn, contract_id)
        except defined_terms.ContractNotFound:
            raise HTTPException(status_code=404, detail="contract not found") from None
        terms = await defined_terms.list_terms_for_deal(conn, deal_id)
    return DefinedTermsResponse(deal_id=deal_id, terms=terms)
