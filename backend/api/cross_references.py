"""Cross-reference routes (F17) — thin (CLAUDE.md): validate, call the service, map
its domain errors to HTTP, return.

Extraction also runs automatically on import-commit (failure-isolated, see
backend/api/imports.py); this POST is the standalone re-run trigger.

  POST /contracts/{contract_id}/cross-references/extract  → rebuild + return links
  GET  /contracts/{contract_id}/cross-references           → the stored links
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.cross_references import (
    CrossReferencesResponse,
    ExtractCrossReferencesResponse,
)
from backend.services import cross_references

router = APIRouter()


@router.post(
    "/contracts/{contract_id}/cross-references/extract",
    response_model=ExtractCrossReferencesResponse,
)
async def extract_cross_references(contract_id: str) -> ExtractCrossReferencesResponse:
    async with acquire() as conn:
        try:
            _, refs = await cross_references.extract_and_store(conn, contract_id)
        except cross_references.ContractNotFound:
            raise HTTPException(status_code=404, detail="contract not found") from None
    return ExtractCrossReferencesResponse(
        contract_id=contract_id, references_found=len(refs), cross_references=refs
    )


@router.get(
    "/contracts/{contract_id}/cross-references",
    response_model=CrossReferencesResponse,
)
async def list_cross_references(contract_id: str) -> CrossReferencesResponse:
    async with acquire() as conn:
        refs = await cross_references.list_cross_references(conn, contract_id)
    return CrossReferencesResponse(contract_id=contract_id, cross_references=refs)
