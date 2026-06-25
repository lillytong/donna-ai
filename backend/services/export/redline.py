"""Redline export orchestration (F15, DD-13/DD-48/DD-61).

Resolves the diff baseline, reconstructs the change set from `node_versions`, and
hands the diff to the tracked-changes renderer. Business logic (baseline
resolution + diff collapse/classify); the route stays thin.

Baseline (DD-61): default = the snapshot under the `last_shared_with_counterparty`
pointer (party='counterparty', direction='shared', DD-48); an explicit
`snapshot_id` overrides it. No such snapshot → the redline is unavailable
(`NoBaselineSnapshot`); an unknown override → `BaselineNotFound`.

Change set (DD-13): every `node_versions` row stamped under a snapshot cut AFTER
the baseline (by `created_at`), plus the pending still-NULL group. Per node these
collapse to one net before→after (append-only history is contiguous, so the first
post-baseline row's `body_before` is the baseline text and the last row's
`body_after` is the current text).

Structural change set (DD-03): moves and table insert/delete write NO `node_versions`
row, so the prose diff above misses them. `_structural_diff` recovers them by diffing
the baseline snapshot's full-tree dump against the current node tree:
  - a node in BOTH trees with a changed parent (reparent) or a changed order among
    its surviving siblings (reorder) is a MOVE — distinguished from a pure-renumber
    shift (number changed only because a sibling was inserted/deleted), which is
    suppressed (DD-03). A move takes precedence over its inline prose edit: it is
    removed from the prose `diffs` and rendered as the del+ins move fallback, whose
    two texts carry any concurrent edit.
  - a table node present only in the current tree is an INSERT; present only in the
    baseline tree is a DELETE (the snapshot stores no `table_data`, so a deleted
    table is struck as an empty table — the one flagged fidelity gap).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from backend.config.settings import get_settings
from backend.models.imports import StoredNode
from backend.models.redline import DeletedNode, MovedNode, NodeDiff
from backend.models.snapshots import SnapshotNode, StoredSnapshot
from backend.services.contract_repo import fetch_nodes
from backend.services.export.render_redline import render_redline_docx
from backend.services.snapshot import get_snapshot


class NoBaselineSnapshot(Exception):
    """No `last_shared_with_counterparty` baseline exists — redline unavailable."""


class BaselineNotFound(Exception):
    """An explicit `snapshot_id` was given but is missing or not this contract's."""


# The default baseline (DD-48/DD-61): the snapshot the counterparty was last sent.
_POINTER_SNAPSHOT = """
SELECT cs.id, cs.created_at
FROM snapshot_pointers sp
JOIN contract_snapshots cs ON cs.id = sp.snapshot_id
WHERE sp.contract_id = $1 AND sp.party = 'counterparty' AND sp.direction = 'shared'
"""

# Every body change since the baseline: rows stamped under a later snapshot (by
# cut time) plus the pending NULL group. node_versions has no contract_id, so the
# scope is resolved via the node; the node row also gives current is_deleted.
_CHANGE_SET = """
SELECT nv.node_id, nv.body_before, nv.body_after, nv.created_at, n.is_deleted
FROM node_versions nv
JOIN nodes n ON n.id = nv.node_id
LEFT JOIN contract_snapshots cs ON cs.id = nv.snapshot_id
WHERE n.contract_id = $1
  AND (nv.snapshot_id IS NULL OR cs.created_at > $2)
ORDER BY nv.node_id, nv.created_at
"""


def _resolve_author() -> str:
    settings = get_settings()
    return settings.redline_author or settings.operator_actor


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _resolve_baseline(conn: Any, contract_id: str, snapshot_id: str | None) -> StoredSnapshot:
    if snapshot_id is not None:
        snapshot = await get_snapshot(conn, snapshot_id)
        if snapshot is None or snapshot.contract_id != contract_id:
            raise BaselineNotFound(snapshot_id)
        return snapshot

    row = await conn.fetchrow(_POINTER_SNAPSHOT, contract_id)
    if row is None:
        raise NoBaselineSnapshot(contract_id)
    snapshot = await get_snapshot(conn, str(row["id"]))
    if snapshot is None:
        raise NoBaselineSnapshot(contract_id)
    return snapshot


def _collapse(rows: list[Any]) -> dict[str, tuple[str | None, str | None, bool]]:
    """Per node: (net body_before, net body_after, current is_deleted)."""
    by_node: dict[str, list[Any]] = {}
    for row in rows:
        by_node.setdefault(str(row["node_id"]), []).append(row)
    collapsed: dict[str, tuple[str | None, str | None, bool]] = {}
    for node_id, node_rows in by_node.items():
        node_rows.sort(key=lambda r: r["created_at"])
        collapsed[node_id] = (
            node_rows[0]["body_before"],
            node_rows[-1]["body_after"],
            bool(node_rows[-1]["is_deleted"]),
        )
    return collapsed


