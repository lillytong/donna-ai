"""Direct inline edit of a node's text (F08) — asyncpg.

The operator edits a clause's prose in place (no issue raised). One transaction:
update the node's content field, append a `node_versions` row (the before/after
that later renders as a tracked change in redline, DD-13), and record an audit
event. The edit-eligibility and no-op rules are business logic and live here; the
route stays thin.

Editing text never renumbers: clause numbers are DERIVED from tree position
(DD-02). order_index / parent_id / role are untouched on this path — no
structural ripple.

The editable field is `body` when present, else `heading` (the two source text
fields). `plain_text` is a derived projection and is NEVER an edit target;
`table_data` / `file_reference` nodes are not inline-editable via this endpoint.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_NODE_EDITED, AuditEvent
from backend.models.imports import StoredNode
from backend.services.audit_repo import record_event
from backend.services.contract_repo import _to_stored_node

# Two distinct actor vocabularies. node_versions.actor is CHECK-constrained to
# ('user','ai','principal') — content authorship — so the version row records 'user'.
# audit_log.actor is the operator identity shared across all operator-initiated events
# (issues, etc.): settings.operator_actor, so "all operator actions" queries catch edits.
_VERSION_ACTOR = "user"


class NodeNotFound(Exception):
    """Node missing, soft-deleted, or not in the given contract."""


class NodeNotEditable(Exception):
    """Node has no inline-editable prose field (table/attachment node, or a node
    carrying only a derived/structured projection and no body/heading)."""


_FETCH_NODE = """
SELECT id, parent_id, order_index, content_type, heading, body, table_data,
       plain_text, role, has_placeholder, enumerator_format
FROM nodes
WHERE id = $1 AND contract_id = $2 AND is_deleted = false
"""

_UPDATE_BODY = "UPDATE nodes SET body = $2, updated_at = now() WHERE id = $1"
_UPDATE_HEADING = "UPDATE nodes SET heading = $2, updated_at = now() WHERE id = $1"

# snapshot_id stays NULL until the next snapshot is cut (schema note).
_INSERT_VERSION = """
INSERT INTO node_versions (node_id, snapshot_id, body_before, body_after, actor)
VALUES ($1, NULL, $2, $3, $4)
"""


async def edit_node(conn: Any, contract_id: str, node_id: str, text: str) -> StoredNode:
    record = await conn.fetchrow(_FETCH_NODE, node_id, contract_id)
    if record is None:
        raise NodeNotFound(node_id)

    if record["content_type"] != "prose":
        raise NodeNotEditable(node_id)

    if record["body"] is not None:
        field, sql = "body", _UPDATE_BODY
        before = record["body"]
    elif record["heading"] is not None:
        field, sql = "heading", _UPDATE_HEADING
        before = record["heading"]
    else:
        raise NodeNotEditable(node_id)

    if before == text:
        return _to_stored_node(record)  # no-op: no version, no audit (no noise)

    async with conn.transaction():
        await conn.execute(sql, node_id, text)
        await conn.execute(_INSERT_VERSION, node_id, before, text, _VERSION_ACTOR)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_NODE_EDITED,
                entity_type="node",
                entity_id=node_id,
                actor=get_settings().operator_actor,
                payload={"field": field},
            ),
        )

    updated = await conn.fetchrow(_FETCH_NODE, node_id, contract_id)
    return _to_stored_node(updated)
