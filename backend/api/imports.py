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
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.audit import EVENT_COMMITTED, AuditEvent
from backend.models.contract_tree import NodeImage
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
async def preview_import(
    request: Request,
    contract_id: str | None = Query(
        default=None,
        description=(
            "Contract UUID — when provided, extracted image bytes are staged in "
            "staging_node_images so the two-step commit can persist them to node_images."
        ),
    ),
) -> PreviewResponse:
    data = await request.body()
    if not data.startswith(_DOCX_MAGIC):
        raise HTTPException(status_code=400, detail="expected .docx (ZIP) bytes")
    path = await asyncio.to_thread(_write_temp, data)
    try:
        if contract_id is not None:
            async with acquire() as conn:
                return await preview_docx(path, conn=conn, contract_id=contract_id)
        return await preview_docx(path)
    finally:
        await asyncio.to_thread(os.unlink, path)


@router.post("/contracts/{contract_id}/commit", response_model=ImportResult)
async def commit_contract(
    contract_id: str, body: CommitRequest, background: BackgroundTasks
) -> ImportResult:
    async with acquire() as conn:
        async with conn.transaction():
            id_map = await insert_nodes(conn, contract_id, body.nodes)
            # Move any images staged at preview time into node_images now that we
            # have real node UUIDs from id_map (keyed by TreeNode.index).
            staging = await conn.fetch(
                "SELECT node_index, mime_type, cx_emu, cy_emu, data"
                " FROM staging_node_images WHERE contract_id = $1",
                contract_id,
            )
            for s in staging:
                node_id = id_map.get(s["node_index"])
                if node_id:
                    await conn.execute(
                        """INSERT INTO node_images
                               (node_id, order_index, mime_type, cx_emu, cy_emu, data)
                           VALUES ($1, 0, $2, $3, $4, $5)
                           ON CONFLICT DO NOTHING""",
                        node_id,
                        s["mime_type"],
                        s["cx_emu"],
                        s["cy_emu"],
                        bytes(s["data"]),
                    )
            await conn.execute(
                "DELETE FROM staging_node_images WHERE contract_id = $1",
                contract_id,
            )
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


@router.get("/contracts/{contract_id}/media/{image_id}")
async def get_contract_image(contract_id: str, image_id: str) -> Any:
    """Serve raw image bytes for a node_image that belongs to this contract.
    Scoped by contract_id so a caller cannot fetch images from other contracts
    by guessing an image UUID."""
    from fastapi.responses import Response

    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT ni.mime_type, ni.data
               FROM node_images ni
               JOIN nodes n ON n.id = ni.node_id
               WHERE ni.id = $1 AND n.contract_id = $2""",
            image_id,
            contract_id,
        )
    if row is None:
        raise HTTPException(status_code=404)
    return Response(content=bytes(row["data"]), media_type=row["mime_type"])


@router.get("/contracts/{contract_id}/tree", response_model=ContractTreeResponse)
async def get_contract_tree(contract_id: str) -> ContractTreeResponse:
    async with acquire() as conn:
        rows = await fetch_nodes(conn, contract_id)
        img_rows = await conn.fetch(
            """SELECT ni.id::text, ni.node_id::text, ni.order_index, ni.mime_type
               FROM node_images ni
               JOIN nodes n ON n.id = ni.node_id
               WHERE n.contract_id = $1
               ORDER BY ni.node_id, ni.order_index""",
            contract_id,
        )
    images_by_node: dict[str, list] = {}
    for r in img_rows:
        images_by_node.setdefault(r["node_id"], []).append(
            NodeImage(id=r["id"], node_id=r["node_id"], order_index=r["order_index"], mime_type=r["mime_type"])
        )
    tree = ContractTreeResponse.from_rows(contract_id, rows)
    for node in tree.nodes:
        _attach_images(node, images_by_node)
    return tree


def _attach_images(node: Any, images_by_node: dict) -> None:
    node.images = images_by_node.get(node.id, [])
    for child in node.children:
        _attach_images(child, images_by_node)
