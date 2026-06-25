"""Mark-as-sent route (DD-71) — thin (CLAUDE.md): the boundary event the operator
records after sending the exported .docx manually.

`POST /contracts/{id}/mark-sent {recipient: "counterparty"|"legal"|"both",
acknowledge_drift?: bool}` cuts a snapshot of the current working copy, advances
the matching DD-48 `shared` pointer(s), and mints the next lineage v-number
(DD-70). Returns `MarkSentResult`: when the working copy was edited since the last
export and `acknowledge_drift` is false, it returns `marked=False, drift=True`
WITHOUT cutting — the non-blocking DD-72 warning — and the client re-calls with
`acknowledge_drift=True` to proceed.

NOTE: not yet registered in main.py — register `mark_sent.router` after merge.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.db import acquire
from backend.models.mark_sent import MarkSentRequest, MarkSentResult
from backend.services.mark_sent import mark_sent

router = APIRouter()


@router.post("/contracts/{contract_id}/mark-sent")
async def mark_sent_route(contract_id: str, request: MarkSentRequest) -> MarkSentResult:
    async with acquire() as conn:
        return await mark_sent(conn, contract_id, request)
