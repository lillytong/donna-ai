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
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.audit import EVENT_COMMITTED, AuditEvent
from backend.models.imports import (
    CommitRequest,
    ContractTreeResponse,
    ImportResult,
    PreviewResponse,
)
from backend.services.audit_repo import record_event
from backend.services.contract_repo import fetch_nodes, insert_nodes
from backend.services.cross_references import extract_and_store as extract_cross_references
from backend.services.defined_terms import extract_and_store
from backend.services.donna.deal_brief import distill_on_import
from backend.services.import_.pipeline import import_docx, preview_docx

log = structlog.get_logger()

router = APIRouter()

_DOCX_MAGIC = b"PK\x03\x04"  # .docx is a ZIP container


async def _extract_defined_terms_safely(conn: Any, contract_id: str) -> None:
    """Auto-populate F16 defined terms after an import commit. Isolated on purpose:
    runs AFTER the import transaction has committed and swallows any error (logged),
    so an extraction failure can never roll back or lose the just-committed contract."""
    try:
        await extract_and_store(conn, contract_id)
    except Exception:
        log.warning("defined_terms_extraction_failed", contract_id=contract_id, exc_info=True)


async def _extract_cross_references_safely(conn: Any, contract_id: str) -> None:
    """Auto-populate F17 cross-reference links after an import commit. Isolated like
    the defined-terms pass above: runs after the import transaction has committed and
    swallows any error (logged), so an extraction failure can never roll back or lose
    the just-committed contract."""
    try:
        await extract_cross_references(conn, contract_id)
    except Exception:
        log.warning("cross_references_extraction_failed", contract_id=contract_id, exc_info=True)


def _write_temp(data: bytes) -> str:
    fd, name = tempfile.mkstemp(suffix=".docx")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return name


@router.post("/contracts/{contract_id}/import", response_model=ImportResult)
async def import_contract(
    contract_id: str, request: Request, background: BackgroundTasks
) -> ImportResult:
    data = await request.body()
    if not data.startswith(_DOCX_MAGIC):
        raise HTTPException(status_code=400, detail="expected .docx (ZIP) bytes")
    path = await asyncio.to_thread(_write_temp, data)
    try:
        async with acquire() as conn, conn.transaction():
            result = await import_docx(conn, contract_id, path)
    finally:
        await asyncio.to_thread(os.unlink, path)
    # F37 auto-seed (DD-95): now that the import has committed, distil the deal brief in the
    # background so the {deal_context} grounding slot is populated before review opens. Post-
    # commit, non-blocking, failure-isolated; force=False, so a re-import respects an operator
    # edit (edits win). Mirrors F03c's recommend_on_import wiring.
    background.add_task(distill_on_import, contract_id)
    return result


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
async def commit_contract(
    contract_id: str, body: CommitRequest, background: BackgroundTasks
) -> ImportResult:
    async with acquire() as conn:
        async with conn.transaction():
            await insert_nodes(conn, contract_id, body.nodes)
            await record_event(
                conn,
                AuditEvent(
                    event_type=EVENT_COMMITTED,
                    entity_type="contract",
                    entity_id=contract_id,
                    actor=get_settings().operator_actor,
                    payload={"node_count": len(body.nodes)},
                ),
            )
        await _extract_defined_terms_safely(conn, contract_id)
        await _extract_cross_references_safely(conn, contract_id)
    # F37 auto-seed (DD-95): post-commit, distil the deal brief in the background so the
    # {deal_context} grounding slot is populated before review opens. Failure-isolated;
    # force=False so a re-import respects an operator edit (edits win).
    background.add_task(distill_on_import, contract_id)
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
