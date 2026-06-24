"""Create a new node (clause/section) mid-negotiation (F08b) — asyncpg.

The operator adds a clause on the fly. It anchors to a parent and lands at a
computed `order_index`; its displayed clause number is DERIVED from tree position
(DD-02), so the backend only fixes `parent_id` + `order_index` — never a number.
The `node_versions` insertion row written here (`body_before=NULL`, `body_after`
= the new text) is what later renders as a tracked insertion in the next redline
(SPEC §5 F08b).

One transaction: any sibling re-space (the OQ-07 no-gap fallback), the node
INSERT, the version-row INSERT, and the audit event commit together.

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

# _ORDER_GAP (=100) is reused from the import spine so on-the-fly inserts share the
# same leave-room-between-siblings spacing convention as imported trees (OQ-07).
from backend.services.import_.tree_builder import _ORDER_GAP

_VERSION_ACTOR = "user"
_VALID_ROLES: frozenset[str] = frozenset(get_args(Role))

# A bump larger than any plausible final order_index, used to vacate the low
# range before re-spacing so per-row UPDATEs can't transiently break the
# UNIQUE (contract_id, parent_id, order_index) constraint mid-renumber.
_RESPACE_OFFSET = 1_000_000


class ParentNotFound(Exception):
    """parent_id given but missing, soft-deleted, or not in this contract."""


class AfterNodeNotFound(Exception):
    """after_node_id given but missing, soft-deleted, or not in this contract."""


class BadPlacement(Exception):
    """after_node exists but is not a child of parent_id (placement mismatch)."""


class InvalidRole(Exception):
    """role is outside the schema CHECK list."""


_FETCH_NODE = """
SELECT id, parent_id, order_index, content_type, heading, body, table_data,
       plain_text, role, has_placeholder
FROM nodes
WHERE id = $1 AND contract_id = $2 AND is_deleted = false
"""

# parent_id IS NOT DISTINCT FROM $2 treats NULL (root) as a value, so root-level
# siblings are matched alongside nested ones.
_FETCH_SIBLINGS = """
SELECT id, order_index
FROM nodes
WHERE contract_id = $1 AND parent_id IS NOT DISTINCT FROM $2 AND is_deleted = false
ORDER BY order_index
"""

_BUMP_SIBLINGS = """
UPDATE nodes SET order_index = order_index + $3, updated_at = now()
WHERE contract_id = $1 AND parent_id IS NOT DISTINCT FROM $2 AND is_deleted = false
"""

_SET_ORDER = "UPDATE nodes SET order_index = $2, updated_at = now() WHERE id = $1"

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


def _norm(value: Any) -> str | None:
    return str(value) if value is not None else None


async def create_node(
    conn: Any,
    contract_id: str,
    parent_id: str | None,
    after_node_id: str | None,
    text: str,
    role: str = "clause",
) -> StoredNode:
    if role not in _VALID_ROLES:
        raise InvalidRole(role)

    if parent_id is not None:
        parent = await conn.fetchrow(_FETCH_NODE, parent_id, contract_id)
        if parent is None:
            raise ParentNotFound(parent_id)

    siblings = await conn.fetch(_FETCH_SIBLINGS, contract_id, parent_id)

    after = None
    if after_node_id is not None:
        after = await conn.fetchrow(_FETCH_NODE, after_node_id, contract_id)
        if after is None:
            raise AfterNodeNotFound(after_node_id)
        if _norm(after["parent_id"]) != _norm(parent_id):
            raise BadPlacement(after_node_id)

    respace = False
    order_index = _ORDER_GAP
    if after is None:
        # Append: one gap past the last sibling, or the first slot if empty.
        if siblings:
            order_index = max(s["order_index"] for s in siblings) + _ORDER_GAP
        else:
            order_index = _ORDER_GAP
    else:
        after_idx = after["order_index"]
        higher = [s["order_index"] for s in siblings if s["order_index"] > after_idx]
        if not higher:
            order_index = after_idx + _ORDER_GAP
        else:
            next_idx = min(higher)
            midpoint = (after_idx + next_idx) // 2
            if midpoint == after_idx:
                # OQ-07 no-gap fallback: adjacent integers leave no room. Re-space
                # all siblings to 100,200,300… (done in-transaction below), then
                # place the new node in the opened gap. Renumbering order_index
                # does NOT change derived clause numbers — those come from tree
                # position, which the stable sibling order preserves.
                respace = True
            else:
                order_index = midpoint

    async with conn.transaction():
        if respace:
            await conn.execute(_BUMP_SIBLINGS, contract_id, parent_id, _RESPACE_OFFSET)
            for i, sibling in enumerate(siblings):
                await conn.execute(_SET_ORDER, sibling["id"], (i + 1) * _ORDER_GAP)
            position = next(
                i for i, s in enumerate(siblings) if _norm(s["id"]) == _norm(after_node_id)
            )
            order_index = (position + 1) * _ORDER_GAP + _ORDER_GAP // 2

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
