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
from typing import Any, NamedTuple, cast

import structlog

from backend.config.settings import get_settings
from backend.models.audit import (
    EVENT_REVISION_MATCH_CONFIRMED,
    EVENT_REVISION_SESSION_APPLIED,
    AuditEvent,
)
from backend.models.contract_tree import ParsedTree, Role, TreeNode
from backend.models.imports import ContractTreeResponse, NodeTreeItem
from backend.models.revision_import import StoredRevisionSession
from backend.models.revision_match import ClauseNode, RevisionMatchResult
from backend.models.revision_review import (
    AbstainMatch,
    ApplyResult,
    ChangeContext,
    ChangeContextSide,
    ChangeKind,
    ClusterDecideRequest,
    ConfirmMatchRequest,
    DocumentChange,
    DocumentChangeKind,
    DocumentNode,
    HunkDecideRequest,
    NodeDecideRequest,
    NodeRoleOverrideResult,
    ProjectedNode,
    ReviewChange,
    ReviewHunk,
    ReviewPayload,
    ReviewPhase1,
    RevisionDocumentView,
    StoredHunkVerdict,
)
from backend.models.snapshots import SnapshotNode
from backend.services import node_create, node_delete, node_edit
from backend.services.audit_repo import record_event
from backend.services.import_.numbering import derive_numbers
from backend.services.import_.revision_cluster import cluster_id, cluster_key
from backend.services.import_.revision_import import baseline_to_clause_nodes, extract_hunks
from backend.services.import_.revision_match import match_revision
from backend.services.snapshot import get_snapshot, get_snapshot_tree

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


class ClusterNotFound(RevisionReviewError):
    status_code = 404


class SessionAlreadyApplied(RevisionReviewError):
    status_code = 409


# --------------------------------------------------------------------------- #
# SQL                                                                           #
# --------------------------------------------------------------------------- #

# `pending_changes` is a correlated subquery (one query, no N+1) over this session's
# changes — the resume affordance's "N pending" count.
_PENDING_CHANGES_SUBQUERY = """
    (SELECT count(*) FROM counterparty_revision_changes crc
     WHERE crc.session_id = counterparty_revision_sessions.id
       AND crc.status <> 'complete') AS pending_changes
"""

_SELECT_SESSION = f"""
SELECT id, contract_id, baseline_snapshot_id, source, source_filename, parse_path,
       status, changes_count, changes_reviewed_count, imported_at,
       {_PENDING_CHANGES_SUBQUERY}
FROM counterparty_revision_sessions
WHERE id = $1
"""

_LIST_SESSIONS = f"""
SELECT id, contract_id, baseline_snapshot_id, source, source_filename, parse_path,
       status, changes_count, changes_reviewed_count, imported_at,
       {_PENDING_CHANGES_SUBQUERY}
FROM counterparty_revision_sessions
WHERE contract_id = $1
ORDER BY (status = 'reviewing') DESC, imported_at DESC
"""

_SELECT_CHANGES = """
SELECT id, session_id, node_id, proposed_parent_id, proposed_order_index,
       match_confidence, received_node_id, hunk_count, hunks_decided, status
FROM counterparty_revision_changes
WHERE session_id = $1
"""

_SELECT_CHANGE = """
SELECT id, session_id, node_id, proposed_parent_id, proposed_order_index,
       match_confidence, received_node_id, hunk_count, hunks_decided, status
FROM counterparty_revision_changes
WHERE id = $1
"""

_SELECT_HUNKS = """
SELECT id, change_id, hunk_type, significance, position_in_body, original_text,
       proposed_text, donna_verdict, donna_counter_text, donna_rationale, verdict, final_text
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

# DD-54 role lookup for the document view. Baseline snapshot node ids ARE real live
# node ids (snapshot.py dumps `nodes.id`), so this recovers the operator-confirmed F04
# role the snapshot shape doesn't carry. is_deleted is NOT filtered — a baseline node
# soft-deleted since the snapshot was cut still carries its role.
_SELECT_NODE_ROLES = "SELECT id, role FROM nodes WHERE id = ANY($1::uuid[])"

# The as_received snapshot of an active session is the one the `received` pointer for
# the session's party points at — the import cut it and advanced the pointer in one
# txn, and the single-open-session guard means no later import can have moved it while
# the session is `reviewing` (session.source == the pointer party, DD-47/DD-48).
_FIND_RECEIVED_POINTER = """
SELECT snapshot_id FROM snapshot_pointers
WHERE contract_id = $1 AND party = $2 AND direction = 'received'
"""

# Abstain-context bounds (read-only enrichment must stay cheap).
_CTX_MAX_BREADCRUMB = 3
_CTX_MAX_CHILDREN = 5
_CTX_SNIPPET = 120

_INSERT_REVISION_ISSUE = """
INSERT INTO issues
    (contract_id, node_id, title, their_position, category, initiator,
     counterparty_revision_session_id, opened_in_snapshot_id)
VALUES ($1, $2, $3, $4, 'counterparty_proposed_edit', 'counterparty', $5, $6)
RETURNING id
"""

# Mode B classification editing (Phase 1) — the session-scoped revised-node role
# override store. A row WINS over render-time inheritance for its (synthetic) node_id;
# a NULL/absent role CLEARS it (DELETE) so the node reverts to auto-classification.
_SELECT_ROLE_OVERRIDES = """
SELECT node_id, role FROM counterparty_revision_node_overrides WHERE session_id = $1
"""

_UPSERT_ROLE_OVERRIDE = """
INSERT INTO counterparty_revision_node_overrides (session_id, node_id, role)
VALUES ($1, $2, $3)
ON CONFLICT (session_id, node_id) DO UPDATE SET role = EXCLUDED.role
"""

_DELETE_ROLE_OVERRIDE = """
DELETE FROM counterparty_revision_node_overrides WHERE session_id = $1 AND node_id = $2
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
        donna_rationale=row["donna_rationale"],
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
        received_node_id=row["received_node_id"],
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
        pending_changes=row["pending_changes"],
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


