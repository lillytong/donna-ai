"""Node-content routes (F08 direct inline edit, F08b new node creation) — thin
(CLAUDE.md): validate, call the service, map its domain errors to HTTP, return.

Each operation is versioned + audited inside its service in one transaction; the
routes only translate domain errors: NodeNotFound/Parent/AfterNode -> 404,
NodeNotEditable/InvalidRole/BadPlacement -> 422.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.imports import StoredNode
from backend.models.nodes import NodeCreateRequest, NodeEditRequest
from backend.services import node_create, node_edit

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
            )
        except (node_create.ParentNotFound, node_create.AfterNodeNotFound):
            raise HTTPException(status_code=404, detail="anchor node not found") from None
        except node_create.InvalidRole:
            raise HTTPException(status_code=422, detail="invalid role") from None
        except node_create.BadPlacement:
            raise HTTPException(status_code=422, detail="invalid placement") from None
