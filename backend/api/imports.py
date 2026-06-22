"""Import + tree-read routes — thin (CLAUDE.md): validate, call a service, return.

The POST accepts the raw .docx bytes as the request body
(application/octet-stream), written to a temp file for the parser. Multipart
file upload is the eventual UI path (Step 1, §9) but needs `python-multipart`;
raw body keeps the dependency set unchanged for the import spine.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from fastapi import APIRouter, HTTPException, Request

from backend.db import acquire
from backend.models.imports import (
    CommitRequest,
    ContractTreeResponse,
    ImportResult,
    PreviewResponse,
)
from backend.services.contract_repo import fetch_nodes, insert_nodes
from backend.services.import_.pipeline import import_docx, preview_docx

router = APIRouter()

_DOCX_MAGIC = b"PK\x03\x04"  # .docx is a ZIP container


def _write_temp(data: bytes) -> str:
    fd, name = tempfile.mkstemp(suffix=".docx")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return name


@router.post("/contracts/{contract_id}/import", response_model=ImportResult)
async def import_contract(contract_id: str, request: Request) -> ImportResult:
    data = await request.body()
    if not data.startswith(_DOCX_MAGIC):
        raise HTTPException(status_code=400, detail="expected .docx (ZIP) bytes")
    path = await asyncio.to_thread(_write_temp, data)
    try:
        async with acquire() as conn, conn.transaction():
            return await import_docx(conn, contract_id, path)
    finally:
        await asyncio.to_thread(os.unlink, path)


@router.post("/import/preview", response_model=PreviewResponse)
async def preview_import(request: Request) -> PreviewResponse:
    data = await request.body()
    if not data.startswith(_DOCX_MAGIC):
        raise HTTPException(status_code=400, detail="expected .docx (ZIP) bytes")
    path = await asyncio.to_thread(_write_temp, data)
    try:
        return await preview_docx(path)
    finally:
        await asyncio.to_thread(os.unlink, path)


@router.post("/contracts/{contract_id}/commit", response_model=ImportResult)
async def commit_contract(contract_id: str, body: CommitRequest) -> ImportResult:
    async with acquire() as conn, conn.transaction():
        await insert_nodes(conn, contract_id, body.nodes)
    return ImportResult(
        contract_id=contract_id,
        node_count=len(body.nodes),
        root_count=sum(1 for n in body.nodes if n.parent_index is None),
        uncertain_count=sum(1 for n in body.nodes if n.uncertain),
    )


@router.get("/contracts/{contract_id}/tree", response_model=ContractTreeResponse)
async def get_contract_tree(contract_id: str) -> ContractTreeResponse:
    async with acquire() as conn:
        rows = await fetch_nodes(conn, contract_id)
    return ContractTreeResponse.from_rows(contract_id, rows)