def _stamp_clusters(changes: list[ReviewChange]) -> None:
    """Stamp `cluster_id` + `cluster_size` (>1 only) onto every substantive replacement hunk that
    recurs across the session (DD-89, F34), so the frontend can collapse identical counterparty
    edits into ONE grouped review stop. IN PLACE.

    Buckets by the SHARED `revision_cluster.cluster_key` — the SAME function Step-1 recommend-time
    clustering uses — so the grouped stop can never drift from the verdicts Donna fanned out.
    Abstains are excluded (recommend never judged them), matching the recommend-time population;
    decided members stay in their bucket (the key is text-only, independent of verdict) so a
    peeled-off member still shows in the group's mixed summary. Singletons are left untouched
    (cluster_id None, size 1) and ride the existing per-hunk path."""
    buckets: dict[tuple[str, str], list[ReviewHunk]] = {}
    for change in changes:
        if change.change_kind == "abstain":
            continue
        for hunk in change.hunks:
            key = cluster_key(hunk.significance, hunk.original_text, hunk.proposed_text)
            if key is not None:
                buckets.setdefault(key, []).append(hunk)
    for key, members in buckets.items():
        if len(members) < 2:
            continue
        cid = cluster_id(key)
        for hunk in members:
            hunk.cluster_id = cid
            hunk.cluster_size = len(members)


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


# --------------------------------------------------------------------------- #
# Change structural context (F03c UX — read-only enrichment, every change kind) #
# --------------------------------------------------------------------------- #


class _Located(NamedTuple):
    number: str
    breadcrumb: list[str]
    item: NodeTreeItem
    prev_label: str | None
    next_label: str | None


def _node_label(item: NodeTreeItem) -> str:
    """Single-line label for a node: its heading, else a truncated body snippet."""
    text = (item.heading or item.body or item.plain_text or "").strip()
    if len(text) > _CTX_SNIPPET:
        return text[:_CTX_SNIPPET].rstrip() + "…"
    return text


def _node_body(item: NodeTreeItem) -> str | None:
    """The FULL clause text the hunk offsets index into. Prose nodes hold their text
    in EITHER heading OR body (persist.py splits one or the other, never both), so
    `heading or body` reproduces the exact string F03b's `_snapshotnode_text` diffed —
    keeping `position_in_body` valid for in-place edit rendering."""
    return (item.heading or item.body or "").strip() or None


def _locate(nodes: list[NodeTreeItem], target_id: str) -> _Located | None:
    """DFS a nested tree for `target_id`, accumulating the derived outline number
    (1-based sibling path, e.g. "2.1"), the ancestor-heading breadcrumb, and the
    labels of the two flanking siblings (placement neighbours). None if id absent."""

    def walk(items: list[NodeTreeItem], prefix: str, crumb: list[str]) -> _Located | None:
        for pos, item in enumerate(items, start=1):
            number = f"{prefix}.{pos}" if prefix else str(pos)
            if item.id == target_id:
                prev_label = _node_label(items[pos - 2]) if pos >= 2 else None
                next_label = _node_label(items[pos]) if pos < len(items) else None
                return _Located(number, crumb, item, prev_label or None, next_label or None)
            hit = walk(item.children, number, [*crumb, _node_label(item)])
            if hit is not None:
                return hit
        return None

    return walk(nodes, "", [])


def _empty_context(side: str) -> ChangeContextSide:
    return ChangeContextSide(side=cast(Any, side), found=False)


def _build_side_context(
    side: str,
    tree: ContractTreeResponse | None,
    target_id: str | None,
    number_by_id: dict[str, str] | None = None,
) -> ChangeContextSide:
    if tree is None or target_id is None:
        return _empty_context(side)
    located = _locate(tree.nodes, target_id)
    if located is None:
        return _empty_context(side)
    breadcrumb = [c for c in located.breadcrumb if c][-_CTX_MAX_BREADCRUMB:]
    children_preview = [lbl for child in located.item.children if (lbl := _node_label(child))][
        :_CTX_MAX_CHILDREN
    ]
    # `_locate`'s `number` is a NAIVE positional path that counts EVERY node (front-matter,
    # recitals, sub-items) as consuming a position, so it inflates past the real clause count
    # (e.g. clause 13.3 shown as 31.3.1). Prefer the canonical role-aware `derive_numbers`
    # value (same scheme as the two-pane view + Mode A export) keyed by node id; fall back to
    # the positional path only when the node isn't in the resolved map (e.g. unit tests).
    number = (number_by_id or {}).get(target_id) or located.number
    return ChangeContextSide(
        side=cast(Any, side),
        found=True,
        number=number,
        heading=(located.item.heading or "").strip() or None,
        breadcrumb=breadcrumb,
        children_preview=children_preview,
        body=_node_body(located.item),
        prev_label=located.prev_label,
        next_label=located.next_label,
    )


