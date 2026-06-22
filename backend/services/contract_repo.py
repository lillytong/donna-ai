"""Persistence for the contract node tree (asyncpg). DB integration only — runs
against a live Postgres (exercised in system tests / the local stack), no logic."""

from __future__ import annotations

import json
from typing import Any

from backend.models.contract_tree import NodeRow
from backend.models.imports import StoredNode

_INSERT_NODE = """
INSERT INTO nodes
    (contract_id, parent_id, order_index, content_type, heading, body, table_data,
     plain_text, role, has_placeholder)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)
RETURNING id
"""

_FETCH_NODES = """
SELECT id, parent_id, order_index, content_type, heading, body, table_data,
       plain_text, role, has_placeholder
FROM nodes
WHERE contract_id = $1 AND is_deleted = false
ORDER BY order_index
"""


async def insert_nodes(conn: Any, contract_id: str, rows: list[NodeRow]) -> dict[int, str]:
    """Insert rows in order, resolving parent_index -> generated id. Returns the
    index->id map. Rows must be topologically ordered (parents before children),
    which `tree_to_node_rows` guarantees."""
    id_for_index: dict[int, str] = {}
    for r in rows:
        parent_id = id_for_index[r.parent_index] if r.parent_index is not None else None
        table_json = json.dumps(r.table_data) if r.table_data is not None else None
        new_id = await conn.fetchval(
            _INSERT_NODE,
            contract_id,
            parent_id,
            r.order_index,
            r.content_type,
            r.heading,
            r.body,
            table_json,
            r.plain_text,
            r.role,
            r.has_placeholder,
        )
        id_for_index[r.index] = str(new_id)
    return id_for_index


def _to_stored_node(record: Any) -> StoredNode:
    table_data = record["table_data"]
    if isinstance(table_data, str):
        table_data = json.loads(table_data)
    parent_id = record["parent_id"]
    return StoredNode(
        id=str(record["id"]),
        parent_id=str(parent_id) if parent_id is not None else None,
        order_index=record["order_index"],
        content_type=record["content_type"],
        heading=record["heading"],
        body=record["body"],
        table_data=table_data,
        plain_text=record["plain_text"],
        role=record["role"],
        has_placeholder=record["has_placeholder"],
    )


async def fetch_nodes(conn: Any, contract_id: str) -> list[StoredNode]:
    """Live (non-deleted) nodes for a contract, ordered by order_index. asyncpg
    returns JSONB as text, so table_data is decoded here."""
    records = await conn.fetch(_FETCH_NODES, contract_id)
    return [_to_stored_node(r) for r in records]
