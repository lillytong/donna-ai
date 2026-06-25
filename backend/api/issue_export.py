"""Issue-list export route (F31, DD-60) — thin (CLAUDE.md): resolve the contract,
load its issues + node tree, render the unresolved-issues table, stream the .docx.

`GET /contracts/{id}/issue-list/export`. 404 when the contract does not exist; a
contract with no unresolved issues still returns a valid header-only document (the
operator may want the clean record). The render is pure CPU and runs off the loop.

Not registered in backend.main — the router is wired in separately.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.db import acquire
from backend.services import issue_repo
from backend.services.contract_repo import fetch_nodes
from backend.services.export.filename import resolve_export_filename
from backend.services.export.issue_export import build_export, render_issue_list_docx
from backend.services.settings_repo import get_contract

router = APIRouter()

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/contracts/{contract_id}/issue-list/export")
async def export_issue_list(contract_id: str) -> Response:
    async with acquire() as conn:
        contract = await get_contract(conn, contract_id)
        if contract is None:
            raise HTTPException(status_code=404, detail="contract not found")
        issues = await issue_repo.list_issues(conn, contract_id)
        nodes = await fetch_nodes(conn, contract_id)
        filename = await resolve_export_filename(conn, contract, kind="open-issues")

    export = build_export(issues, nodes)
    data = await asyncio.to_thread(render_issue_list_docx, contract.name, export)

    return Response(
        content=data,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