def _find_incoming_id(nodes: list[NodeTreeItem], body: str) -> tuple[str | None, bool]:
    """Recover an incoming (as_received) node by matching its reconstructed body
    against snapshot node text. Used for `new` and `abstain` changes — neither stores
    an incoming-node reference (only the baseline candidate, see report). Returns
    (node_id, ambiguous) where ambiguous flags >1 body-identical node (e.g. repeated
    headings), which cannot be disambiguated from the staged data."""
    target = body.strip()
    if not target:
        return None, False
    matches: list[str] = []

    def walk(items: list[NodeTreeItem]) -> None:
        for item in items:
            text = (item.heading or item.body or item.plain_text or "").strip()
            if text == target:
                matches.append(item.id)
            walk(item.children)

    walk(nodes)
    if not matches:
        return None, False
    return matches[0], len(matches) > 1


async def _change_context(
    conn: Any,
    contract_id: str,
    change: ReviewChange,
    baseline_tree: ContractTreeResponse | None,
    received_tree: ContractTreeResponse | None,
    baseline_number_by_id: dict[str, str] | None = None,
    revised_number_by_id: dict[str, str] | None = None,
) -> ChangeContext:
    """Resolve both sides of a change's structural context. Resolution is EXACT for
    settled changes (the change row carries the node id) and a body-match heuristic
    only where no incoming reference is stored (new / abstain):
      - edited / deleted ⇒ `baseline` located by `node_id`.
      - new              ⇒ `their` body-matched in the as_received tree.
      - abstain          ⇒ `baseline` = candidate (`proposed_parent_id`) + `their`.

    The `*_number_by_id` maps carry the canonical role-aware clause numbers (same scheme as
    the two-pane view) so the context number is the REAL document number, not `_locate`'s
    inflated positional path."""
    kind = change.change_kind
    baseline_ctx = _empty_context("baseline")
    their_ctx = _empty_context("their")

    if kind in ("edited", "deleted"):
        baseline_ctx = _build_side_context(
            "baseline", baseline_tree, change.node_id, baseline_number_by_id
        )
    elif kind == "abstain":
        baseline_ctx = _build_side_context(
            "baseline", baseline_tree, change.proposed_parent_id, baseline_number_by_id
        )

    if kind in ("new", "abstain"):
        incoming_body = await _reconstruct_incoming(
            conn, contract_id, change.proposed_parent_id, change.hunks
        )
        their_id, ambiguous = _find_incoming_id(
            received_tree.nodes if received_tree is not None else [], incoming_body
        )
        if ambiguous:
            log.warning(
                "revision_review.change_incoming_ambiguous",
                change_id=change.id,
                contract_id=contract_id,
                kind=kind,
            )
        their_ctx = _build_side_context("their", received_tree, their_id, revised_number_by_id)

    return ChangeContext(their=their_ctx, baseline=baseline_ctx)


async def _attach_change_context(
    conn: Any,
    session: StoredRevisionSession,
    changes: list[ReviewChange],
    baseline_tree: ContractTreeResponse | None,
    received_tree: ContractTreeResponse | None,
    baseline_number_by_id: dict[str, str],
    revised_number_by_id: dict[str, str],
) -> None:
    """Populate `context` on every change (both phases) from the already-resolved trees +
    role-aware number maps (computed once by `_resolve_document`)."""
    for change in changes:
        change.context = await _change_context(
            conn,
            session.contract_id,
            change,
            baseline_tree,
            received_tree,
            baseline_number_by_id,
            revised_number_by_id,
        )


async def get_review_payload(conn: Any, session_id: str) -> ReviewPayload:
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    session = _to_session(session_row)

    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]
    _stamp_clusters(changes)

    resolved = await _resolve_document(conn, session)
    baseline_number_by_id = {
        n.node_id: n.clause_number for n in resolved.baseline if n.clause_number
    }
    revised_number_by_id = {n.node_id: n.clause_number for n in resolved.revised if n.clause_number}
    await _attach_change_context(
        conn,
        session,
        changes,
        resolved.baseline_tree,
        resolved.revised_tree,
        baseline_number_by_id,
        revised_number_by_id,
    )

    abstains = sorted(
        (c for c in changes if c.change_kind == "abstain"),
        key=lambda c: c.match_confidence if c.match_confidence is not None else 1.0,
    )

    settled = [c for c in changes if c.change_kind != "abstain"]
    phase2 = sorted(settled, key=_document_order_key(resolved))

    return ReviewPayload(
        session=session,
        phase1=ReviewPhase1(abstains=abstains, tree_anomalies=[]),
        phase2=phase2,
    )


# --------------------------------------------------------------------------- #
# Two-pane document view (F03c rework) — read-only render data                  #
# --------------------------------------------------------------------------- #


def derive_document_change_kinds(change: ReviewChange) -> list[DocumentChangeKind]:
    """Node-level overlay kinds for a settled change (pure; abstains excluded).

    - new     -> ["added"]
    - deleted -> ["deleted"]
    - edited  -> ["modified"] (its hunks are intra-clause insertions/replacements/
                 deletions — text changed WITHIN the node, which is a node-level
                 "modified", never a node add/delete). [] if it carries no hunk.

    "shifted" (a moved/reordered clause) is NEVER emitted: F03b stages no revised-side
    position for a matched node and stages no change row at all for an unedited move, so
    a position change is not a derivable signal. See the report / DEV_TODO follow-up."""
    if change.change_kind == "new":
        return ["added"]
    if change.change_kind == "deleted":
        return ["deleted"]
    if change.change_kind == "edited":
        return ["modified"] if change.hunks else []
    return []


async def _roles_for(conn: Any, node_ids: list[str]) -> dict[str, Role]:
    """Map node_id -> live `nodes.role` for the given ids (one batched query, no N+1).
    Ids absent from `nodes` (e.g. a non-joinable synthetic as_received id) are omitted,
    so the caller falls back to the snapshot default."""
    if not node_ids:
        return {}
    rows = await conn.fetch(_SELECT_NODE_ROLES, node_ids)
    return {str(r["id"]): cast(Role, r["role"]) for r in rows}


