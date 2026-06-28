"""DD-86 "Start over" — reset an OPEN Mode-B revision session to its as-imported
state. Staging-only and non-destructive: touches ONLY the
`counterparty_revision_{sessions,changes,hunks}` rows. The working copy / live
contract is untouched (nothing is applied until Apply, F03c) and Donna's advisory
recommendations are preserved (the `donna_*` hunk columns are never written).

What "fresh" means (DD-86):
  - every hunk `verdict` -> 'pending', every `final_text` -> NULL (a single blanket
    UPDATE) — this also clears whole-node new/deleted decisions (they ride the hunk
    verdict, DD-79);
  - every change's progress -> pending / 0 decided;
  - every Phase-1 abstain the matcher produced returns to abstain-pending.

Abstain restore (DD-86 "do not discard the match RESULTS, only the operator's
confirmations"): a confirm-match RECLASSIFIES an abstain row out of the bucket
(node_id / proposed_order_index / hunks mutated) with no discriminator column, so the
original abstain shape is not recoverable from the row alone. We re-run the
DETERMINISTIC matcher (`match_revision` on the same baseline + as_received snapshots
the import used — the read path already does this, DD-79) to recover every abstain's
provisional candidate + confidence, then re-stage each abstain row to its import
shape. The stable join is `received_node_id` (= the as_received synthetic index F03b
set on NEW + ABSTAIN rows; survives confirm-match, which never rewrites it).

No schema change; not a re-parse/re-import.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import structlog

from backend.config.settings import get_settings
from backend.models.audit import AuditEvent
from backend.models.revision_import import HunkDraft
from backend.services.audit_repo import record_event
from backend.services.import_.revision_import import baseline_to_clause_nodes, extract_hunks
from backend.services.import_.revision_match import match_revision
from backend.services.import_.revision_review import (
    SessionAlreadyApplied,
    SessionNotFound,
    _revised_to_incoming_clause_nodes,
)
from backend.services.snapshot import get_snapshot

log = structlog.get_logger()

# Free-form audit action (audit_log.event_type is unconstrained TEXT; kept local to
# stay disjoint from the shared audit constants).
_EVENT_REVISION_RESET = "revision_review_reset"


class _ResetSession(NamedTuple):
    """The minimal session fields the reset + abstain re-stage need."""

    id: str
    contract_id: str
    baseline_snapshot_id: str
    source: str
    status: str


_SELECT_SESSION = """
SELECT id, contract_id, baseline_snapshot_id, source, status
FROM counterparty_revision_sessions
WHERE id = $1
"""

# Mirrors revision_review._FIND_RECEIVED_POINTER: an open session's as_received
# snapshot is the one its party's `received` pointer points at (stable while reviewing).
_FIND_RECEIVED_POINTER = """
SELECT snapshot_id FROM snapshot_pointers
WHERE contract_id = $1 AND party = $2 AND direction = 'received'
"""

_SELECT_CHANGE_LINKS = """
SELECT id, received_node_id
FROM counterparty_revision_changes
WHERE session_id = $1
"""

# Blanket resets (one UPDATE each, WHERE the session). Donna's `donna_*` columns are
# deliberately NOT in the SET list (recommendations are preserved, DD-86).
_RESET_HUNKS = """
UPDATE counterparty_revision_hunks
SET verdict = 'pending', final_text = NULL, decided_at = NULL
WHERE change_id IN (
    SELECT id FROM counterparty_revision_changes WHERE session_id = $1
)
"""

_RESET_CHANGE_PROGRESS = """
UPDATE counterparty_revision_changes
SET hunks_decided = 0, status = 'pending'
WHERE session_id = $1
"""

_RESET_SESSION_COUNT = """
UPDATE counterparty_revision_sessions
SET changes_reviewed_count = 0
WHERE id = $1
"""

# Restore one change row to abstain shape (node_id NULL + proposed_order_index NULL +
# match_confidence set = the derived "abstain" bucket, see revision_review._derive_kind).
_RECLASSIFY_TO_ABSTAIN = """
UPDATE counterparty_revision_changes
SET node_id = NULL, proposed_parent_id = $2, proposed_order_index = NULL,
    match_confidence = $3, hunk_count = $4, hunks_decided = 0, status = 'pending'
WHERE id = $1
"""

_DELETE_HUNKS = "DELETE FROM counterparty_revision_hunks WHERE change_id = $1"

_INSERT_HUNK = """
INSERT INTO counterparty_revision_hunks
    (change_id, hunk_type, significance, position_in_body, original_text, proposed_text)
