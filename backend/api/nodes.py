"""Node-content routes (F08 inline edit, F08b create, clause delete, move) — thin
(CLAUDE.md): validate, call the service, map its domain errors to HTTP, return.

Each operation is audited inside its service in one transaction (edit/create/delete
also write a node_versions row; move does not — it is structure-only); the routes
only translate domain errors: NodeNotFound/Parent/After/BeforeNode -> 404,
NodeNotEditable/InvalidRole/BadPlacement/ConflictingAnchors/InvalidMove -> 422.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.imports import StoredNode
from backend.models.nodes import (
    NodeCreateRequest,
    NodeDeleteResponse,
    NodeEditRequest,
    NodeMoveRequest,
    NodeMoveResponse,
)
from backend.services import node_create, node_delete, node_edit, node_move

router = APIRouter()


@router.patch("/contracts/{contract_id}/nodes/{node_id}", response_model=StoredNode)
async def edit_node(contract_id: str, node_id: str, payload: NodeEditRequest) -> StoredNode:
    async with acquire() as conn:
        try:
            return await node_edit.edit_node(conn, contract_id, node_id, payload.text)
        except node_edit.NodeNotFound:
            raise HTTPException(status_code=404, detail="node not found") from None
        except node_edit.NodeNotEditable:
            raise HTTPException(status_code=422, detail="node is not inline-editable") from None


@router.post("/contracts/{contract_id}/nodes", response_model=StoredNode, status_code=201)
async def create_node(contract_id: str, payload: NodeCreateRequest) -> StoredNode:
    async with acquire() as conn:
        try:
            return await node_create.create_node(
                conn,
                contract_id,
                payload.parent_id,
                payload.after_node_id,
                payload.text,
                payload.role,
                payload.before_node_id,
            )
        except (
            node_create.ParentNotFound,
            node_create.AfterNodeNotFound,
            node_create.BeforeNodeNotFound,
        ):
            raise HTTPException(status_code=404, detail="anchor node not found") from None
        except node_create.InvalidRole:
            raise HTTPException(status_code=422, detail="invalid role") from None
        except (node_create.BadPlacement, node_create.ConflictingAnchors):
            raise HTTPException(status_code=422, detail="invalid placement") from None


@router.delete("/contracts/{contract_id}/nodes/{node_id}", response_model=NodeDeleteResponse)
async def delete_node(contract_id: str, node_id: str) -> NodeDeleteResponse:
    async with acquire() as conn:
        try:
            deleted_ids = await node_delete.delete_node(conn, contract_id, node_id)
        except node_delete.NodeNotFound:
            raise HTTPException(status_code=404, detail="node not found") from None
    return NodeDeleteResponse(deleted_ids=deleted_ids)


@router.post("/contracts/{contract_id}/nodes/{node_id}/move", response_model=NodeMoveResponse)
async def move_node(contract_id: str, node_id: str, payload: NodeMoveRequest) -> NodeMoveResponse:
    async with acquire() as conn:
        try:
            return await node_move.move_node(
                conn,
                contract_id,
                node_id,
                payload.parent_id,
                payload.after_node_id,
                payload.before_node_id,
            )
        except (
            node_move.NodeNotFound,
            node_move.ParentNotFound,
            node_move.AfterNodeNotFound,
            node_move.BeforeNodeNotFound,
        ):
            raise HTTPException(status_code=404, detail="node not found") from None
        except (
            node_move.InvalidMove,
            node_move.BadPlacement,
            node_move.ConflictingAnchors,
        ):
            raise HTTPException(status_code=422, detail="invalid move") from None
