"""Snapshot service (F14) — cut and read immutable contract captures (asyncpg).

Cutting a snapshot, in one transaction:
  1. Dump the contract's FULL node tree (incl. soft-deleted) into `contract_snapshots`.
  2. Stamp every pending `node_versions` row for the contract (`snapshot_id IS NULL`)
     with the new snapshot's id — this groups "all body edits since the last
     snapshot" under it. That group is exactly the change set the F15 redline diffs.
  3. Optionally advance one of the four named pointers (DD-48) to the new snapshot
     (the "send to counterparty" case sets party='counterparty', direction='shared').
  4. Record a `snapshot_cut` audit event (actor = operator_actor, per the issue/edit
     convention; node_versions content-authorship actors are a separate vocabulary).

Prerequisite for F15 (tracked-changes redline) alongside the renderer.
"""

from __future__ import annotations

import json
from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_SNAPSHOT_CUT, AuditEvent
from backend.models.snapshots import CutSnapshotRequest, SnapshotNode, StoredSnapshot
from backend.services.audit_repo import record_event

_FETCH_TREE = """
SELECT id, parent_id, order_index, content_type, heading, body, is_deleted
FROM nodes
WHERE contract_id = $1
ORDER BY order_index
"""

_INSERT_SNAPSHOT = """
INSERT INTO contract_snapshots (contract_id, label, tree, origin)
VALUES ($1, $2, $3::jsonb, $4)
RETURNING id, contract_id, label, origin, created_at
"""

# Groups the pending (unsnapshotted) body edits under this snapshot. node_versions
# has no contract_id, so the contract scope is resolved via its node_id.
_STAMP_VERSIONS = """
UPDATE node_versions
SET snapshot_id = $1
WHERE snapshot_id IS NULL
  AND node_id IN (SELECT id FROM nodes WHERE contract_id = $2)
"""

_UPSERT_POINTER = """
INSERT INTO snapshot_pointers (contract_id, party, direction, snapshot_id)
VALUES ($1, $2, $3, $4)
ON CONFLICT (contract_id, party, direction)
DO UPDATE SET snapshot_id = EXCLUDED.snapshot_id, set_at = now()
"""

_LIST_SNAPSHOTS = """
SELECT id, contract_id, label, origin, created_at
FROM contract_snapshots
WHERE contract_id = $1
ORDER BY created_at DESC
"""

_FETCH_SNAPSHOT = """
SELECT id, contract_id, label, tree, origin, created_at
FROM contract_snapshots
WHERE id = $1
"""


def _to_snapshot_node(record: Any) -> SnapshotNode:
    parent_id = record["parent_id"]
    return SnapshotNode(
        id=str(record["id"]),
        parent_id=str(parent_id) if parent_id is not None else None,
        order_index=record["order_index"],
        content_type=record["content_type"],
        heading=record["heading"],
        body=record["body"],
        is_deleted=record["is_deleted"],
    )


def _to_stored_snapshot(record: Any, *, tree: list[SnapshotNode] | None) -> StoredSnapshot:
    return StoredSnapshot(
        id=str(record["id"]),
        contract_id=str(record["contract_id"]),
        label=record["label"],
        origin=record["origin"],
        created_at=record["created_at"],
        tree=tree,
    )


async def cut_snapshot(conn: Any, contract_id: str, request: CutSnapshotRequest) -> StoredSnapshot:
    tree_records = await conn.fetch(_FETCH_TREE, contract_id)
    tree = [_to_snapshot_node(r) for r in tree_records]
    tree_json = json.dumps([n.model_dump() for n in tree])

    async with conn.transaction():
        snapshot_record = await conn.fetchrow(
            _INSERT_SNAPSHOT, contract_id, request.label, tree_json, request.origin
        )
        snapshot_id = str(snapshot_record["id"])

        await conn.execute(_STAMP_VERSIONS, snapshot_id, contract_id)

        if request.pointer is not None:
            await conn.execute(
                _UPSERT_POINTER,
                contract_id,
                request.pointer.party,
                request.pointer.direction,
                snapshot_id,
            )

        payload: dict[str, Any] = {"snapshot_id": snapshot_id, "origin": request.origin}
        if request.pointer is not None:
            payload["pointer"] = request.pointer.model_dump()
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_SNAPSHOT_CUT,
                entity_type="contract",
                entity_id=contract_id,
                actor=get_settings().operator_actor,
                payload=payload,
            ),
        )

    return _to_stored_snapshot(snapshot_record, tree=tree)


async def list_snapshots(conn: Any, contract_id: str) -> list[StoredSnapshot]:
    records = await conn.fetch(_LIST_SNAPSHOTS, contract_id)
    return [_to_stored_snapshot(r, tree=None) for r in records]


async def get_snapshot(conn: Any, snapshot_id: str) -> StoredSnapshot | None:
    record = await conn.fetchrow(_FETCH_SNAPSHOT, snapshot_id)
    if record is None:
        return None
    raw_tree = record["tree"]
    if isinstance(raw_tree, str):
        raw_tree = json.loads(raw_tree)
    tree = [SnapshotNode.model_validate(n) for n in raw_tree]
    return _to_stored_snapshot(record, tree=tree)
