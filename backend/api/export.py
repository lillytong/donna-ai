"""Export route (F15b, DD-43, DD-71) — thin (CLAUDE.md): load the tree, render,
stream the .docx.

`POST /contracts/{id}/export` regenerates the contract's current clean state from
the DB to a Word document (DD-43, no tracked changes — redline is F15). **DD-71:
export is a pure grab** — it cuts NO snapshot, advances NO pointer, has zero
lineage effect; there is no recipient selector. It is POST (not GET) only because
it stamps `contracts.last_export_at` for the DD-72 drift marker (Mark-as-sent
compares each node's `updated_at` against it). The snapshot-cut boundary lives in
the Mark-as-sent action (`api/mark_sent.py`). 404 when the contract has no content.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.db import acquire
from backend.services.contract_repo import fetch_nodes
from backend.services.export.clean_copy import export_clean_copy
from backend.services.export.filename import resolve_export_filename
from backend.services.settings_repo import get_contract, touch_last_export

router = APIRouter()

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/contracts/{contract_id}/export")
async def export_contract(contract_id: str) -> Response:
    async with acquire() as conn:
        nodes = await fetch_nodes(conn, contract_id)
        if not nodes:
            raise HTTPException(status_code=404, detail="contract has no content to export")
        contract = await get_contract(conn, contract_id)
        style_config = contract.style_config if contract is not None else {}
        data = await export_clean_copy(nodes, style_config)
        filename = await resolve_export_filename(conn, contract)
        await touch_last_export(conn, contract_id)

    return Response(
        content=data,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
