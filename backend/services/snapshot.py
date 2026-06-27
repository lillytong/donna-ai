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
from backend.models.imports import ContractTreeResponse, StoredNode
from backend.models.lineage import PointerRow
from backend.models.snapshots import (
    CutSnapshotRequest,
    SnapshotNode,
    SnapshotPointerTarget,
    StoredSnapshot,
)
from backend.services.audit_repo import record_event

_FETCH_TREE = """
SELECT id, parent_id, order_index, content_type, heading, body, is_deleted
FROM nodes
WHERE contract_id = $1
ORDER BY order_index
"""

# version_number is minted atomically inside the INSERT (DD-87 §1): the next value
# per contract = COALESCE(MAX(version_number),0)+1, never reused, so a version-delete
# leaves a preserved gap. Serves BOTH insert paths (cut_snapshot, snapshot_tree).
_INSERT_SNAPSHOT = """
INSERT INTO contract_snapshots (contract_id, label, tree, origin, version_number)
SELECT $1, $2, $3::jsonb, $4, COALESCE(MAX(version_number), 0) + 1
FROM contract_snapshots
WHERE contract_id = $1
RETURNING id, contract_id, label, origin, created_at, version_number
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
SELECT id, contract_id, label, origin, created_at, version_number
FROM contract_snapshots
WHERE contract_id = $1
ORDER BY created_at DESC
"""

_FETCH_SNAPSHOT = """
SELECT id, contract_id, label, tree, origin, created_at, version_number
FROM contract_snapshots
WHERE id = $1
"""

# Numbered timeline (DD-70/DD-85): v-numbers read the PERSISTED `version_number`
# column (DD-87 §1) — never ROW_NUMBER, which would renumber survivors after a
# version-delete and destroy the required gap. Ordered by the number itself (a
# deleted version simply leaves a hole). Heavy `tree` JSONB is omitted.
_LIST_NUMBERED = """
SELECT id, contract_id, label, origin, created_at, version_number
FROM contract_snapshots
WHERE contract_id = $1
ORDER BY version_number
"""

_LIST_POINTERS = """
SELECT party, direction, snapshot_id
FROM snapshot_pointers
WHERE contract_id = $1
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
    version_number = record.get("version_number") if hasattr(record, "get") else None
    return StoredSnapshot(
        id=str(record["id"]),
        contract_id=str(record["contract_id"]),
        label=record["label"],
        origin=record["origin"],
        created_at=record["created_at"],
        tree=tree,
        version_number=int(version_number) if version_number is not None else None,
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


async def snapshot_tree(
    conn: Any,
    contract_id: str,
    tree: list[SnapshotNode],
    *,
    origin: str,
    label: str | None = None,
    pointer: SnapshotPointerTarget | None = None,
) -> StoredSnapshot:
    """Snapshot an ARBITRARY caller-supplied tree (additive sibling of `cut_snapshot`).

    `cut_snapshot` dumps the LIVE node tree; the Mode B revision-import path (F03b)
    needs to freeze the PARSED INCOMING tree instead (origin='as_received', DD-48) —
    which is not in the `nodes` table. So this writes the given JSONB dump directly
    and does NOT stamp `node_versions` (there is no live edit group to close — the
    received copy is not the working copy). Optionally advances one named pointer
    (the F03b `received` pointer). Records the same `snapshot_cut` audit event."""
    tree_json = json.dumps([n.model_dump() for n in tree])
    async with conn.transaction():
        snapshot_record = await conn.fetchrow(
            _INSERT_SNAPSHOT, contract_id, label, tree_json, origin
        )
        snapshot_id = str(snapshot_record["id"])

        if pointer is not None:
            await conn.execute(
                _UPSERT_POINTER, contract_id, pointer.party, pointer.direction, snapshot_id
            )

        payload: dict[str, Any] = {"snapshot_id": snapshot_id, "origin": origin}
        if pointer is not None:
            payload["pointer"] = pointer.model_dump()
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


async def set_pointer(
    conn: Any, contract_id: str, target: SnapshotPointerTarget, snapshot_id: str
) -> None:
    """Advance one of the four named DD-48 pointers to an existing snapshot.

    Exposed for Mark-as-sent (DD-71), which cuts ONE snapshot and may advance TWO
    pointers (recipient='both' → counterparty + legal_team), so the pointer move is
    decoupled from `cut_snapshot`'s single-pointer convenience path."""
    await conn.execute(_UPSERT_POINTER, contract_id, target.party, target.direction, snapshot_id)


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


async def list_numbered_snapshots(conn: Any, contract_id: str) -> list[tuple[int, StoredSnapshot]]:
    """All snapshots in v-number order, each paired with its PERSISTED lineage
    v-number (DD-87 §1; never ROW_NUMBER, so a deleted version leaves a gap). Tree
    omitted (list view). Lineage assembly consumes this."""
    records = await conn.fetch(_LIST_NUMBERED, contract_id)
    return [(int(r["version_number"]), _to_stored_snapshot(r, tree=None)) for r in records]


async def list_pointers(conn: Any, contract_id: str) -> list[PointerRow]:
    """The DD-48 named pointers currently set for the contract (party × direction →
    snapshot). At most four rows; in v1 only the `shared` pointers are ever set."""
    records = await conn.fetch(_LIST_POINTERS, contract_id)
    return [
        PointerRow(party=r["party"], direction=r["direction"], snapshot_id=str(r["snapshot_id"]))
        for r in records
    ]


async def get_snapshot_tree(
    conn: Any, contract_id: str, snapshot_id: str
) -> ContractTreeResponse | None:
    """Read-only render adapter (F27): rebuild the nested, render-ready node tree
    from a snapshot's frozen JSONB dump — the SAME shape the cockpit renders live
    nodes from (`ContractTreeResponse`), so the frontend can render a historical
    version read-only with the existing tree component. Soft-deleted nodes are
    dropped (the live tree excludes them too). Returns None if the snapshot is
    missing or belongs to a different contract.

    Note: the stored snapshot shape (DD/schema) carries no `role`/`table_data`/
    `has_placeholder`, so those fall back to the StoredNode defaults — the historical
    render is structurally faithful (topology + headings + bodies) but does not
    reconstruct front-matter roles. Sufficient for a read-only lineage view."""
    snapshot = await get_snapshot(conn, snapshot_id)
    if snapshot is None or snapshot.contract_id != contract_id:
        return None
    rows = [
        StoredNode(
            id=n.id,
            parent_id=n.parent_id,
            order_index=n.order_index,
            content_type=n.content_type,
            heading=n.heading,
            body=n.body,
        )
        for n in (snapshot.tree or [])
        if not n.is_deleted
    ]
    return ContractTreeResponse.from_rows(contract_id, rows)
