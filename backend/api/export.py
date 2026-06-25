"""Export route (F15b) — thin (CLAUDE.md): load the tree, render + cut a snapshot,
stream the .docx.

`POST /contracts/{id}/export` regenerates the contract's current clean state from
the DB to a Word document (DD-43, no tracked changes — F15 is a later slice). It can
MUTATE (hence POST, not GET): per DD-61 a **send** (recipient Counterparty/Legal)
cuts an `origin='export'` snapshot (F14) and advances the matching DD-48 `shared`
pointer; a **grab** (Internal/Copy-only) regenerates + downloads only — no snapshot,
no pointer, no lineage effect. 404 when the contract has no content. The orchestration
lives in the export service.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.db import acquire
from backend.models.export import CleanCopyExportRequest
from backend.services.contract_repo import fetch_nodes
from backend.services.export.clean_copy import export_clean_copy
from backend.services.export.filename import resolve_export_filename
from backend.services.settings_repo import get_contract

router = APIRouter()

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/contracts/{contract_id}/export")
async def export_contract(contract_id: str, request: CleanCopyExportRequest) -> Response:
    async with acquire() as conn:
        nodes = await fetch_nodes(conn, contract_id)
        if not nodes:
            raise HTTPException(status_code=404, detail="contract has no content to export")
        contract = await get_contract(conn, contract_id)
        style_config = contract.style_config if contract is not None else {}
        data = await export_clean_copy(conn, contract_id, nodes, style_config, request.recipient)
        filename = await resolve_export_filename(conn, contract)

    return Response(
        content=data,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