def _revised_to_incoming_clause_nodes(tree: list[SnapshotNode]) -> list[ClauseNode]:
    """Adapt the as_received snapshot (the REVISED side) back to matcher INCOMING
    `ClauseNode`s, reconstructing the exact import-time incoming shape so the match
    map reproduces F03b's import run. `incoming_to_snapshot_nodes` froze each node's
    flat document-order index as the snapshot id (`str(index)`) and its parent's index
    as `parent_id`, so `order = int(id)` and `parent = int(parent_id)` recover the
    `incoming_to_clause_nodes` keying byte-for-byte (`id=None`, body=canonical text)."""
    out: list[ClauseNode] = []
    for n in tree:
        if n.is_deleted:
            continue
        out.append(
            ClauseNode(
                id=None,
                parent=int(n.parent_id) if n.parent_id is not None else None,
                order=int(n.id),
                heading="",
                body=(n.body or "").strip(),
                role="clause",
            )
        )
    return out


async def _inherit_revised_roles(
    conn: Any,
    baseline_snapshot_id: str,
    revised_snapshot_id: str | None,
    baseline: list[DocumentNode],
    revised: list[DocumentNode],
) -> RevisionMatchResult | None:
    """DD-28/DD-54 classification inheritance at render time: a revision is a DIFF, not
    a fresh import, so every revised node that MATCHES a baseline node inherits that
    baseline node's operator-confirmed `role` — unchanged, reworded, AND moved alike;
    only genuinely-NEW revised nodes keep the snapshot default `clause`.

    Without this the revised (as_received) side — whose synthetic ids do NOT join live
    `nodes` — renders EVERY clause, recital, note and front/back-matter line as a
    generic `clause` (Lilly's 51-vs-20-clauses bug). Reuses the de-risked pure matcher
    `match_revision` on the SAME adapter inputs F03b's import used (baseline snapshot
    tree + the as_received tree reconstructed to its import-time incoming shape), so the
    map reproduces import exactly. One in-memory pass — the baseline roles are already
    enriched in `baseline`; no per-node query.

    Returns the `RevisionMatchResult` (baseline_id <-> incoming-index map) so the read
    path can reuse it for document-order placement; None when no revised side / snapshot."""
    if not revised or revised_snapshot_id is None:
        return None
    baseline_snapshot = await get_snapshot(conn, baseline_snapshot_id)
    revised_snapshot = await get_snapshot(conn, revised_snapshot_id)
    if (
        baseline_snapshot is None
        or baseline_snapshot.tree is None
        or revised_snapshot is None
        or revised_snapshot.tree is None
    ):
        return None

    result = match_revision(
        baseline_to_clause_nodes(baseline_snapshot.tree),
        _revised_to_incoming_clause_nodes(revised_snapshot.tree),
    )

    role_by_baseline_id = {n.node_id: n.role for n in baseline}
    revised_by_id = {n.node_id: n for n in revised}

    def inherit(incoming_index: int, baseline_id: str | None) -> None:
        if baseline_id is None:
            return
        role = role_by_baseline_id.get(baseline_id)
        node = revised_by_id.get(str(incoming_index))
        if role is not None and node is not None:
            node.role = role

    # Matched (incl. reworded/moved) inherit; abstains adopt their best candidate's role
    # (still a corresponding baseline node, surfaced for operator confirm). NEW keep default.
    for m in result.matches:
        inherit(m.incoming_index, m.baseline_id)
    for ab in result.abstains:
        inherit(ab.incoming_index, ab.best_baseline_id)
    return result


async def fetch_role_overrides(conn: Any, session_id: str) -> dict[str, Role]:
    """All operator role overrides for a session: revised synthetic node_id -> role
    (one batched query, no N+1). A row with NULL role cannot exist (the clear path
    DELETEs), so every returned value is a concrete role."""
    rows = await conn.fetch(_SELECT_ROLE_OVERRIDES, session_id)
    return {str(r["node_id"]): cast(Role, r["role"]) for r in rows if r["role"] is not None}


def _apply_role_overrides(revised: list[DocumentNode], overrides: dict[str, Role]) -> None:
    """Replace each revised node's render-time role with the operator override (Phase 1).
    Applied AFTER `_inherit_revised_roles` and BEFORE `_assign_clause_numbers`, so a
    re-type to/from `clause` correctly changes the role-aware numbering. In place."""
    if not overrides:
        return
    for n in revised:
        role = overrides.get(n.node_id)
        if role is not None:
            n.role = role


async def set_node_role_override(
    conn: Any, contract_id: str, session_id: str, node_id: str, role: Role | None
) -> NodeRoleOverrideResult:
    """Upsert (role set) or clear (role None -> DELETE) a revised node's role override.
    Validates the session exists and belongs to the contract (mismatch -> 404 via
    SessionNotFound). `role` is already `Role`-validated at the route (request model)."""
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    if str(session_row["contract_id"]) != contract_id:
        raise SessionNotFound(session_id)
    if role is None:
        await conn.execute(_DELETE_ROLE_OVERRIDE, session_id, node_id)
    else:
        await conn.execute(_UPSERT_ROLE_OVERRIDE, session_id, node_id, role)
    return NodeRoleOverrideResult(node_id=node_id, role=role)