VALUES ($1, $2, $3, $4, $5, $6)
"""


def _abstain_hunks(
    candidate: str | None,
    baseline_body_by_id: dict[str, str],
    incoming_body: str,
) -> list[HunkDraft]:
    """The provisional hunks for one abstain — reproduces revision_import's abstain
    staging exactly: a candidate-anchored diff (whole-body replacement when the diff is
    empty), else a single whole-body insertion when there is no baseline candidate."""
    if candidate is not None and candidate in baseline_body_by_id:
        baseline_body = baseline_body_by_id[candidate]
        return extract_hunks(baseline_body, incoming_body) or [
            HunkDraft(
                hunk_type="replacement",
                position_in_body=0,
                original_text=baseline_body or None,
                proposed_text=incoming_body or None,
            )
        ]
    return [
        HunkDraft(
            hunk_type="insertion",
            position_in_body=0,
            original_text=None,
            proposed_text=incoming_body or None,
        )
    ]


async def _restage_abstains(conn: Any, session: _ResetSession) -> int:
    """Re-stage every abstain the matcher produced back to abstain-pending. Re-runs the
    deterministic matcher on the import-time snapshots, then for each abstain restores
    the change row (found by `received_node_id`) to its import shape + provisional hunks.
    No-op (returns 0) when the snapshots are unavailable (e.g. a pre-0011 legacy session
    whose rows carry no `received_node_id`). Caller wraps this in the reset transaction."""
    received_id = await conn.fetchval(_FIND_RECEIVED_POINTER, session.contract_id, session.source)
    if received_id is None:
        return 0
    baseline_snapshot = await get_snapshot(conn, session.baseline_snapshot_id)
    received_snapshot = await get_snapshot(conn, str(received_id))
    if (
        baseline_snapshot is None
        or baseline_snapshot.tree is None
        or received_snapshot is None
        or received_snapshot.tree is None
    ):
        return 0

    baseline_clause_nodes = baseline_to_clause_nodes(baseline_snapshot.tree)
    incoming_clause_nodes = _revised_to_incoming_clause_nodes(received_snapshot.tree)
    result = match_revision(baseline_clause_nodes, incoming_clause_nodes)
    if not result.abstains:
        return 0

    baseline_body_by_id = {n.id: n.body for n in baseline_clause_nodes if n.id is not None}
    # The as_received snapshot froze each incoming node with id = str(flat index) and
    # body = its canonical text, so this recovers the abstain's incoming body by index.
    incoming_body_by_index = {
        int(n.id): (n.body or "").strip() for n in received_snapshot.tree if not n.is_deleted
    }

    change_rows = await conn.fetch(_SELECT_CHANGE_LINKS, session.id)
    change_by_received = {
        str(r["received_node_id"]): str(r["id"])
        for r in change_rows
        if r["received_node_id"] is not None
    }

    restaged = 0
    for ab in result.abstains:
        change_id = change_by_received.get(str(ab.incoming_index))
        if change_id is None:
            continue
        incoming_body = incoming_body_by_index.get(ab.incoming_index, "")
        hunks = _abstain_hunks(ab.best_baseline_id, baseline_body_by_id, incoming_body)
        await conn.execute(
            _RECLASSIFY_TO_ABSTAIN, change_id, ab.best_baseline_id, ab.confidence, len(hunks)
        )
        await conn.execute(_DELETE_HUNKS, change_id)
        for h in hunks:
            await conn.execute(
                _INSERT_HUNK,
                change_id,
                h.hunk_type,
                h.significance,
                h.position_in_body,
                h.original_text,
                h.proposed_text,
            )
        restaged += 1
    return restaged


async def reset_session(conn: Any, contract_id: str, session_id: str) -> None:
    """Return the OPEN session to its as-imported state (DD-86). 404 if the session is
    missing or not this contract's; 409 if it is already applied/closed. All staging
    resets run in one transaction; the route re-reads the fresh ReviewPayload after."""
    row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if row is None or str(row["contract_id"]) != contract_id:
        raise SessionNotFound(session_id)
    if row["status"] != "reviewing":
        raise SessionAlreadyApplied(f"session {session_id} has already been applied")

    session = _ResetSession(
        id=str(row["id"]),
        contract_id=str(row["contract_id"]),
        baseline_snapshot_id=str(row["baseline_snapshot_id"]),
        source=row["source"],
        status=row["status"],
    )

    async with conn.transaction():
        restaged = await _restage_abstains(conn, session)
        await conn.execute(_RESET_HUNKS, session_id)
        await conn.execute(_RESET_CHANGE_PROGRESS, session_id)
        await conn.execute(_RESET_SESSION_COUNT, session_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=_EVENT_REVISION_RESET,
                entity_type="contract",
                entity_id=contract_id,
                actor=get_settings().operator_actor,
                payload={"session_id": session_id, "abstains_restaged": restaged},
            ),
        )

    log.info(
        "revision_review.reset",
        session_id=session_id,
        contract_id=contract_id,
        abstains_restaged=restaged,
    )
