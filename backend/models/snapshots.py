"""Snapshots (F14) — immutable point-in-time captures of a contract (DD-09, DD-48).

A snapshot stores the FULL node tree (topology + bodies, including soft-deleted
nodes) as a JSONB dump so structural diffs (DD-03) can reconstruct insert/delete/
move, which `node_versions` does not record. Cutting a snapshot also groups every
pending `node_versions` row (those still `snapshot_id IS NULL`) under the new
snapshot — that group is exactly the body-change set the F15 redline renders.

`origin` and the pointer `party`/`direction` mirror the schema CHECK constraints
verbatim. The four named pointers (DD-48) are the per-source diff baselines: the
"send to counterparty" case is `party='counterparty', direction='shared'`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SnapshotOrigin = Literal["export", "as_received", "manual"]
PointerParty = Literal["counterparty", "legal_team", "internal"]
PointerDirection = Literal["shared", "received"]


class SnapshotNode(BaseModel):
    """One entry in a snapshot's frozen tree dump (schema's documented JSONB shape).

    Soft-deleted nodes are retained (`is_deleted`) so the diff can reconstruct
    deletions; clause numbers are derived from position (DD-02), never stored."""

    id: str
    parent_id: str | None
    order_index: int
    content_type: str
    heading: str | None
    body: str | None
    is_deleted: bool


class SnapshotPointerTarget(BaseModel):
    """Which of the four named pointers (DD-48) this cut should advance, if any.

    `direction='shared'` is the export/send case (F14): doubles as the diff
    baseline for that party's next inbound revision (DD-47)."""

    party: PointerParty
    direction: PointerDirection


class CutSnapshotRequest(BaseModel):
    label: str | None = None
    origin: SnapshotOrigin = "export"
    pointer: SnapshotPointerTarget | None = None


class StoredSnapshot(BaseModel):
    """A snapshot row read back. `tree` is the heavy JSONB dump — populated on
    single-snapshot fetch, omitted (None) on list reads to stay light."""

    id: str
    contract_id: str
    label: str | None
    origin: SnapshotOrigin
    created_at: datetime
    tree: list[SnapshotNode] | None = None