def _flatten_document(tree: ContractTreeResponse | None) -> list[DocumentNode]:
    """Flatten a nested snapshot tree to reading-order `DocumentNode`s carrying depth
    only — `clause_number` is left None here and stamped ROLE-AWARE by
    `_assign_clause_numbers` AFTER roles are resolved on each side (the snapshot does
    not carry role, so positional numbering at flatten time would number front/back-
    matter as if it were operative clauses — DD-02/DD-43, Lilly's "51 clauses" bug).
    Text is the node's canonical body (`heading or body`, the exact string the hunk
    offsets index into). None tree -> empty list."""
    out: list[DocumentNode] = []

    def walk(items: list[NodeTreeItem], depth: int) -> None:
        for item in items:
            heading = (item.heading or "").strip()
            body = (item.body or "").strip()
            out.append(
                DocumentNode(
                    node_id=item.id,
                    clause_number=None,
                    role=item.role,
                    depth=depth,
                    text=(item.heading or item.body or "").strip() or None,
                    # Heading-only node (heading set, empty body) — import's `typeLabel ===
                    # "Heading"`. `text` above is `heading or body`, so it carries the heading.
                    is_heading=bool(heading) and not body,
                )
            )
            walk(item.children, depth + 1)

    if tree is not None:
        walk(tree.nodes, 0)
    return out


def _assign_clause_numbers(nodes: list[DocumentNode]) -> None:
    """Stamp the canonical DD-02/DD-43 clause number onto a resolved-role flat document
    IN PLACE: only `role == "clause"` nodes get a dotted decimal number (counting only
    clause-role siblings within each parent), every non-clause node gets None.

    REUSES the single numbering source — `import_.numbering.derive_numbers`, the same
    function `export.render_docx._plan` / `cross_references.build_number_map` route
    through — so these numbers MATCH Mode A and export. `derive_numbers` needs a
    `ParsedTree`; the flat list is in pre-order DFS, so each node's parent is the
    nearest preceding node at `depth - 1` and `order_index = list position` preserves
    sibling order. Must run AFTER role resolution (`_roles_for` / `_inherit_revised_roles`)
    so the role-aware skip is correct."""
    last_at_depth: dict[int, int] = {}
    tree_nodes: list[TreeNode] = []
    for i, n in enumerate(nodes):
        parent_index = last_at_depth.get(n.depth - 1) if n.depth > 0 else None
        last_at_depth[n.depth] = i
        tree_nodes.append(
            TreeNode(
                index=i,
                parent_index=parent_index,
                depth=n.depth,
                order_index=i,
                kind="prose",
                text=n.text or "",
                role=n.role,
            )
        )
    numbers = derive_numbers(ParsedTree(nodes=tree_nodes))
    for i, n in enumerate(nodes):
        n.clause_number = numbers.get(i)


async def _abstain_match(
    conn: Any,
    contract_id: str,
    change: ReviewChange,
    received_tree: ContractTreeResponse | None,
) -> AbstainMatch:
    """Both sides of an abstain's proposed match. `baseline_node_id` = the staged
    baseline candidate (`proposed_parent_id`). The received node carries no stored
    linkage (only the candidate is staged), so it is recovered by reconstructing the
    incoming body and body-matching the as_received tree — exact where bodies are
    unique, first-of-duplicates (logged) otherwise, NULL when unrecoverable."""
    incoming_body = await _reconstruct_incoming(
        conn, contract_id, change.proposed_parent_id, change.hunks
    )
    received_id, ambiguous = _find_incoming_id(
        received_tree.nodes if received_tree is not None else [], incoming_body
    )
    if ambiguous:
        log.warning(
            "revision_review.abstain_incoming_ambiguous",
            change_id=change.id,
            contract_id=contract_id,
        )
    return AbstainMatch(
        change_id=change.id,
        baseline_node_id=change.proposed_parent_id,
        proposed_received_node_id=received_id,
        confidence=change.match_confidence,
    )


class _ResolvedDocument(NamedTuple):
    """The role-resolved, role-aware-numbered baseline + revised document trees plus the
    baseline<->revised match map — the single source of truth both `get_document_view`
    (two-pane render) and `get_review_payload` (change context numbers + document-order
    placement) read, so the two surfaces never disagree on a clause's number/position."""

    baseline: list[DocumentNode]
    revised: list[DocumentNode]
    baseline_tree: ContractTreeResponse | None
    revised_tree: ContractTreeResponse | None
    match: RevisionMatchResult | None
    received_snapshot_id: str | None


async def _resolve_document(conn: Any, session: StoredRevisionSession) -> _ResolvedDocument:
    """Resolve both document sides to role-aware-numbered `DocumentNode`s ONCE:
    baseline roles from live `nodes`, revised roles inherited from their matched baseline
    (DD-28/DD-54) + operator overrides, then the canonical role-aware `derive_numbers`
    pass on each side. Also returns the matcher result so callers can place new/edited
    nodes by REAL document position instead of the staged (often null) parent pointer."""
    baseline_tree = await get_snapshot_tree(conn, session.contract_id, session.baseline_snapshot_id)
    received_id = await conn.fetchval(_FIND_RECEIVED_POINTER, session.contract_id, session.source)
    revised_tree = (
        await get_snapshot_tree(conn, session.contract_id, str(received_id))
        if received_id is not None
        else None
    )

    baseline = _flatten_document(baseline_tree)
    role_by_id = await _roles_for(conn, [n.node_id for n in baseline])
    for n in baseline:
        role = role_by_id.get(n.node_id)
        if role is not None:
            n.role = role
    _assign_clause_numbers(baseline)

    revised = _flatten_document(revised_tree)
    received_snapshot_id = str(received_id) if received_id is not None else None
    match = await _inherit_revised_roles(
        conn, session.baseline_snapshot_id, received_snapshot_id, baseline, revised
    )
    _apply_role_overrides(revised, await fetch_role_overrides(conn, session.id))
    _assign_clause_numbers(revised)

    return _ResolvedDocument(
        baseline=baseline,
        revised=revised,
        baseline_tree=baseline_tree,
        revised_tree=revised_tree,
        match=match,
        received_snapshot_id=received_snapshot_id,
    )


