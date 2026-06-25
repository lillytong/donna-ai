"""Mode B revision review + decision (F03c) — the READ + DECISION side of the loop
F03b staged. asyncpg, raw SQL.

Read: project the staged `counterparty_revision_{sessions,changes,hunks}` into the
two-phase review payload (DD-78) — Phase 1 = the abstain match-confirm queue (ranked
by ascending `match_confidence`) + tree-shape anomalies (none staged yet); Phase 2 =
the settled edited/new/deleted changes in document order, each with its hunks +
current decision state so the UI can resume.

Decision:
  - 6b match-confirm (abstain resolution): confirm / new / rematch — reclassifies a
    change out of the abstain bucket by mutating its node_id / proposed_order_index
    and regenerating hunks where the baseline changes.
  - hunk verdict (DD-27 four actions accept|counter|edit|keep) and whole-node
    decision (accept|reject|edit) — both record onto the CHECK-constrained stored
    `verdict` + `final_text` and roll up the parent change's hunks_decided/status.
  - apply: when every change is decided, APPLY to the live working copy reusing the
    EXISTING F08 paths (node_edit / node_create / node_delete — each writes
    node_versions + audit). Rejections seed a `counterparty_proposed_edit` issue
    (§11 step 9). One transaction; rich F03d learning-context capture is deferred.

`change_kind` is derived (F03b wrote no kind column) — see models/revision_review.py.
The hunk verdict→stored mapping: accept→accepted(proposed_text) · counter→modified
(donna_counter_text) · edit→modified(operator text) · keep→rejected(original_text);
whole-node accept→accepted · reject→rejected · edit→modified.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import structlog

from backend.config.settings import get_settings
from backend.models.audit import (
    EVENT_REVISION_MATCH_CONFIRMED,
    EVENT_REVISION_SESSION_APPLIED,
    AuditEvent,
)
from backend.models.revision_import import StoredRevisionSession
from backend.models.revision_review import (
    ApplyResult,
    ChangeKind,
    ConfirmMatchRequest,
    HunkDecideRequest,
    NodeDecideRequest,
    ReviewChange,
    ReviewHunk,
    ReviewPayload,
    ReviewPhase1,
    StoredHunkVerdict,
)
from backend.services import node_create, node_delete, node_edit
from backend.services.audit_repo import record_event
from backend.services.import_.revision_import import extract_hunks

log = structlog.get_logger()


class RevisionReviewError(Exception):
    """Base for typed failures; the route maps `status_code`/`detail`."""

    status_code: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SessionNotFound(RevisionReviewError):
    status_code = 404


class ChangeNotFound(RevisionReviewError):
    status_code = 404


class HunkNotFound(RevisionReviewError):
    status_code = 404


class NotAnAbstain(RevisionReviewError):
    status_code = 409


class WrongChangeKind(RevisionReviewError):
    status_code = 422


class BadDecision(RevisionReviewError):
    status_code = 422


class SessionNotReady(RevisionReviewError):
    status_code = 409


class SessionAlreadyApplied(RevisionReviewError):
    status_code = 409


# --------------------------------------------------------------------------- #
# SQL                                                                           #
# --------------------------------------------------------------------------- #

_SELECT_SESSION = """
SELECT id, contract_id, baseline_snapshot_id, source, source_filename, parse_path,
       status, changes_count, changes_reviewed_count, imported_at
FROM counterparty_revision_sessions
WHERE id = $1
"""

_LIST_SESSIONS = """
SELECT id, contract_id, baseline_snapshot_id, source, source_filename, parse_path,
       status, changes_count, changes_reviewed_count, imported_at
FROM counterparty_revision_sessions
WHERE contract_id = $1
ORDER BY (status = 'reviewing') DESC, imported_at DESC
"""

_SELECT_CHANGES = """
SELECT id, session_id, node_id, proposed_parent_id, proposed_order_index,
       match_confidence, hunk_count, hunks_decided, status
FROM counterparty_revision_changes
WHERE session_id = $1
"""

_SELECT_CHANGE = """
SELECT id, session_id, node_id, proposed_parent_id, proposed_order_index,
       match_confidence, hunk_count, hunks_decided, status
FROM counterparty_revision_changes
WHERE id = $1
"""

_SELECT_HUNKS = """
SELECT id, change_id, hunk_type, significance, position_in_body, original_text,
       proposed_text, donna_verdict, donna_counter_text, verdict, final_text
