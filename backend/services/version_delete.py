"""Version-delete service (DD-85 / DD-87) — hard-wipe a lineage version, rolling the
working copy back when the LATEST version is deleted.

Position is typed off `version_number` vs `MAX(version_number)` (DD-87 §2):
  * latest  (target == max)         → roll the working copy back to the predecessor;
  * middle  (target  < max)         → remove only the version (preserved gap);
  * only    (no predecessor exists) → remove the version, pointers cleared.

The wipe is an FK-correct cascade to `contract_snapshots` (DD-87 §4), all in ONE
transaction so a partial wipe can never commit (mirrors the DD-63 contract-delete
discipline in `settings_repo.delete_contract`). Rollback is UPDATE-restore, never
re-insert: soft-delete keeps every snapshot node id live, so restoring the
predecessor's frozen tree is a set of `UPDATE nodes` plus a soft-delete of any node
added after it, with `updated_at = predecessor.created_at` so the DD-75 divergence
probe does not false-flag the freshly-restored content as "edited since sent".

`confirm=false` is a no-mutation preview (the DD-85 warnings); `confirm=true` executes.
Returns None when the snapshot is missing / not the contract's (route → 404).

When the target version anchors an OPEN revision review — as its baseline OR its
as_received (received) version — the delete CASCADE-DISCARDS that review in the same
transaction (DD-94, replaces the DD-87 §4d 409 guard), so no dangling `reviewing`
session is left behind a stale resume bar. The preview reports the discard (count +
already-decided) as a non-blocking warning (DD-85 pattern).
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_VERSION_DELETED, AuditEvent
from backend.models.version_delete import ReviewDiscard, SentRecord, SnapshotDeleteResponse
from backend.services.audit_repo import record_event
from backend.services.snapshot import get_snapshot

# schema pointer party → operator-facing side label (matches lineage._SIDE).
_SIDE = {"counterparty": "counterparty", "legal_team": "legal", "internal": "internal"}


_MAX_VERSION = """
SELECT MAX(version_number) FROM contract_snapshots WHERE contract_id = $1::uuid
"""

_PREDECESSOR = """
SELECT id, version_number, created_at
FROM contract_snapshots
WHERE contract_id = $1::uuid AND version_number < $2
ORDER BY version_number DESC
LIMIT 1
"""

_POINTERS_ON = """
SELECT party, direction
FROM snapshot_pointers
WHERE contract_id = $1::uuid AND snapshot_id = $2::uuid
"""

# OPEN revision sessions the target version anchors: deleting the baseline the review
# diffs against OR the as_received version it reviews invalidates the review (DD-94).
_DEPENDENT_SESSIONS = """
SELECT id, changes_count, changes_reviewed_count
FROM counterparty_revision_sessions
WHERE contract_id = $1::uuid
  AND status = 'reviewing'
  AND (baseline_snapshot_id = $2::uuid OR as_received_snapshot_id = $2::uuid)
"""

# Discard one dependent session, children-first (DD-94 §3, extends DD-87 §4 cascade).
_DISCARD_HUNKS = """
DELETE FROM counterparty_revision_hunks
WHERE change_id IN (
    SELECT id FROM counterparty_revision_changes WHERE session_id = $1::uuid
)
"""
_DISCARD_OVERRIDES = (
    "DELETE FROM counterparty_revision_node_overrides WHERE session_id = $1::uuid"
)
_NULL_ISSUE_SESSION = (
    "UPDATE issues SET counterparty_revision_session_id = NULL "
    "WHERE counterparty_revision_session_id = $1::uuid"
)
_DISCARD_CHANGES = "DELETE FROM counterparty_revision_changes WHERE session_id = $1::uuid"
_DISCARD_SESSION = "DELETE FROM counterparty_revision_sessions WHERE id = $1::uuid"

_RESTORE_NODE = """
UPDATE nodes
SET parent_id = $1::uuid, order_index = $2, content_type = $3, heading = $4,
    body = $5, is_deleted = $6, updated_at = $7