def _baseline_to_revised_order(
    match: RevisionMatchResult | None, revised_order_by_id: dict[str, int]
) -> dict[str, int]:
    """Map each surviving baseline node id -> its position in the REVISED reading order
    (matched first, abstain best-candidate as fallback). The bridge that lets an edited
    clause be placed in revised-document order even though its change row carries only the
    baseline node id."""
    out: dict[str, int] = {}
    if match is None:
        return out
    for m in match.matches:
        pos = revised_order_by_id.get(str(m.incoming_index))
        if pos is not None:
            out[m.baseline_id] = pos
    for ab in match.abstains:
        if ab.best_baseline_id is not None and ab.best_baseline_id not in out:
            pos = revised_order_by_id.get(str(ab.incoming_index))
            if pos is not None:
                out[ab.best_baseline_id] = pos
    return out


def _document_order_key(
    resolved: _ResolvedDocument,
) -> Callable[[ReviewChange], tuple[float, int]]:
    """Sort key placing every settled change in REVISED reading order — the position the
    clause actually occupies in the counterparty's document.

    The pre-fix `order_key` keyed `new` changes by their staged `proposed_parent_id`, which
    F03b leaves NULL for a genuinely-new top-level clause (and for a child of another new
    clause). A null parent anchored at -1, so those clauses floated to the TOP of the stream
    instead of sitting where the counterparty put them. Here a `new` clause is keyed by its
    OWN revised reading position (`received_node_id`), an `edited` clause by its matched
    revised position, and a `deleted` clause (absent from the revised side) by the slot just
    after its nearest surviving predecessor — so nothing floats and the seam around an
    inserted section stays in document order."""
    revised_order_by_id = {n.node_id: i for i, n in enumerate(resolved.revised)}
    b2r = _baseline_to_revised_order(resolved.match, revised_order_by_id)
    end = float(len(resolved.revised))

    # Deleted baseline nodes have no revised position: anchor each just after the nearest
    # preceding baseline node that DID survive (matched), walking baseline reading order.
    deleted_anchor: dict[str, float] = {}
    last_surviving = -1.0
    for n in resolved.baseline:
        pos = b2r.get(n.node_id)
        if pos is not None:
            last_surviving = float(pos)
        else:
            deleted_anchor[n.node_id] = last_surviving + 0.5

    def key(c: ReviewChange) -> tuple[float, int]:
        if c.change_kind == "new":
            if c.received_node_id is not None and c.received_node_id in revised_order_by_id:
                pos = float(revised_order_by_id[c.received_node_id])
            elif c.proposed_parent_id is not None and c.proposed_parent_id in b2r:
                pos = b2r[c.proposed_parent_id] + 0.5
            else:
                pos = end
            tiebreak = int(c.received_node_id) if c.received_node_id is not None else 0
            return (pos, tiebreak)
        node_id = cast(str, c.node_id)
        if node_id in b2r:
            return (float(b2r[node_id]), 0)
        return (deleted_anchor.get(node_id, end), 0)

    return key


# --------------------------------------------------------------------------- #
# Projected reading order (verdict-aware) — the single linear sequence          #
# --------------------------------------------------------------------------- #


def _primary_verdict(change: ReviewChange) -> StoredHunkVerdict:
    """The whole-node verdict for a new/deleted change (one hunk after staging /
    match-confirm). `pending` when no hunk is staged or none decided — pending counts
    as APPLIED for projection (an undecided insert is shown inserted, an undecided
    delete is shown removed) so numbering reflects the live in-progress document."""
    return change.hunks[0].verdict if change.hunks else "pending"


def _assign_projected_numbers(nodes: list[ProjectedNode]) -> None:
    """Stamp the role-aware DD-02/DD-43 clause number onto the projected sequence IN
    PLACE, reusing the canonical `derive_numbers` (same scheme as Mode A / export /
    the two-pane view). The flat list is pre-order DFS, so each node's parent is the
    nearest preceding node at `depth - 1`. A node with `numbered=False` (an accepted /
    pending deletion — removed from the projected tree) is fed to `derive_numbers` as a
    non-clause role so it gets NO number AND consumes NO sibling position, which is what
    renumbers the survivors down/up as deletions are kept or removed."""
    last_at_depth: dict[int, int] = {}
    tree_nodes: list[TreeNode] = []
    for i, n in enumerate(nodes):
        parent_index = last_at_depth.get(n.depth - 1) if n.depth > 0 else None
        last_at_depth[n.depth] = i
        role: Role = n.role if n.numbered else "drafting_note"
        tree_nodes.append(
            TreeNode(
                index=i,
                parent_index=parent_index,
                depth=n.depth,
                order_index=i,
                kind="prose",
                text=n.text or "",
                role=role,
            )
        )
    numbers = derive_numbers(ParsedTree(nodes=tree_nodes))
    for i, n in enumerate(nodes):
        n.clause_number = numbers.get(i)