FROM counterparty_revision_hunks
WHERE change_id = ANY($1::uuid[])
ORDER BY change_id, position_in_body NULLS FIRST, id
"""

_SELECT_HUNK_WITH_CHANGE = """
SELECT h.id AS h_id, h.change_id, h.hunk_type, h.significance, h.position_in_body,
       h.original_text, h.proposed_text, h.donna_verdict, h.donna_counter_text,
       h.verdict, h.final_text,
       c.session_id, c.node_id, c.match_confidence, c.proposed_order_index
FROM counterparty_revision_hunks h
JOIN counterparty_revision_changes c ON c.id = h.change_id
WHERE h.id = $1
"""

_UPDATE_HUNK_VERDICT = """
UPDATE counterparty_revision_hunks
SET verdict = $2, final_text = $3, decided_at = now()
WHERE id = $1
"""

_UPDATE_CHANGE_PROGRESS = """
UPDATE counterparty_revision_changes
SET hunks_decided = sub.decided,
    status = CASE
        WHEN sub.decided = 0 THEN 'pending'
        WHEN sub.decided >= hunk_count AND hunk_count > 0 THEN 'complete'
        ELSE 'partial'
    END
FROM (
    SELECT count(*) FILTER (WHERE verdict <> 'pending') AS decided
    FROM counterparty_revision_hunks WHERE change_id = $1
) sub
WHERE id = $1
"""

_RECOUNT_SESSION_REVIEWED = """
UPDATE counterparty_revision_sessions
SET changes_reviewed_count = (
    SELECT count(*) FROM counterparty_revision_changes
    WHERE session_id = $1 AND status = 'complete'
)
WHERE id = $1
"""

_RECLASSIFY_CHANGE = """
UPDATE counterparty_revision_changes
SET node_id = $2, proposed_parent_id = $3, proposed_order_index = $4,
    match_confidence = $5, hunk_count = $6, hunks_decided = 0, status = 'pending'
WHERE id = $1
"""

_DELETE_HUNKS = "DELETE FROM counterparty_revision_hunks WHERE change_id = $1"

_INSERT_HUNK = """
INSERT INTO counterparty_revision_hunks
    (change_id, hunk_type, significance, position_in_body, original_text, proposed_text)
VALUES ($1, $2, $3, $4, $5, $6)
"""

_COMPLETE_SESSION = """
UPDATE counterparty_revision_sessions
SET status = 'completed', changes_reviewed_count = changes_count
WHERE id = $1
"""

_LIVE_NODE_TEXT = """
SELECT body, heading FROM nodes
WHERE id = $1 AND contract_id = $2 AND is_deleted = false
"""

_LIVE_NODE_POS = """
SELECT parent_id, order_index FROM nodes
WHERE id = $1 AND contract_id = $2 AND is_deleted = false
"""

_LIVE_NODES = """
SELECT id, parent_id, order_index FROM nodes
WHERE contract_id = $1 AND is_deleted = false
"""

_INSERT_REVISION_ISSUE = """
INSERT INTO issues
    (contract_id, node_id, title, their_position, category, initiator,
     counterparty_revision_session_id, opened_in_snapshot_id)
