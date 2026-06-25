"""Reposition a node (reorder + reparent) to back a drag-and-drop tree UI
(cockpit, SPEC §9) — asyncpg.

A move is a STRUCTURAL change, not a content change: it touches only the moved
node's `parent_id` + `order_index`, never any text field. So — unlike F08 edit /
F08b create / clause delete — it writes an audit event but NO `node_versions` row.
`node_versions` captures body_before/after text deltas (DD-13 redline); a move has
no text delta, so there is nothing for it to record.

The sub-tree rides along for free: descendants reference the moved node via
`parent_id`, so re-pointing only the moved node carries its whole sub-tree with it.
Depth/clause numbers are DERIVED from tree position (DD-02), so they re-derive at
the new location — no renumber here.

CYCLE SAFETY: a node may not move into itself or any of its own descendants (that
would orphan the sub-tree into a cycle). The descendant set is walked via a
recursive CTE over the `parent_id` edge before any write; an offending target →
`InvalidMove`.

Placement (anchor → order_index, gap-based with the OQ-07 respace fallback, and the
NULL-aware sibling fetch) is shared with `node_create` via `node_placement`.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_NODE_MOVED, AuditEvent
from backend.models.nodes import NodeMoveResponse
from backend.services.audit_repo import record_event
from backend.services.node_placement import (
    FETCH_SIBLINGS,
    compute_order_index,
    norm,
    position_of,
    recompute_after_respace,
    respace_siblings,
)

# A temp slot outside the live range, used to vacate the moved node's order_index
# before any sibling respace so no per-row UPDATE transiently breaks the
# UNIQUE (contract_id, parent_id, order_index) constraint. Live order_index values
# are gap-based positives (>= 100), so a negative is guaranteed free.
_TEMP_ORDER_INDEX = -1


class NodeNotFound(Exception):
    """Node missing, soft-deleted, or not in the given contract."""


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


class InvalidMove(Exception):
    """parent_id is the node itself or one of its descendants — would cycle the tree."""


_FETCH_NODE = """
SELECT id, parent_id, order_index
FROM nodes
WHERE id = $1 AND contract_id = $2 AND is_deleted = false
"""

# Recursive walk of the parent_id edge over the contract's non-deleted nodes: base
# row is the scoped target, then every descendant. Returns descendants only (the
# target id is excluded) — the set a new parent_id must avoid to keep the tree acyclic.
_FETCH_DESCENDANTS = """
WITH RECURSIVE subtree AS (
    SELECT id FROM nodes
    WHERE id = $1 AND contract_id = $2 AND is_deleted = false
    UNION ALL
    SELECT n.id FROM nodes n
    JOIN subtree s ON n.parent_id = s.id
    WHERE n.contract_id = $2 AND n.is_deleted = false
)
SELECT id FROM subtree WHERE id <> $1
"""

_SET_ORDER = "UPDATE nodes SET order_index = $2, updated_at = now() WHERE id = $1"
_SET_PARENT_ORDER = (
    "UPDATE nodes SET parent_id = $2, order_index = $3, updated_at = now() WHERE id = $1"
)


def _is_noop(
    full_siblings: list[Any],
    node_id: str,
    after_node_id: str | None,
    before_node_id: str | None,
) -> bool:
    """True when the node already sits at exactly the requested position under its
    current parent. `full_siblings` INCLUDES the node (current order)."""
    cur = position_of(full_siblings, node_id)
    if after_node_id is not None:
        return cur > 0 and norm(full_siblings[cur - 1]["id"]) == norm(after_node_id)
    if before_node_id is not None:
        return cur < len(full_siblings) - 1 and norm(full_siblings[cur + 1]["id"]) == norm(
            before_node_id
        )
    return cur == len(full_siblings) - 1  # append: already the last child


async def move_node(
    conn: Any,
    contract_id: str,
    node_id: str,
    parent_id: str | None,
    after_node_id: str | None,
    before_node_id: str | None,
) -> NodeMoveResponse:
    if after_node_id is not None and before_node_id is not None:
        raise ConflictingAnchors(node_id)

    target = await conn.fetchrow(_FETCH_NODE, node_id, contract_id)
    if target is None:
        raise NodeNotFound(node_id)

    if parent_id is not None:
        parent = await conn.fetchrow(_FETCH_NODE, parent_id, contract_id)
        if parent is None:
            raise ParentNotFound(parent_id)

    if norm(parent_id) == norm(node_id):
        raise InvalidMove(node_id)
    if parent_id is not None:
        descendants = await conn.fetch(_FETCH_DESCENDANTS, node_id, contract_id)
        if any(norm(r["id"]) == norm(parent_id) for r in descendants):
            raise InvalidMove(parent_id)

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

    full_siblings = await conn.fetch(FETCH_SIBLINGS, contract_id, parent_id)
    same_parent = norm(target["parent_id"]) == norm(parent_id)

    if same_parent and _is_noop(full_siblings, node_id, after_node_id, before_node_id):
        return NodeMoveResponse(moved=False, node_id=node_id, parent_id=norm(parent_id))

    # Destination siblings must EXCLUDE the moved node so the gap-based placement
    # (and any respace) reasons over the other children only.
    siblings = [s for s in full_siblings if norm(s["id"]) != norm(node_id)]
    respace, order_index = compute_order_index(siblings, after, before)

    async with conn.transaction():
        # Vacate the moved node's current slot first so a same-parent respace can't
        # transiently collide on UNIQUE (contract_id, parent_id, order_index).
        await conn.execute(_SET_ORDER, node_id, _TEMP_ORDER_INDEX)
        if respace:
            await respace_siblings(conn, contract_id, parent_id, siblings)
            order_index = recompute_after_respace(siblings, after_node_id, before_node_id)
        await conn.execute(_SET_PARENT_ORDER, node_id, parent_id, order_index)
        # No node_versions row: a move is structure-only (parent_id/order_index), it
        # carries no body_before/after text delta for redline to record.
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_NODE_MOVED,
                entity_type="node",
                entity_id=node_id,
                actor=get_settings().operator_actor,
                payload={
                    "parent_id": norm(parent_id),
                    "anchor": {"after": norm(after_node_id), "before": norm(before_node_id)},
                },
            ),
        )

    return NodeMoveResponse(moved=True, node_id=node_id, parent_id=norm(parent_id))