WHERE id = $8::uuid AND contract_id = $9::uuid
"""

_SOFT_DELETE_ADDED = """
UPDATE nodes
SET is_deleted = true, updated_at = $1
WHERE contract_id = $2::uuid AND is_deleted = false AND NOT (id = ANY($3::uuid[]))
"""

_DELETE_PENDING_VERSIONS = """
DELETE FROM node_versions
WHERE snapshot_id IS NULL
  AND node_id IN (SELECT id FROM nodes WHERE contract_id = $1::uuid)
"""

_DELETE_POINTERS = """
DELETE FROM snapshot_pointers WHERE contract_id = $1::uuid AND snapshot_id = $2::uuid
"""

_NULL_OPENED = (
    "UPDATE issues SET opened_in_snapshot_id = NULL WHERE opened_in_snapshot_id = $1::uuid"
)
_NULL_RESOLVED = (
    "UPDATE issues SET resolved_in_snapshot_id = NULL WHERE resolved_in_snapshot_id = $1::uuid"
)
_DELETE_TARGET_VERSIONS = "DELETE FROM node_versions WHERE snapshot_id = $1::uuid"
_DELETE_SNAPSHOT = "DELETE FROM contract_snapshots WHERE id = $1::uuid"


def _combined_side_label(parties: list[str]) -> str:
    sides = sorted({_SIDE.get(p, p) for p in parties})
    if sides == ["counterparty", "legal"]:
        return "counterparty & legal"
    return " & ".join(sides)


async def delete_version(
    conn: Any, contract_id: str, snapshot_id: str, *, confirm: bool
) -> SnapshotDeleteResponse | None:
    target = await get_snapshot(conn, snapshot_id)
    if target is None or target.contract_id != contract_id or target.version_number is None:
        return None
    target_version = target.version_number

    max_version = await conn.fetchval(_MAX_VERSION, contract_id)
    is_latest = max_version is not None and target_version == int(max_version)

    pred = await conn.fetchrow(_PREDECESSOR, contract_id, target_version)
    has_predecessor = pred is not None
    will_rollback = is_latest and has_predecessor
    pred_version = int(pred["version_number"]) if pred is not None else None

    # OPEN revision review(s) this version anchors (baseline OR as_received). Deleting the
    # version invalidates the review, so the delete cascade-discards it (DD-94) rather than
    # leaving a dangling `reviewing` session behind a stale resume bar. At most one open
    # session per contract in v1, but the find/discard handles any matched set.
    dependent_rows = await conn.fetch(_DEPENDENT_SESSIONS, contract_id, snapshot_id)
    dependent_session_ids = [str(r["id"]) for r in dependent_rows]
    review_discard: ReviewDiscard | None = None
    if dependent_rows:
        review_discard = ReviewDiscard(
            changes_count=sum(int(r["changes_count"]) for r in dependent_rows),
            reviewed=sum(int(r["changes_reviewed_count"]) for r in dependent_rows),
        )

    # The send this version carried (a `shared` pointer rests on it), for the warning.
    pointer_rows = await conn.fetch(_POINTERS_ON, contract_id, snapshot_id)
    shared_parties = [r["party"] for r in pointer_rows if r["direction"] == "shared"]
    sent_record: SentRecord | None = None
    if shared_parties:
        sent_record = SentRecord(
            party=_combined_side_label(shared_parties),
            date=target.created_at.date().isoformat(),
        )

    warnings: list[str] = []
    if will_rollback:
        warnings.append(
            "Rollback is destructive — deleting the latest version discards the working "
            "copy's current content and any unsent edits since "
            f"v{pred_version}."
        )
    if sent_record is not None:
        warnings.append(
            "Deleting a sent version erases the record of what was sent to "
            f"{sent_record.party} on {sent_record.date}. This version holds the redline "
            "baseline tag, so deleting it removes that tag — no earlier version inherits it."
        )
    if review_discard is not None:
        warnings.append(
            "This also discards the in-progress revision review — "
            f"{review_discard.changes_count} changes, {review_discard.reviewed} already decided."
        )

    rollback_to_version = pred_version if will_rollback else None

    if not confirm:
        return SnapshotDeleteResponse(
            deleted=False,
            snapshot_id=snapshot_id,
            version_number=target_version,
            is_latest=is_latest,
            will_rollback=will_rollback,
            rolled_back=False,
            rollback_to_version=rollback_to_version,
            sent_record=sent_record,
            review_discard=review_discard,
            warnings=warnings,
            pointers_removed=[],
        )

    pointers_removed = sorted({_SIDE.get(r["party"], r["party"]) for r in pointer_rows})

    async with conn.transaction():
        # (a) Latest-delete → restore the working copy to the predecessor (UPDATE-restore).
        if will_rollback and pred is not None:
            pred_snapshot = await get_snapshot(conn, str(pred["id"]))
            pred_created_at = pred["created_at"]
            pred_nodes = pred_snapshot.tree if pred_snapshot is not None else []
            pred_node_ids: list[str] = []
            for node in pred_nodes or []:
                pred_node_ids.append(node.id)
                await conn.execute(
                    _RESTORE_NODE,
                    node.parent_id,
                    node.order_index,
                    node.content_type,
                    node.heading,
                    node.body,
                    node.is_deleted,
                    pred_created_at,
                    node.id,
                    contract_id,
                )
            # Any live node added AFTER the predecessor (absent from its tree) → soft-delete.
            await conn.execute(_SOFT_DELETE_ADDED, pred_created_at, contract_id, pred_node_ids)
            # Discard the unsent edits the rollback throws away (DD-85).
            await conn.execute(_DELETE_PENDING_VERSIONS, contract_id)

        # (b) A pointer on the target carries that version's lifecycle tag (shared or
        # received); the version is gone, so DROP the tag — never roll it back to the
        # predecessor, which would wrongly re-tag it (DD-87 §4(b), amended 2026-06-27).
        await conn.execute(_DELETE_POINTERS, contract_id, snapshot_id)

        # (c) Nullable issue references → SET NULL.
        await conn.execute(_NULL_OPENED, snapshot_id)
        await conn.execute(_NULL_RESOLVED, snapshot_id)

        # (c.5) Cascade-discard every OPEN review anchored on the target (DD-94), children-
        # first. The session FK-references the snapshot (baseline + as_received), so the
        # session MUST be gone before the snapshot delete below or the FK blocks it.
        for sid in dependent_session_ids:
            await conn.execute(_DISCARD_HUNKS, sid)
            await conn.execute(_DISCARD_OVERRIDES, sid)
            await conn.execute(_NULL_ISSUE_SESSION, sid)
            await conn.execute(_DISCARD_CHANGES, sid)
            await conn.execute(_DISCARD_SESSION, sid)

        # (d) The target's own node_versions group, then (e) the snapshot row.
        await conn.execute(_DELETE_TARGET_VERSIONS, snapshot_id)
        await conn.execute(_DELETE_SNAPSHOT, snapshot_id)

        # (f) Audit.
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_VERSION_DELETED,
                entity_type="contract",
                entity_id=contract_id,
                actor=get_settings().operator_actor,
                payload={
                    "snapshot_id": snapshot_id,
                    "version_number": target_version,
                    "is_latest": is_latest,
                    "rolled_back": will_rollback,
                    "rollback_to_version": rollback_to_version,
                    "pointers_removed": pointers_removed,
                    "reviews_discarded": dependent_session_ids,
                },
            ),
        )

    return SnapshotDeleteResponse(
        deleted=True,
        snapshot_id=snapshot_id,
        version_number=target_version,
        is_latest=is_latest,
        will_rollback=will_rollback,
        rolled_back=will_rollback,
        rollback_to_version=rollback_to_version,
        sent_record=sent_record,
        review_discard=review_discard,
        warnings=warnings,
        pointers_removed=pointers_removed,
    )