VALUES ($1, $2, $3, $4, 'counterparty_proposed_edit', 'counterparty', $5, $6)
RETURNING id
"""


# --------------------------------------------------------------------------- #
# Row → model projection + derivation                                           #
# --------------------------------------------------------------------------- #


def _derive_kind(row: Any) -> ChangeKind:
    node_id = row["node_id"]
    if node_id is not None:
        return "edited" if row["match_confidence"] is not None else "deleted"
    return "new" if row["proposed_order_index"] is not None else "abstain"


def _to_hunk(row: Any) -> ReviewHunk:
    return ReviewHunk(
        id=str(row["id"]),
        change_id=str(row["change_id"]),
        hunk_type=row["hunk_type"],
        significance=row["significance"],
        position_in_body=row["position_in_body"],
        original_text=row["original_text"],
        proposed_text=row["proposed_text"],
        donna_verdict=row["donna_verdict"],
        donna_counter_text=row["donna_counter_text"],
        verdict=row["verdict"],
        final_text=row["final_text"],
    )


def _to_change(row: Any, hunks: list[ReviewHunk]) -> ReviewChange:
    node_id = row["node_id"]
    parent = row["proposed_parent_id"]
    return ReviewChange(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        change_kind=_derive_kind(row),
        node_id=str(node_id) if node_id is not None else None,
        proposed_parent_id=str(parent) if parent is not None else None,
        proposed_order_index=row["proposed_order_index"],
        match_confidence=row["match_confidence"],
        hunk_count=row["hunk_count"],
        hunks_decided=row["hunks_decided"],
        status=row["status"],
        hunks=hunks,
    )


def _to_session(row: Any) -> StoredRevisionSession:
    fname = row["source_filename"]
    return StoredRevisionSession(
        id=str(row["id"]),
        contract_id=str(row["contract_id"]),
        baseline_snapshot_id=str(row["baseline_snapshot_id"]),
        source=row["source"],
        source_filename=fname,
        parse_path=row["parse_path"],
        status=row["status"],
        changes_count=row["changes_count"],
        changes_reviewed_count=row["changes_reviewed_count"],
        imported_at=row["imported_at"],
    )


async def _hunks_for(conn: Any, change_ids: list[str]) -> dict[str, list[ReviewHunk]]:
    if not change_ids:
        return {}
    rows = await conn.fetch(_SELECT_HUNKS, change_ids)
    out: dict[str, list[ReviewHunk]] = {}
    for r in rows:
        out.setdefault(str(r["change_id"]), []).append(_to_hunk(r))
    return out


# --------------------------------------------------------------------------- #
# Text reconstruction (deterministic; reuses F03b's word-level diff)            #
# --------------------------------------------------------------------------- #


async def _live_text(conn: Any, contract_id: str, node_id: str | None) -> str:
    """The live node's editable text — body if present, else heading. This is the
    same canonical clause text F03b diffed against (the working copy == baseline in
    the normal Mode B flow), and the field node_edit will patch."""
    if node_id is None:
        return ""
    row = await conn.fetchrow(_LIVE_NODE_TEXT, node_id, contract_id)
    if row is None:
        return ""
    return (row["body"] if row["body"] is not None else row["heading"]) or ""


def _apply(base: str, hunks: list[ReviewHunk], pick: Callable[[ReviewHunk], str]) -> str:
    """Apply each hunk's chosen text over `base`. Hunks reference BASELINE char
    offsets, so they are applied in descending position to keep offsets valid."""
    out = base
    for h in sorted(hunks, key=lambda x: x.position_in_body or 0, reverse=True):
        pos = h.position_in_body or 0
        repl = pick(h)
        if h.original_text is not None:
            end = pos + len(h.original_text)
            out = out[:pos] + repl + out[end:]
        else:
            out = out[:pos] + repl + out[pos:]
    return out


async def _reconstruct_incoming(
    conn: Any, contract_id: str, parent_candidate: str | None, hunks: list[ReviewHunk]
) -> str:
    """Recover the counterparty's full incoming clause body for an abstain. A
    no-candidate abstain already carries one whole-body insertion hunk; a
    candidate abstain carries a diff against the candidate, so replay the proposed
    text over the candidate's live body (the equal regions come from the base)."""
    if len(hunks) == 1 and hunks[0].original_text is None:
        return hunks[0].proposed_text or ""
    base = await _live_text(conn, contract_id, parent_candidate)
    return _apply(base, hunks, lambda h: h.proposed_text or "")


# --------------------------------------------------------------------------- #
# Read                                                                          #
# --------------------------------------------------------------------------- #


async def list_sessions(conn: Any, contract_id: str) -> list[StoredRevisionSession]:
    rows = await conn.fetch(_LIST_SESSIONS, contract_id)
    return [_to_session(r) for r in rows]


async def _doc_order(conn: Any, contract_id: str) -> dict[str, int]:
    """Pre-order DFS sequence over the live tree (parent before children, siblings
    by order_index) — the document-order key for the Phase-2 content stream."""
    rows = await conn.fetch(_LIVE_NODES, contract_id)
    children: dict[str | None, list[Any]] = {}
    for r in rows:
        parent = str(r["parent_id"]) if r["parent_id"] is not None else None
        children.setdefault(parent, []).append(r)
    for sibs in children.values():
        sibs.sort(key=lambda x: x["order_index"])
    seq: dict[str, int] = {}
    counter = 0

    def walk(parent: str | None) -> None:
        nonlocal counter
        for r in children.get(parent, []):
            nid = str(r["id"])
            seq[nid] = counter
            counter += 1
            walk(nid)

    walk(None)
    return seq


