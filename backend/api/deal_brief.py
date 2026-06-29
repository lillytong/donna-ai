"""Per-contract deal-brief routes (F37 / DD-95) — thin (CLAUDE.md): validate, call the
repo / service, return. The global-context brief Donna distils at import and the operator
edits; Part B injects it into the {deal_context} grounding slot.

  * GET  /contracts/{contract_id}/deal-brief          -> the current brief (empty when none)
  * PUT  /contracts/{contract_id}/deal-brief {content} -> operator edit (edits win)
  * POST /contracts/{contract_id}/deal-brief/refresh   -> force a fresh Donna distil (Refresh)

GET returns an empty brief (no row yet) rather than 404 so the editor and the {deal_context}
grounding both treat "never distilled" as the no-op case. The refresh is a deliberate operator
action, so it runs the distil inline (one Opus call, longer timeout) and returns the new brief.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.deal_brief import DealBrief, DealBriefEdit
from backend.services import deal_brief_repo
from backend.services.donna.deal_brief import distill_deal_brief

router = APIRouter()


@router.get("/contracts/{contract_id}/deal-brief", response_model=DealBrief)
async def read_deal_brief(contract_id: str) -> DealBrief:
    async with acquire() as conn:
        brief = await deal_brief_repo.get_brief(conn, contract_id)
    return brief if brief is not None else DealBrief(contract_id=contract_id)


@router.put("/contracts/{contract_id}/deal-brief", response_model=DealBrief)
async def write_deal_brief(contract_id: str, payload: DealBriefEdit) -> DealBrief:
    async with acquire() as conn:
        return await deal_brief_repo.update_brief(conn, contract_id, payload.content)


@router.post("/contracts/{contract_id}/deal-brief/refresh", response_model=DealBrief)
async def refresh_deal_brief(contract_id: str) -> DealBrief:
    """Force a fresh Donna distil, overwriting any prior brief (including an operator-edited
    one — Refresh is the explicit operator request to regenerate). 404 when the contract has
    no nodes to read."""
    async with acquire() as conn:
        brief = await distill_deal_brief(conn, contract_id, force=True)
    if brief is None:
        raise HTTPException(
            status_code=404, detail="no contract content to distil a deal brief from"
        )
    return brief
