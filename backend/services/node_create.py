"""Create a new node (clause/section) mid-negotiation (F08b) — asyncpg.

The operator adds a clause on the fly. It anchors to a parent and lands at a
computed `order_index`; its displayed clause number is DERIVED from tree position
(DD-02), so the backend only fixes `parent_id` + `order_index` — never a number.
The `node_versions` insertion row written here (`body_before=NULL`, `body_after`
= the new text) is what later renders as a tracked insertion in the next redline
(SPEC §5 F08b).

One transaction: any sibling re-space (the OQ-07 no-gap fallback), the node
INSERT, the version-row INSERT, and the audit event commit together. The anchor →
order_index placement (gap-based + respace fallback + the IS NOT DISTINCT FROM
sibling fetch) is shared with `node_move` via `node_placement`.

Two actor vocabularies, matching F08 (`node_edit`): `node_versions.actor` is
CHECK-constrained to ('user','ai','principal') — content authorship — so the
version row records 'user'; the audit event uses the operator identity shared
across all operator-initiated events (`settings.operator_actor`).
"""

from __future__ import annotations

from typing import Any, get_args

from backend.config.settings import get_settings
from backend.models.audit import EVENT_NODE_CREATED, AuditEvent
from backend.models.contract_tree import Role
from backend.models.imports import StoredNode
from backend.services.audit_repo import record_event
from backend.services.contract_repo import _to_stored_node
from backend.services.node_placement import (
    FETCH_SIBLINGS,
    compute_order_index,
    norm,
    recompute_after_respace,
    respace_siblings,
)

_VERSION_ACTOR = "user"
_VALID_ROLES: frozenset[str] = frozenset(get_args(Role))


class ParentNotFound(Exception):
    """parent_id given but missing, soft-deleted, or not in this contract."""


class AfterNodeNotFound(Exception):
    """after_node_id given but missing, soft-deleted, or not in this contract."""


class BeforeNodeNotFound(Exception):
    """before_node_id given but missing, soft-deleted, or not in this contract."""


class BadPlacement(Exception):
    """anchor node exists but is not a child of parent_id (placement mismatch)."""


class ConflictingAnchors(Exception):
    """after_node_id and before_node_id both given — anchors are mutually exclusive."""


class InvalidRole(Exception):
    """role is outside the schema CHECK list."""


_FETCH_NODE = """
SELECT id, parent_id, order_index, content_type, heading, body, table_data,
       plain_text, role, has_placeholder
FROM nodes
WHERE id = $1 AND contract_id = $2 AND is_deleted = false
"""

_INSERT_NODE = """
INSERT INTO nodes (contract_id, parent_id, order_index, content_type, role, body)
VALUES ($1, $2, $3, 'prose', $4, $5)
RETURNING id
"""

# Insertion: body_before is NULL (no prior text); snapshot_id NULL until cut.
_INSERT_VERSION = """
INSERT INTO node_versions (node_id, snapshot_id, body_before, body_after, actor)
VALUES ($1, NULL, NULL, $2, $3)
"""


async def create_node(
    conn: Any,
    contract_id: str,
    parent_id: str | None,
    after_node_id: str | None,
    text: str,
    role: str = "clause",
    before_node_id: str | None = None,
) -> StoredNode:
    if role not in _VALID_ROLES:
        raise InvalidRole(role)
    if after_node_id is not None and before_node_id is not None:
        raise ConflictingAnchors(after_node_id)

    if parent_id is not None:
        parent = await conn.fetchrow(_FETCH_NODE, parent_id, contract_id)
        if parent is None:
            raise ParentNotFound(parent_id)

    siblings = await conn.fetch(FETCH_SIBLINGS, contract_id, parent_id)

    after = None
    if after_node_id is not None:
        after = await conn.fetchrow(_FETCH_NODE, after_node_id, contract_id)
        if after is None:
            raise AfterNodeNotFound(after_node_id)
        if norm(after["parent_id"]) != norm(parent_id):
            raise BadPlacement(after_node_id)

    before = None
    if before_node_id is not None:
        before = await conn.fetchrow(_FETCH_NODE, before_node_id, contract_id)
        if before is None:
            raise BeforeNodeNotFound(before_node_id)
        if norm(before["parent_id"]) != norm(parent_id):
            raise BadPlacement(before_node_id)

    respace, order_index = compute_order_index(siblings, after, before)

    async with conn.transaction():
        if respace:
            await respace_siblings(conn, contract_id, parent_id, siblings)
            order_index = recompute_after_respace(siblings, after_node_id, before_node_id)

        new_id = str(
            await conn.fetchval(_INSERT_NODE, contract_id, parent_id, order_index, role, text)
        )
        await conn.execute(_INSERT_VERSION, new_id, text, _VERSION_ACTOR)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_NODE_CREATED,
                entity_type="node",
                entity_id=new_id,
                actor=get_settings().operator_actor,
                payload={"parent_id": parent_id, "role": role},
            ),
        )

    created = await conn.fetchrow(_FETCH_NODE, new_id, contract_id)
    return _to_stored_node(created)
