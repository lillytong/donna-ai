"""Soft-delete a node and its whole sub-tree mid-negotiation (cockpit ⋮ menu,
SPEC §9; DD-13 redline) — asyncpg.

Deleting a clause removes it from the live tree (`is_deleted = true`) and, per
node, records a `node_versions` deletion row (`body_before = <current text>`,
`body_after = NULL`) that later renders as a tracked deletion in redline. The
intent is "delete this clause" in full, so its sub-clauses go with it: the target
plus every descendant (walked over the contract's non-deleted nodes via a
recursive CTE on the `parent_id` tree edge) are deleted in one transaction.

No renumber: clause numbers are DERIVED from tree position (DD-02). Remaining
siblings keep their `order_index` — soft-deleting leaves their order untouched.

Two actor vocabularies, matching F08 (`node_edit`) / F08b (`node_create`):
`node_versions.actor` is CHECK-constrained to ('user','ai','principal') — content
authorship — so each deletion row records 'user'; the single audit event for the
operation uses the operator identity (`settings.operator_actor`).
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_NODE_DELETED, AuditEvent
from backend.services.audit_repo import record_event

_VERSION_ACTOR = "user"


class NodeNotFound(Exception):
    """Node missing, already soft-deleted, or not in the given contract."""


# Recursive walk of the parent_id tree edge over the contract's non-deleted nodes:
# the base row is the scoped target (so a missing/deleted/foreign target yields an
# empty set → NodeNotFound), then every descendant. Target lands first; ordering is
# otherwise breadth-driven, which is fine — deletion is set-wise, not positional.
_FETCH_SUBTREE = """
WITH RECURSIVE subtree AS (
    SELECT id, parent_id, body, heading
    FROM nodes
    WHERE id = $1 AND contract_id = $2 AND is_deleted = false
    UNION ALL
    SELECT n.id, n.parent_id, n.body, n.heading
    FROM nodes n
    JOIN subtree s ON n.parent_id = s.id
    WHERE n.contract_id = $2 AND n.is_deleted = false
)
SELECT id, body, heading FROM subtree
"""

_SOFT_DELETE = "UPDATE nodes SET is_deleted = true, deleted_at = now() WHERE id = $1"

# Deletion: body_after is NULL (text removed); snapshot_id NULL until the next cut.
_INSERT_VERSION = """
INSERT INTO node_versions (node_id, snapshot_id, body_before, body_after, actor)
VALUES ($1, NULL, $2, NULL, $3)
"""


async def delete_node(conn: Any, contract_id: str, node_id: str) -> list[str]:
    rows = await conn.fetch(_FETCH_SUBTREE, node_id, contract_id)
    if not rows:
        raise NodeNotFound(node_id)

    deleted_ids = [str(row["id"]) for row in rows]

    async with conn.transaction():
        for row in rows:
            before = row["body"] if row["body"] is not None else row["heading"]
            await conn.execute(_SOFT_DELETE, row["id"])
            await conn.execute(_INSERT_VERSION, row["id"], before, _VERSION_ACTOR)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_NODE_DELETED,
                entity_type="node",
                entity_id=node_id,
                actor=get_settings().operator_actor,
                payload={"deleted_ids": deleted_ids, "count": len(deleted_ids)},
            ),
        )

    return deleted_ids