async def get_review_payload(conn: Any, session_id: str) -> ReviewPayload:
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    session = _to_session(session_row)

    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]

    abstains = sorted(
        (c for c in changes if c.change_kind == "abstain"),
        key=lambda c: c.match_confidence if c.match_confidence is not None else 1.0,
    )

    settled = [c for c in changes if c.change_kind != "abstain"]
    seq = await _doc_order(conn, session.contract_id)

    def order_key(c: ReviewChange) -> tuple[int, int, int]:
        if c.change_kind == "new":
            anchor = seq.get(c.proposed_parent_id, len(seq)) if c.proposed_parent_id else -1
            return (anchor, 1, c.proposed_order_index or 0)
        return (seq.get(cast(str, c.node_id), len(seq)), 0, 0)

    phase2 = sorted(settled, key=order_key)

    return ReviewPayload(
        session=session,
        phase1=ReviewPhase1(abstains=abstains, tree_anomalies=[]),
        phase2=phase2,
    )


async def _load_change(conn: Any, change_id: str) -> tuple[Any, list[ReviewHunk]]:
    row = await conn.fetchrow(_SELECT_CHANGE, change_id)
    if row is None:
        raise ChangeNotFound(change_id)
    hunks = (await _hunks_for(conn, [change_id])).get(change_id, [])
    return row, hunks


async def _change_view(conn: Any, change_id: str) -> ReviewChange:
    row, hunks = await _load_change(conn, change_id)
    return _to_change(row, hunks)


async def _assert_session_reviewing(conn: Any, session_id: str) -> None:
    """Decisions mutate verdicts that only matter pre-apply; reject them once the
    session has advanced past `reviewing` (apply is the sole state-advancing op and
    is already terminal). Mirrors apply_session's already-completed rejection."""
    row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if row is None:
        raise SessionNotFound(session_id)
    if row["status"] != "reviewing":
        raise SessionAlreadyApplied(f"session {session_id} has already been applied")


# --------------------------------------------------------------------------- #
# 6b match-confirm (abstain resolution)                                         #
# --------------------------------------------------------------------------- #


async def confirm_match(conn: Any, change_id: str, req: ConfirmMatchRequest) -> ReviewChange:
    row, hunks = await _load_change(conn, change_id)
    if _derive_kind(row) != "abstain":
        raise NotAnAbstain(f"change {change_id} is not in the abstain bucket")

    session = await conn.fetchrow(_SELECT_SESSION, str(row["session_id"]))
    if session["status"] != "reviewing":
        raise SessionAlreadyApplied(f"session {session['id']} has already been applied")
    contract_id = str(session["contract_id"])
    candidate = str(row["proposed_parent_id"]) if row["proposed_parent_id"] is not None else None
    confidence = row["match_confidence"]

    async with conn.transaction():
        if req.action == "confirm":
            if candidate is None:
                raise BadDecision("no provisional baseline candidate to confirm")
            # Reclassify → edited-match against the provisional best baseline. The
            # staged hunks are already the diff against that candidate.
            await conn.execute(
                _RECLASSIFY_CHANGE, change_id, candidate, None, None, confidence, row["hunk_count"]
            )

        elif req.action == "new":
            # Genuine new node: collapse to one whole-body insertion hunk, anchor it
            # near the candidate (its parent), drop the baseline link.
            incoming = await _reconstruct_incoming(conn, contract_id, candidate, hunks)
            parent_id: str | None = None
            order_index = 0
            if candidate is not None:
                pos = await conn.fetchrow(_LIVE_NODE_POS, candidate, contract_id)
                if pos is not None:
                    parent_id = str(pos["parent_id"]) if pos["parent_id"] is not None else None
                    order_index = pos["order_index"]
            await conn.execute(_RECLASSIFY_CHANGE, change_id, None, parent_id, order_index, None, 1)
            await conn.execute(_DELETE_HUNKS, change_id)
            await conn.execute(
                _INSERT_HUNK, change_id, "insertion", "substantive", 0, None, incoming or None
            )

        else:  # rematch
            if req.baseline_node_id is None:
                raise BadDecision("rematch requires baseline_node_id")
            incoming = await _reconstruct_incoming(conn, contract_id, candidate, hunks)
            new_base = await _live_text(conn, contract_id, req.baseline_node_id)
            new_hunks = extract_hunks(new_base, incoming)
            await conn.execute(
                _RECLASSIFY_CHANGE,
                change_id,
                req.baseline_node_id,
                None,
                None,
                confidence,
                len(new_hunks),
            )
            await conn.execute(_DELETE_HUNKS, change_id)
            for h in new_hunks:
                await conn.execute(
                    _INSERT_HUNK,
                    change_id,
                    h.hunk_type,
                    h.significance,
                    h.position_in_body,
                    h.original_text,
                    h.proposed_text,
                )

        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_REVISION_MATCH_CONFIRMED,
                entity_type="contract",
                entity_id=contract_id,
                actor=get_settings().operator_actor,
                payload={"change_id": change_id, "action": req.action},
            ),
        )

    return await _change_view(conn, change_id)


