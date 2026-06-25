"""Lineage + read-only snapshot-render routes (F27, DD-75) — thin (CLAUDE.md).

`GET /contracts/{id}/lineage` → the lifecycle badge + the numbered version timeline
+ the live working-copy marker + the two greyed `received` placeholder slots
(`LineageView`).

`GET /contracts/{id}/snapshots/{snapshot_id}/tree` → a read-only, render-ready node
tree rebuilt from the frozen snapshot dump (same `ContractTreeResponse` shape the
cockpit renders live nodes from), so the frontend can open a historical version
read-only.

NOTE: register `lineage.router` in main.py (`app.include_router(lineage.router)`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.imports import ContractTreeResponse
from backend.models.lineage import LineageView
from backend.services.lineage import get_lineage
from backend.services.snapshot import get_snapshot_tree

router = APIRouter()


@router.get("/contracts/{contract_id}/lineage", response_model=LineageView)
async def contract_lineage(contract_id: str) -> LineageView:
    async with acquire() as conn:
        return await get_lineage(conn, contract_id)


@router.get(
    "/contracts/{contract_id}/snapshots/{snapshot_id}/tree",
    response_model=ContractTreeResponse,
)
async def snapshot_tree(contract_id: str, snapshot_id: str) -> ContractTreeResponse:
    async with acquire() as conn:
        tree = await get_snapshot_tree(conn, contract_id, snapshot_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="snapshot not found for contract")
    return tree
