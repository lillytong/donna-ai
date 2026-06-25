"""Redline export route (F15) — thin (CLAUDE.md): resolve baseline + diff + render
in the service, stream the tracked-changes .docx.

`POST /contracts/{id}/redline-export` body `{snapshot_id?}` streams a Word document
with `<w:ins>`/`<w:del>` tracked changes of the working copy against a baseline
snapshot (null = the `last_shared_with_counterparty` pointer, DD-48/DD-61). Read-only
(no snapshot cut, no pointer moved) — POST only for the request body. 409 when no
baseline exists (the redline is unavailable; the UI disables it) — per DD-71 the
first **Mark as sent** is what cuts that first baseline snapshot, so the gate keys
off marking, not export. 404 when an explicit `snapshot_id` is not this contract's.

NOTE: not yet registered in main.py — register `redline.router` after merge.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.db import acquire
from backend.models.redline import RedlineExportRequest
from backend.services.export.filename import resolve_export_filename
from backend.services.export.redline import (
    BaselineNotFound,
    NoBaselineSnapshot,
    build_redline,
)
from backend.services.settings_repo import get_contract

router = APIRouter()

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/contracts/{contract_id}/redline-export")
async def redline_export(contract_id: str, request: RedlineExportRequest) -> Response:
    async with acquire() as conn:
        contract = await get_contract(conn, contract_id)
        style_config = contract.style_config if contract is not None else {}
        try:
            data = await build_redline(conn, contract_id, request.snapshot_id, style_config)
        except NoBaselineSnapshot:
            raise HTTPException(
                status_code=409,
                detail="no baseline snapshot to diff against; mark as sent first",
            ) from None
        except BaselineNotFound:
            raise HTTPException(
                status_code=404, detail="baseline snapshot not found for this contract"
            ) from None
        filename = await resolve_export_filename(conn, contract, kind="redline")

    return Response(
        content=data,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