def _classify(
    collapsed: dict[str, tuple[str | None, str | None, bool]],
    baseline_tree: list[SnapshotNode] | None,
) -> tuple[dict[str, NodeDiff], list[DeletedNode]]:
    baseline_by_id = {n.id: n for n in (baseline_tree or [])}
    diffs: dict[str, NodeDiff] = {}
    deleted: list[DeletedNode] = []

    for node_id, (before, after, _is_deleted) in collapsed.items():
        if before is None and after is None:
            continue  # inserted then deleted since baseline — never in the baseline, gone now
        if after is None:
            base = baseline_by_id.get(node_id)
            if base is None:
                continue  # not present at baseline → nothing to strike
            text = before if before is not None else (base.body or base.heading or "")
            if not text:
                continue
            deleted.append(
                DeletedNode(
                    id=node_id,
                    parent_id=base.parent_id,
                    order_index=base.order_index,
                    content_type=base.content_type,
                    text=text,
                )
            )
        elif before is None:
            if not after:
                continue
            diffs[node_id] = NodeDiff(node_id=node_id, change_type="inserted", text_after=after)
        elif before == after:
            continue  # net no-op (e.g. edited then reverted) — no markup
        else:
            diffs[node_id] = NodeDiff(
                node_id=node_id, change_type="edited", text_before=before, text_after=after
            )

    return diffs, deleted


def _norm_parent(parent_id: str | None, present: set[str]) -> str | None:
    """A parent absent from its own tree's node set (or null) is a root — mirrors
    `_group_children` / the renderer's weave so both sides compare on the same axis."""
    return parent_id if (parent_id is not None and parent_id in present) else None


def _lcs_keep(a: list[str], b: list[str]) -> set[str]:
    """Ids on a longest common subsequence of two sibling orderings. Ids NOT kept
    are the minimal set whose relative order changed — i.e. the reordered nodes."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    keep: set[str] = set()
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            keep.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return keep


def _heading_first(heading: str | None, body: str | None) -> str:
    """Text a node shows in the clean export (heading wins) — matches the renderer's
    `_render_unchanged`, so a moved node's struck/inserted text equals its rendering."""
    return heading if heading is not None else (body or "")


def _structural_diff(
    baseline_tree: list[SnapshotNode] | None, live_nodes: list[StoredNode]
) -> tuple[dict[str, MovedNode], set[str], list[DeletedNode]]:
    """Diff the baseline snapshot tree against the current node tree for changes the
    prose diff cannot see: moves (reparent/reorder, renumber-only excluded) and table
    insert/delete. Returns (moved by live id, inserted-table live ids, table deletes)."""
    base_by_id = {n.id: n for n in (baseline_tree or []) if not n.is_deleted}
    live_by_id = {n.id: n for n in live_nodes}
    base_ids = set(base_by_id)
    live_ids = set(live_by_id)

    inserted_tables = {
        n.id for n in live_nodes if n.content_type == "table" and n.id not in base_by_id
    }
    table_deletes = [
        DeletedNode(
            id=n.id,
            parent_id=_norm_parent(n.parent_id, base_ids),
            order_index=n.order_index,
            content_type="table",
            text="",
        )
        for n in (baseline_tree or [])
        if n.content_type == "table" and not n.is_deleted and n.id not in live_by_id
    ]

    common = base_ids & live_ids
    reparented = {
        nid
        for nid in common
        if _norm_parent(base_by_id[nid].parent_id, base_ids)
        != _norm_parent(live_by_id[nid].parent_id, live_ids)
    }

    groups: dict[str | None, list[str]] = {}
    for nid in common - reparented:
        groups.setdefault(_norm_parent(live_by_id[nid].parent_id, live_ids), []).append(nid)
    reordered: set[str] = set()
    for ids in groups.values():
        if len(ids) < 2:
            continue
        base_seq = sorted(ids, key=lambda i: (base_by_id[i].order_index, i))
        # tie-break the live order by the baseline order so an order_index TIE (no real
        # relocation) preserves baseline sequence and is not read as a reorder.
        live_seq = sorted(
            ids, key=lambda i: (live_by_id[i].order_index, base_by_id[i].order_index, i)
        )
        if base_seq == live_seq:
            continue
        keep = _lcs_keep(base_seq, live_seq)
        reordered.update(i for i in ids if i not in keep)

    moved: dict[str, MovedNode] = {}
    for nid in reparented | reordered:
        base = base_by_id[nid]
        live = live_by_id[nid]
        moved[nid] = MovedNode(
            id=nid,
            baseline_parent_id=_norm_parent(base.parent_id, base_ids),
            baseline_order_index=base.order_index,
            content_type=live.content_type,
            baseline_text=_heading_first(base.heading, base.body),
            current_text=_heading_first(live.heading, live.body),
            table_data=live.table_data,
            move_kind="reparent" if nid in reparented else "reorder",
        )
    return moved, inserted_tables, table_deletes


async def build_redline(
    conn: Any, contract_id: str, snapshot_id: str | None, style_config: dict[str, Any]
) -> bytes:
    baseline = await _resolve_baseline(conn, contract_id, snapshot_id)
    live_nodes = await fetch_nodes(conn, contract_id)
    rows = await conn.fetch(_CHANGE_SET, contract_id, baseline.created_at)
    collapsed = _collapse(rows)
    diffs, deleted = _classify(collapsed, baseline.tree)
    moved, inserted_tables, table_deletes = _structural_diff(baseline.tree, live_nodes)

    # Precedence: a moved node renders as a move (del-old@baseline + ins-new@current),
    # not as an inline prose edit — its concurrent edit lives in the two move texts.
    for nid in moved:
        diffs.pop(nid, None)
    deleted = deleted + table_deletes

    return await asyncio.to_thread(
        render_redline_docx,
        live_nodes,
        diffs,
        deleted,
        style_config,
        _resolve_author(),
        _now_iso(),
        moved,
        inserted_tables,
    )