def _build_projected(
    resolved: _ResolvedDocument, changes: list[ReviewChange]
) -> list[ProjectedNode]:
    """Project the baseline into a single linear reading order with every NON-REJECTED
    change applied, then role-aware-number it.

    Placement is by REVISED position, not the staged (often NULL) `proposed_parent_id`
    that floated new clauses to the top: each baseline survivor carries its revised
    coordinate (`_baseline_to_revised_order`), each non-rejected added clause its own
    revised coordinate (`received_node_id` -> revised index), and each deleted baseline
    node (absent from the revised side) is anchored just after its nearest surviving
    predecessor in baseline order. Sorting by that coordinate reproduces the
    counterparty's document order (a new section lands AFTER the preceding section's whole
    subtree because revised positions are DFS order), so the frontend renders it linearly.

    Verdict semantics (read from the persisted hunk verdicts, recomputed per request):
      - added rejected   -> emitted IN PLACE at its real revised position but NOT numbered
        (numbered=False, clause_number=None) — a struck trace of the rejected addition, the
        symmetric mirror of an accepted/pending deletion shown-in-place. It consumes no
        sibling position, so the surrounding clauses renumber back exactly as if it were absent.
      - added otherwise  -> emitted, numbered.
      - deleted rejected/modified -> clause survives, numbered.
      - deleted accepted/pending  -> clause shown in place, NOT numbered (numbered=False).
      - edited / unchanged -> always present, numbered."""
    baseline = resolved.baseline
    revised = resolved.revised
    revised_order_by_id = {n.node_id: i for i, n in enumerate(revised)}
    if resolved.match is not None:
        b2r = _baseline_to_revised_order(resolved.match, revised_order_by_id)
    else:
        b2r = {n.node_id: i for i, n in enumerate(baseline)}

    change_by_baseline: dict[str, ReviewChange] = {
        c.node_id: c
        for c in changes
        if c.change_kind in ("edited", "deleted") and c.node_id is not None
    }

    events: list[tuple[float, int, ProjectedNode]] = []

    last_surv = -1.0
    for bidx, bnode in enumerate(baseline):
        change = change_by_baseline.get(bnode.node_id)
        kind: DocumentChangeKind | None = None
        change_id: str | None = None
        numbered = True
        if change is not None and change.change_kind == "edited":
            kind = "modified"
            change_id = change.id
        elif change is not None and change.change_kind == "deleted":
            kind = "deleted"
            change_id = change.id
            numbered = _primary_verdict(change) in ("rejected", "modified")
        pnode = ProjectedNode(
            node_id=bnode.node_id,
            clause_number=None,
            role=bnode.role,
            depth=bnode.depth,
            text=bnode.text,
            change_id=change_id,
            change_kind=kind,
            numbered=numbered,
            is_heading=bnode.is_heading,
        )
        rpos = b2r.get(bnode.node_id)
        if rpos is not None:
            last_surv = float(rpos)
            events.append((float(rpos), bidx, pnode))
        else:
            events.append((last_surv + 0.5, bidx, pnode))

    end = float(len(revised))
    for c in changes:
        if c.change_kind != "new":
            continue
        # A rejected addition is still EMITTED (struck trace) but unnumbered, so it consumes
        # no sibling position and the surrounding clauses renumber as if it were absent.
        numbered = _primary_verdict(c) != "rejected"
        flatpos = revised_order_by_id.get(c.received_node_id) if c.received_node_id else None
        if flatpos is not None:
            rnode = revised[flatpos]
            node_id, role_, depth, text = rnode.node_id, rnode.role, rnode.depth, rnode.text
            is_heading = rnode.is_heading
            coord = float(flatpos)
        else:
            node_id = c.received_node_id or c.id
            role_, depth = "clause", 0
            text = c.hunks[0].proposed_text if c.hunks else None
            is_heading = False
            coord = end
        events.append(
            (
                coord,
                -1,
                ProjectedNode(
                    node_id=node_id,
                    clause_number=None,
                    role=role_,
                    depth=depth,
                    text=text,
                    change_id=c.id,
                    change_kind="added",
                    numbered=numbered,
                    is_heading=is_heading,
                ),
            )
        )

    events.sort(key=lambda e: (e[0], e[1]))
    projected = [e[2] for e in events]
    _assign_projected_numbers(projected)
    return projected


async def projected_clause_numbers(
    conn: Any, contract_id: str, session_id: str
) -> dict[str, str]:
    """The DD-88 PROJECTED clause number per node_id for this session's live review state — the
    SAME numbers the review pane shows, reusing the canonical `_build_projected` projection (not a
    second numbering path). F35/DD-92: the recommend grounding labels referenceable clauses with
    these live projected numbers, not the baseline ones, so a clause anchor's resolved number
    matches the pane. Keys are baseline node_ids (== live node ids); unnumbered projected nodes (a
    non-clause, or a pending/accepted deletion) are omitted. Read-only."""
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    session = _to_session(session_row)
    if session.contract_id != contract_id:
        raise SessionNotFound(session_id)
    resolved = await _resolve_document(conn, session)
    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]
    _stamp_clusters(changes)
    projected = _build_projected(resolved, changes)
    return {n.node_id: n.clause_number for n in projected if n.clause_number is not None}