# --------------------------------------------------------------------------- #
# Decisions                                                                     #
# --------------------------------------------------------------------------- #


def _map_hunk_verdict(req: HunkDecideRequest, hunk: Any) -> tuple[StoredHunkVerdict, str | None]:
    if req.verdict == "accept":
        return "accepted", hunk["proposed_text"]
    if req.verdict == "keep":
        return "rejected", hunk["original_text"]
    if req.verdict == "counter":
        counter = hunk["donna_counter_text"]
        if counter is None:
            raise BadDecision("no Donna counter-language staged for this hunk")
        return "modified", counter
    # edit
    if req.final_text is None:
        raise BadDecision("edit requires final_text")
    return "modified", req.final_text


async def _refresh_progress(conn: Any, change_id: str, session_id: str) -> None:
    await conn.execute(_UPDATE_CHANGE_PROGRESS, change_id)
    await conn.execute(_RECOUNT_SESSION_REVIEWED, session_id)


async def decide_hunk(conn: Any, hunk_id: str, req: HunkDecideRequest) -> ReviewChange:
    hunk = await conn.fetchrow(_SELECT_HUNK_WITH_CHANGE, hunk_id)
    if hunk is None:
        raise HunkNotFound(hunk_id)
    await _assert_session_reviewing(conn, str(hunk["session_id"]))
    verdict, final_text = _map_hunk_verdict(req, hunk)
    change_id = str(hunk["change_id"])
    async with conn.transaction():
        await conn.execute(_UPDATE_HUNK_VERDICT, hunk_id, verdict, final_text)
        await _refresh_progress(conn, change_id, str(hunk["session_id"]))
    return await _change_view(conn, change_id)


async def decide_node(conn: Any, change_id: str, req: NodeDecideRequest) -> ReviewChange:
    row, hunks = await _load_change(conn, change_id)
    await _assert_session_reviewing(conn, str(row["session_id"]))
    kind = _derive_kind(row)
    if kind not in ("new", "deleted"):
        raise WrongChangeKind(f"decide-node is for new/deleted changes, not {kind}")
    if not hunks:
        raise BadDecision("change has no hunk to decide")

    if req.verdict == "accept":
        verdict: StoredHunkVerdict = "accepted"
        final_text = hunks[0].proposed_text
    elif req.verdict == "reject":
        verdict = "rejected"
        final_text = hunks[0].original_text
    else:  # edit
        if req.final_text is None:
            raise BadDecision("edit requires final_text")
        verdict = "modified"
        final_text = req.final_text

    async with conn.transaction():
        for h in hunks:
            await conn.execute(_UPDATE_HUNK_VERDICT, h.id, verdict, final_text)
        await _refresh_progress(conn, change_id, str(row["session_id"]))
    return await _change_view(conn, change_id)


# --------------------------------------------------------------------------- #
# Apply / complete                                                              #
# --------------------------------------------------------------------------- #


async def _open_revision_issue(
    conn: Any,
    *,
    contract_id: str,
    node_id: str | None,
    title: str,
    their_position: str | None,
    session_id: str,
    baseline_snapshot_id: str,
) -> str:
    return str(
        await conn.fetchval(
            _INSERT_REVISION_ISSUE,
            contract_id,
            node_id,
            title,
            their_position,
            session_id,
            baseline_snapshot_id,
        )
    )


