"""Mark-as-sent orchestration (DD-71) — the boundary event that cuts the baseline.

Reuses F14 `services/snapshot.py` unchanged: cuts ONE snapshot of the current
working copy (which also stamps the pending `node_versions` edits group under it,
closing the round's change set — as the old send-export did) and advances the
matching DD-48 `shared` pointer(s). `recipient='both'` is one snapshot, two
pointers. The next lineage v-number (DD-70) is the snapshot's position on the
timeline, derived from the snapshot count — no separate label store.

Drift (DD-72): before cutting, compare each non-deleted node's `updated_at` to the
contract's `last_export_at` (NULL = never exported). If drift exists and the
operator has not acknowledged it, return a no-op preview (`marked=False`) so the UI
can show the non-blocking "edited since last export" warning; the operator re-calls
with `acknowledge_drift=True` to proceed.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_MARK_SENT, AuditEvent
from backend.models.mark_sent import MarkSentRecipient, MarkSentRequest, MarkSentResult
from backend.models.snapshots import CutSnapshotRequest, SnapshotPointerTarget
from backend.services.audit_repo import record_event
from backend.services.snapshot import cut_snapshot, set_pointer

# Which DD-48 `shared` pointer(s) each recipient advances. legal → `legal_team`
# (the schema's pointer party); `both` advances both off one snapshot.
_RECIPIENT_POINTERS: dict[MarkSentRecipient, list[SnapshotPointerTarget]] = {
    "counterparty": [SnapshotPointerTarget(party="counterparty", direction="shared")],
    "legal": [SnapshotPointerTarget(party="legal_team", direction="shared")],
    "both": [
        SnapshotPointerTarget(party="counterparty", direction="shared"),
        SnapshotPointerTarget(party="legal_team", direction="shared"),
    ],
}

# Drift + the version that would be minted (snapshot count + 1) + last_export_at,
# in one read. EXISTS is false for a contract with no live edits since the last
# export; true when last_export_at IS NULL (never exported) and live nodes exist.
_DRIFT = """
SELECT
    c.last_export_at,
    (SELECT count(*) FROM contract_snapshots WHERE contract_id = c.id) AS snapshot_count,
    EXISTS (
        SELECT 1 FROM nodes n
        WHERE n.contract_id = c.id AND n.is_deleted = false
          AND (c.last_export_at IS NULL OR n.updated_at > c.last_export_at)
    ) AS drift
FROM contracts c
WHERE c.id = $1
"""


async def mark_sent(conn: Any, contract_id: str, request: MarkSentRequest) -> MarkSentResult:
    info = await conn.fetchrow(_DRIFT, contract_id)
    last_export_at = info["last_export_at"] if info is not None else None
    snapshot_count = int(info["snapshot_count"]) if info is not None else 0
    drift = bool(info["drift"]) if info is not None else False
    # vN being minted = this snapshot's position on the lineage timeline (DD-70).
    version = snapshot_count + 1

    targets = _RECIPIENT_POINTERS[request.recipient]

    # Non-blocking drift gate (DD-72): one click-through, never a hard stop.
    if drift and not request.acknowledge_drift:
        return MarkSentResult(
            marked=False,
            drift=True,
            recipient=request.recipient,
            version=version,
            pointers=[],
            snapshot_id=None,
            last_export_at=last_export_at,
        )

    # One snapshot, then its pointer(s) — wrapped so the cut + pointer advances +
    # audit commit atomically. cut_snapshot's own transaction nests as a savepoint.
    async with conn.transaction():
        snapshot = await cut_snapshot(conn, contract_id, CutSnapshotRequest(origin="export"))
        for target in targets:
            await set_pointer(conn, contract_id, target, snapshot.id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_MARK_SENT,
                entity_type="contract",
                entity_id=contract_id,
                actor=get_settings().operator_actor,
                payload={
                    "recipient": request.recipient,
                    "snapshot_id": snapshot.id,
                    "pointers": [t.party for t in targets],
                    "drift": drift,
                },
            ),
        )

    return MarkSentResult(
        marked=True,
        drift=drift,
        recipient=request.recipient,
        version=version,
        pointers=[t.party for t in targets],
        snapshot_id=snapshot.id,
        last_export_at=last_export_at,
    )