async def get_document_view(conn: Any, contract_id: str, session_id: str) -> RevisionDocumentView:
    """The two-pane document payload (F03c rework): the baseline + revised document trees
    as ordered nodes, the settled-change overlay keyed to the revised side, the abstain
    match-confirm pairs, and the verdict-aware `projected` linear reading order. Read-only;
    no hunk redline text (fetched on click)."""
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    session = _to_session(session_row)
    if session.contract_id != contract_id:
        raise SessionNotFound(session_id)

    # Role-resolve + role-aware-number both sides ONCE (the single source of truth shared
    # with get_review_payload, so the two-pane render and the change context never disagree
    # on a clause's number). Baseline roles join live `nodes`; revised (as_received synthetic
    # ids that don't join) inherit their matched baseline role (DD-28/DD-54) + overrides.
    resolved = await _resolve_document(conn, session)
    baseline, revised, revised_tree = resolved.baseline, resolved.revised, resolved.revised_tree

    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]
    _stamp_clusters(changes)

    overlay = [
        DocumentChange(
            change_id=c.id,
            node_id=c.node_id,
            proposed_parent_id=c.proposed_parent_id,
            received_node_id=c.received_node_id,
            kinds=derive_document_change_kinds(c),
            decided=c.status == "complete",
            hunk_count=c.hunk_count,
            hunks_decided=c.hunks_decided,
        )
        for c in changes
        if c.change_kind != "abstain"
    ]
    abstain_matches = [
        await _abstain_match(conn, session.contract_id, c, revised_tree)
        for c in changes
        if c.change_kind == "abstain"
    ]

    return RevisionDocumentView(
        baseline=baseline,
        revised=revised,
        changes=overlay,
        abstain_matches=abstain_matches,
        projected=_build_projected(resolved, changes),
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


async def decide_cluster(
    conn: Any, session_id: str, cluster_id_: str, req: ClusterDecideRequest
) -> ReviewPayload:
    """DD-89 grouped-stop decision (F34): apply ONE verdict to every member hunk of a cluster in a
    single transaction (decide-once → fans to all). Resolves the cluster's members by re-deriving
    the SAME `_stamp_clusters` grouping the read payload exposed (so cluster ids can't drift),
    reuses `_map_hunk_verdict` per member (each `counter` reads its OWN staged counter-text), then
    refreshes progress for every DISTINCT affected change (members span multiple change rows).
    Returns the refreshed review payload (members span clauses — one patch wouldn't cover them)."""
    await _assert_session_reviewing(conn, session_id)
    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]
    _stamp_clusters(changes)

    members = [h for c in changes for h in c.hunks if h.cluster_id == cluster_id_]
    if not members:
        raise ClusterNotFound(cluster_id_)

    hunk_req = HunkDecideRequest(verdict=req.verdict, final_text=req.final_text)
    affected_change_ids = {h.change_id for h in members}
    async with conn.transaction():
        for h in members:
            verdict, final_text = _map_hunk_verdict(hunk_req, _hunk_decision_fields(h))
            await conn.execute(_UPDATE_HUNK_VERDICT, h.id, verdict, final_text)
        for cid in affected_change_ids:
            await _refresh_progress(conn, cid, session_id)
    return await get_review_payload(conn, session_id)


def _hunk_decision_fields(hunk: ReviewHunk) -> dict[str, str | None]:
    """The three text fields `_map_hunk_verdict` reads, as a row-shaped dict so the SAME mapper
    serves both the asyncpg per-hunk path and the cluster (ReviewHunk) path."""
    return {
        "proposed_text": hunk.proposed_text,
        "original_text": hunk.original_text,
        "donna_counter_text": hunk.donna_counter_text,
    }


def _descendant_received_ids(tree: ContractTreeResponse | None, root_received_id: str) -> set[str]:
    """All proper-descendant node ids under the as_received (revised) node
    `root_received_id` (the rejected added node's subtree root). The as_received snapshot
    tree node ids ARE the `received_node_id` values change rows carry, so these map straight
    back to change rows. Empty set when the root is absent / no tree."""
    if tree is None:
        return set()

    def find(items: list[NodeTreeItem]) -> NodeTreeItem | None:
        for item in items:
            if str(item.id) == root_received_id:
                return item
            hit = find(item.children)
            if hit is not None:
                return hit
        return None

    root = find(tree.nodes)
    if root is None:
        return set()
    out: set[str] = set()

    def collect(items: list[NodeTreeItem]) -> None:
        for item in items:
            out.add(str(item.id))
            collect(item.children)

    collect(root.children)
    return out


async def _cascade_reject_added_descendants(
    conn: Any, session: StoredRevisionSession, parent_row: Any
) -> None:
    """Rejecting an ADDED parent clause auto-rejects every ADDED descendant in the same txn
    (you can't keep a sub-clause without its added section). ASYMMETRIC: accept does NOT
    cascade (children stay pending for individual review). Descendants are located in the
    as_received (revised) tree under the parent's `received_node_id` subtree root (loaded the
    same way the read path does, via `_resolve_document`), mapped back to change rows by
    `received_node_id`; only NEW/added descendants are rejected — any other kind is left."""
    root_received_id = parent_row["received_node_id"]
    if root_received_id is None:
        return
    resolved = await _resolve_document(conn, session)
    descendant_ids = _descendant_received_ids(resolved.revised_tree, str(root_received_id))
    if not descendant_ids:
        return
    for crow in await conn.fetch(_SELECT_CHANGES, session.id):
        if _derive_kind(crow) != "new":
            continue
        rid = crow["received_node_id"]
        if rid is None or str(rid) not in descendant_ids:
            continue
        child_id = str(crow["id"])
        child_hunks = (await _hunks_for(conn, [child_id])).get(child_id, [])
        for h in child_hunks:
            await conn.execute(_UPDATE_HUNK_VERDICT, h.id, "rejected", h.original_text)
        await _refresh_progress(conn, child_id, session.id)


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
        if req.verdict == "reject" and kind == "new":
            session = _to_session(await conn.fetchrow(_SELECT_SESSION, str(row["session_id"])))
            await _cascade_reject_added_descendants(conn, session, row)
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