async def apply_session(conn: Any, session_id: str) -> ApplyResult:
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    if session_row["status"] == "completed":
        raise SessionAlreadyApplied(f"session {session_id} is already completed")

    contract_id = str(session_row["contract_id"])
    baseline_snapshot_id = str(session_row["baseline_snapshot_id"])

    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]

    undecided = [r for r, _ in changes if r["status"] != "complete"]
    if undecided:
        raise SessionNotReady(
            f"{len(undecided)} change(s) still undecided — resolve every change first"
        )

    edits_applied = nodes_inserted = nodes_deleted = 0
    issue_ids: list[str] = []

    async with conn.transaction():
        for row, hunks in changes:
            kind = _derive_kind(row)
            change_node = str(row["node_id"]) if row["node_id"] is not None else None

            if kind == "edited":
                base = await _live_text(conn, contract_id, change_node)
                new_body = _apply(base, hunks, lambda h: h.final_text or "")
                # A rejected-only change leaves the body == baseline (every kept hunk
                # re-emits its original span), so there is nothing to patch.
                if new_body != base:
                    try:
                        await node_edit.edit_node(
                            conn, contract_id, cast(str, change_node), new_body
                        )
                        edits_applied += 1
                    except (node_edit.NodeNotFound, node_edit.NodeNotEditable):
                        issue_ids.append(
                            await _open_revision_issue(
                                conn,
                                contract_id=contract_id,
                                node_id=change_node,
                                title="Counterparty edit — manual application needed",
                                their_position=new_body,
                                session_id=session_id,
                                baseline_snapshot_id=baseline_snapshot_id,
                            )
                        )
                for h in hunks:
                    if h.verdict == "rejected":
                        issue_ids.append(
                            await _open_revision_issue(
                                conn,
                                contract_id=contract_id,
                                node_id=change_node,
                                title="Counterparty edit — kept our language",
                                their_position=h.proposed_text,
                                session_id=session_id,
                                baseline_snapshot_id=baseline_snapshot_id,
                            )
                        )

            elif kind == "new":
                h = hunks[0]
                if h.verdict in ("accepted", "modified"):
                    await node_create.create_node(
                        conn,
                        contract_id,
                        parent_id=row["proposed_parent_id"] and str(row["proposed_parent_id"]),
                        after_node_id=None,
                        text=h.final_text or "",
                    )
                    nodes_inserted += 1
                else:  # rejected
                    issue_ids.append(
                        await _open_revision_issue(
                            conn,
                            contract_id=contract_id,
                            node_id=None,
                            title="Counterparty addition — rejected",
                            their_position=h.proposed_text,
                            session_id=session_id,
                            baseline_snapshot_id=baseline_snapshot_id,
                        )
                    )

            elif kind == "deleted":
                h = hunks[0]
                if h.verdict == "accepted":
                    await node_delete.delete_node(conn, contract_id, cast(str, change_node))
                    nodes_deleted += 1
                elif h.verdict == "modified":
                    await node_edit.edit_node(
                        conn, contract_id, cast(str, change_node), h.final_text or ""
                    )
                    edits_applied += 1
                else:  # rejected → reinstate (keep) + seed issue
                    issue_ids.append(
                        await _open_revision_issue(
                            conn,
                            contract_id=contract_id,
                            node_id=change_node,
                            title="Counterparty deletion — reinstated",
                            their_position=h.original_text,
                            session_id=session_id,
                            baseline_snapshot_id=baseline_snapshot_id,
                        )
                    )

        await conn.execute(_COMPLETE_SESSION, session_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_REVISION_SESSION_APPLIED,
                entity_type="contract",
                entity_id=contract_id,
                actor=get_settings().operator_actor,
                payload={
                    "session_id": session_id,
                    "edits_applied": edits_applied,
                    "nodes_inserted": nodes_inserted,
                    "nodes_deleted": nodes_deleted,
                    "issues_created": len(issue_ids),
                },
            ),
        )

    log.info(
        "revision_review.applied",
        session_id=session_id,
        contract_id=contract_id,
        edits_applied=edits_applied,
        nodes_inserted=nodes_inserted,
        nodes_deleted=nodes_deleted,
        issues_created=len(issue_ids),
    )

    return ApplyResult(
        session_id=session_id,
        status="completed",
        edits_applied=edits_applied,
        nodes_inserted=nodes_inserted,
        nodes_deleted=nodes_deleted,
        issues_created=len(issue_ids),
        issue_ids=issue_ids,
    )
