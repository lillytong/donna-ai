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
from backend.models.revision_match import ClauseNode
from backend.models.revision_review import (
    AbstainMatch,
    ApplyResult,
    ChangeContext,
    ChangeContextSide,
    ChangeKind,
    ConfirmMatchRequest,
    DocumentChange,
    DocumentChangeKind,
    DocumentNode,
    HunkDecideRequest,
    NodeDecideRequest,
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

_LIVE_NODES = """
SELECT id, parent_id, order_index FROM nodes
WHERE contract_id = $1 AND is_deleted = false
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
    side: str, tree: ContractTreeResponse | None, target_id: str | None
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
    return ChangeContextSide(
        side=cast(Any, side),
        found=True,
        number=located.number,
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
) -> ChangeContext:
    """Resolve both sides of a change's structural context. Resolution is EXACT for
    settled changes (the change row carries the node id) and a body-match heuristic
    only where no incoming reference is stored (new / abstain):
      - edited / deleted ⇒ `baseline` located by `node_id`.
      - new              ⇒ `their` body-matched in the as_received tree.
      - abstain          ⇒ `baseline` = candidate (`proposed_parent_id`) + `their`."""
    kind = change.change_kind
    baseline_ctx = _empty_context("baseline")
    their_ctx = _empty_context("their")

    if kind in ("edited", "deleted"):
        baseline_ctx = _build_side_context("baseline", baseline_tree, change.node_id)
    elif kind == "abstain":
        baseline_ctx = _build_side_context("baseline", baseline_tree, change.proposed_parent_id)

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
        their_ctx = _build_side_context("their", received_tree, their_id)

    return ChangeContext(their=their_ctx, baseline=baseline_ctx)


async def _attach_change_context(
    conn: Any, session: StoredRevisionSession, changes: list[ReviewChange]
) -> None:
    """Populate `context` on every change (both phases). Resolves the two snapshot
    trees ONCE per payload; the received tree is the as_received snapshot the session's
    `received` pointer points at (reliable while the session is `reviewing`)."""
    if not changes:
        return
    baseline_tree = await get_snapshot_tree(conn, session.contract_id, session.baseline_snapshot_id)
    received_id = await conn.fetchval(_FIND_RECEIVED_POINTER, session.contract_id, session.source)
    received_tree = (
        await get_snapshot_tree(conn, session.contract_id, str(received_id))
        if received_id is not None
        else None
    )
    for change in changes:
        change.context = await _change_context(
            conn, session.contract_id, change, baseline_tree, received_tree
        )


async def get_review_payload(conn: Any, session_id: str) -> ReviewPayload:
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    session = _to_session(session_row)

    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]
    await _attach_change_context(conn, session, changes)

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
) -> None:
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
    enriched in `baseline`; no per-node query."""
    if not revised or revised_snapshot_id is None:
        return
    baseline_snapshot = await get_snapshot(conn, baseline_snapshot_id)
    revised_snapshot = await get_snapshot(conn, revised_snapshot_id)
    if (
        baseline_snapshot is None
        or baseline_snapshot.tree is None
        or revised_snapshot is None
        or revised_snapshot.tree is None
    ):
        return

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
            out.append(
                DocumentNode(
                    node_id=item.id,
                    clause_number=None,
                    role=item.role,
                    depth=depth,
                    text=(item.heading or item.body or "").strip() or None,
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


async def get_document_view(conn: Any, contract_id: str, session_id: str) -> RevisionDocumentView:
    """The two-pane document payload (F03c rework): the baseline + revised document trees
    as ordered nodes, the settled-change overlay keyed to the revised side, and the
    abstain match-confirm pairs. Read-only; no hunk redline text (fetched on click)."""
    session_row = await conn.fetchrow(_SELECT_SESSION, session_id)
    if session_row is None:
        raise SessionNotFound(session_id)
    session = _to_session(session_row)
    if session.contract_id != contract_id:
        raise SessionNotFound(session_id)

    baseline_tree = await get_snapshot_tree(conn, session.contract_id, session.baseline_snapshot_id)
    received_id = await conn.fetchval(_FIND_RECEIVED_POINTER, session.contract_id, session.source)
    revised_tree = (
        await get_snapshot_tree(conn, session.contract_id, str(received_id))
        if received_id is not None
        else None
    )

    baseline = _flatten_document(baseline_tree)
    # Recover the operator-confirmed DD-54 role the snapshot shape drops: baseline node
    # ids are real live `nodes` ids, so they join. The revised (as_received) tree carries
    # synthetic ids that do NOT join — its matched nodes instead INHERIT the baseline
    # role via `_inherit_revised_roles` below (DD-28); only genuinely-new clauses keep
    # the snapshot default `clause` (the frontend falls back to a generic label).
    role_by_id = await _roles_for(conn, [n.node_id for n in baseline])
    for n in baseline:
        role = role_by_id.get(n.node_id)
        if role is not None:
            n.role = role
    # Numbers are role-aware (DD-02/DD-43): stamped only after the baseline roles are
    # resolved so front/back-matter is excluded from the clause count.
    _assign_clause_numbers(baseline)
    revised = _flatten_document(revised_tree)
    # DD-28/DD-54: a revision is a diff — matched revised nodes INHERIT the baseline's
    # operator-confirmed role instead of all falling back to the snapshot default.
    await _inherit_revised_roles(
        conn,
        session.baseline_snapshot_id,
        str(received_id) if received_id is not None else None,
        baseline,
        revised,
    )
    # Re-derive role-aware numbers on the revised side AFTER `_inherit_revised_roles`
    # has fixed its roles (it runs after flatten, which left numbers None).
    _assign_clause_numbers(revised)

    change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
    hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r in change_rows])
    changes = [_to_change(r, hunks_by_change.get(str(r["id"]), [])) for r in change_rows]

    overlay = [
        DocumentChange(
            change_id=c.id,
            node_id=c.node_id,
            proposed_parent_id=c.proposed_parent_id,
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
