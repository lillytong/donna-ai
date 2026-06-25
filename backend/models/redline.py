"""Redline export models (F15, DD-13/DD-51/DD-61).

The redline diffs the current working copy against a baseline snapshot. The change
set is reconstructed from `node_versions` (DD-13): per node, the net `body_before`
→ `body_after` across every version row stamped after the baseline (plus the
pending, still-unsnapshotted group). A node collapses to exactly one of:

- `inserted`  — first row's `body_before` is NULL (the node did not exist at baseline).
- `deleted`   — last row's `body_after` is NULL and the node is now soft-deleted.
- `edited`    — both present and the net text changed.

Deletions are rendered struck IN their baseline position, so they carry the
position fields (parent_id / order_index) read from the baseline snapshot tree;
live nodes keep their current position and are looked up by id.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

RedlineChangeType = Literal["edited", "inserted"]
MoveKind = Literal["reparent", "reorder"]


class RedlineExportRequest(BaseModel):
    """Body of POST /contracts/{id}/redline-export. `snapshot_id` null (default)
    diffs against the `last_shared_with_counterparty` pointer (DD-48/DD-61); a
    value overrides the baseline to an explicit snapshot."""

    snapshot_id: str | None = None


class NodeDiff(BaseModel):
    """A net change on a still-live node — rendered with inline tracked changes.

    `inserted`: `text_after` only (fully inserted). `edited`: `text_before` struck
    + `text_after` inserted."""

    node_id: str
    change_type: RedlineChangeType
    text_before: str | None = None
    text_after: str | None = None


class DeletedNode(BaseModel):
    """A node present at baseline but deleted after it. Woven back into document
    order at its baseline position and rendered fully struck (`w:del`).

    `content_type == "table"` marks a table delete reconstructed from the STRUCTURAL
    baseline (snapshot tree vs current tree, DD-03), not from `node_versions` —
    tables are not inline-editable so they never produce a version row. The snapshot
    tree stores no `table_data`, so a deleted table is struck as an empty table at
    its baseline position (the one flagged fidelity gap; see render_redline)."""

    id: str
    parent_id: str | None
    order_index: int
    content_type: str
    text: str


class MovedNode(BaseModel):
    """A node present at baseline AND still live but relocated — reparented or
    reordered relative to its surviving siblings (DD-13 move). Moves write no
    `node_versions` row, so they are recovered by the STRUCTURAL diff (snapshot tree
    vs current tree), not the prose diff.

    Rendered as the del+ins move fallback (Word `w:move*` markup is not emitted —
    see render_redline): `baseline_text` struck at the baseline position
    (moved-from) + `current_text` inserted at the current position (moved-to). When
    the node was ALSO edited, the two texts differ, so the text change shows
    alongside the move — this is the edited+moved reconciliation (move takes
    precedence over the inline edit; the edit is carried in the two texts).

    A pure-renumber shift (number changed only because siblings were inserted or
    deleted, with no real relocation) is NOT a move and is excluded upstream
    (DD-03 renumber suppression)."""

    id: str
    baseline_parent_id: str | None
    baseline_order_index: int
    content_type: str
    baseline_text: str
    current_text: str
    table_data: list[list[str]] | None = None
    move_kind: MoveKind
