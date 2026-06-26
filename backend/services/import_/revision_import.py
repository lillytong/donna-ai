"""Mode B Path-B revision import (F03b) — clean-.docx revision → review staging.

The pipeline, leaf-up:
  1. Detect parse path — scan the upload for `<w:ins>`/`<w:del>`. Present →
     tracked-changes (Path A) → raise `TrackedChangesNotSupported` (deferred). Else
     Path B (clean diff).
  2. Parse the incoming .docx with the Mode A chain (`read_docx` → `build_tree`),
     adapt to `list[ClauseNode]`.
  3. Resolve the baseline = the `last_shared_with_{party}` snapshot (the source
     picker chooses the party); adapt its `SnapshotNode` tree → `list[ClauseNode]`.
     No such snapshot → raise `BaselineMissing`.
  4. `match_revision(baseline, incoming)` — the built + de-risked matcher (precision
     1.000); we WIRE it, never modify it.
  5. Persist in one transaction: freeze the incoming tree as an `as_received`
     snapshot + advance the `received` pointer (DD-48); open a
     `counterparty_revision_session`; stage one `counterparty_revision_changes` row
     per matched-with-edits / new / deleted / abstain bucket, each with its
     deterministic `difflib` hunks. NO issues are created (SPEC §11 step 5).

Single-open-session guard: a second import is rejected while a `reviewing` session
is open for the contract (baseline-collision rule). Hunk `significance` defaults to
`substantive` (the safe default) — Donna's trivial/substantive classification is a
deferred follow-up. Matched nodes inherit role/has_placeholder from the baseline
(DD-28/DD-54) — not re-classified here (this staging build does not touch live
`nodes`); only `new` nodes are genuinely new.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, cast

import structlog

from backend.models.audit import EVENT_REVISION_IMPORTED, AuditEvent
from backend.models.contract_tree import ParsedTree, TreeNode
from backend.models.revision_import import (
    HunkDraft,
    HunkType,
    RevisionImportRequest,
    RevisionImportResponse,
)
from backend.models.revision_match import ClauseNode, MatchedPair, RevisionMatchResult
from backend.models.snapshots import SnapshotNode, SnapshotPointerTarget
from backend.services.audit_repo import record_event
from backend.services.import_.docx_reader import count_tracked_changes, read_docx
from backend.services.import_.revision_match import match_revision
from backend.services.import_.tree_builder import build_tree
from backend.services.snapshot import get_snapshot, snapshot_tree

log = structlog.get_logger()

# Operator-facing source → (DB pointer party, DB session source). The pointer party
# is the schema's `legal_team`; the request uses the shorter `legal` (DD-47).
_SOURCE_TO_PARTY: dict[str, str] = {"counterparty": "counterparty", "legal": "legal_team"}
_SOURCE_TO_DB_SOURCE: dict[str, str] = {"counterparty": "counterparty", "legal": "legal_team"}


class RevisionImportError(Exception):
    """Base for the import's typed failures; the route maps `status_code`/`detail`."""

    status_code: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TrackedChangesNotSupported(RevisionImportError):
    status_code = 422


class BaselineMissing(RevisionImportError):
    status_code = 409


class SessionAlreadyOpen(RevisionImportError):
    status_code = 409


_FIND_SHARED_POINTER = """
SELECT snapshot_id
FROM snapshot_pointers
WHERE contract_id = $1 AND party = $2 AND direction = 'shared'
"""

_OPEN_SESSION_EXISTS = """
SELECT 1 FROM counterparty_revision_sessions
WHERE contract_id = $1 AND status = 'reviewing'
LIMIT 1
"""

_SNAPSHOT_COUNT = "SELECT count(*) FROM contract_snapshots WHERE contract_id = $1"

_INSERT_SESSION = """
INSERT INTO counterparty_revision_sessions
    (contract_id, baseline_snapshot_id, source, source_filename, parse_path,
     status, changes_count)
VALUES ($1, $2, $3, $4, 'clean_diff', 'reviewing', $5)
RETURNING id
"""

_INSERT_CHANGE = """
INSERT INTO counterparty_revision_changes
    (session_id, node_id, proposed_parent_id, proposed_order_index,
     match_confidence, hunk_count, received_node_id)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING id
"""

_INSERT_HUNK = """
INSERT INTO counterparty_revision_hunks
    (change_id, hunk_type, significance, position_in_body, original_text, proposed_text)
VALUES ($1, $2, $3, $4, $5, $6)
"""


# --------------------------------------------------------------------------- #
# Canonical text + ClauseNode adapters (production siblings of the spike's)    #
# --------------------------------------------------------------------------- #


def _treenode_text(n: TreeNode) -> str:
    """Canonical comparison text for an incoming parsed node — flatten table rows so
    a table node still carries distinguishing text (matches the spike adapter)."""
    if n.kind == "table" and n.rows:
        return " ".join(c for row in n.rows for c in row if c).strip()
    return (n.text or "").strip()


def _snapshotnode_text(n: SnapshotNode) -> str:
    """Canonical comparison text for a baseline snapshot node: heading if present,
    else body. (Tables persist their content in `body` as flattened text.)"""
    return (n.heading or n.body or "").strip()


def incoming_to_clause_nodes(tree: ParsedTree) -> list[ClauseNode]:
    """Adapt a freshly parsed incoming tree → matcher input (incoming side).

    Incoming nodes have no persisted id, so `order` = `TreeNode.index` (the flat
    document-order position) doubles as the key, and `parent` = `parent_index` (the
    parent's order). Canonical text goes in `body`, heading left empty, so the
    matcher's `heading if heading else body` rule resolves to the node text on both
    sides (verbatim from the greenlit spike adapter)."""
    return [
        ClauseNode(
            id=None,
            parent=n.parent_index,
            order=n.index,
            heading="",
            body=_treenode_text(n),
            role=n.role,
        )
        for n in tree.nodes
    ]


def baseline_to_clause_nodes(tree: list[SnapshotNode]) -> list[ClauseNode]:
    """Adapt a baseline snapshot tree → matcher input (baseline side).

    Soft-deleted nodes are dropped (the counterparty never saw them). `order` is a
    pre-order DFS sequence over the live tree (parent before children, siblings by
    `order_index`) so it mirrors the incoming side's document-order index; `id` is
    the real node id (→ `change.node_id`), `parent` the parent's node id."""
    live = [n for n in tree if not n.is_deleted]
    children: dict[str | None, list[SnapshotNode]] = {}
    for n in live:
        children.setdefault(n.parent_id, []).append(n)
    for sibs in children.values():
        sibs.sort(key=lambda x: x.order_index)

    order_of: dict[str, int] = {}
    seq = 0

    def walk(parent_id: str | None) -> None:
        nonlocal seq
        for n in children.get(parent_id, []):
            order_of[n.id] = seq
            seq += 1
            walk(n.id)

    walk(None)
    # Any node orphaned by a missing parent still gets a deterministic order.
    for n in live:
        if n.id not in order_of:
            order_of[n.id] = seq
            seq += 1

    return [
        ClauseNode(
            id=n.id,
            parent=n.parent_id,
            order=order_of[n.id],
            heading="",
            body=_snapshotnode_text(n),
            role="clause",
        )
        for n in live
    ]


def incoming_to_snapshot_nodes(tree: ParsedTree) -> list[SnapshotNode]:
    """Freeze the parsed incoming tree as an `as_received` JSONB dump (DD-48). Ids are
    the synthetic flat indices (the received copy has no live-node ids); canonical
    text lands in `body`."""
    return [
        SnapshotNode(
            id=str(n.index),
            parent_id=None if n.parent_index is None else str(n.parent_index),
            order_index=n.order_index,
            content_type="table" if n.kind == "table" else "prose",
            heading=None,
            body=_treenode_text(n),
            is_deleted=False,
        )
        for n in tree.nodes
    ]


# --------------------------------------------------------------------------- #
# Deterministic hunk extraction (difflib — NOT the matcher's metric)           #
# --------------------------------------------------------------------------- #


def extract_hunks(baseline_body: str, incoming_body: str) -> list[HunkDraft]:
    """Word-level `difflib.SequenceMatcher` diff of two clause bodies → ordered
    insertion/deletion/replacement hunks. `position_in_body` is the char offset in
    the BASELINE body where the change begins (for inline rendering in F03c).

    Deterministic by construction — this is the hunk granularity, distinct from the
    matcher's token-set-Jaccard match decision. Significance defaults to substantive."""
    b_tokens = list(_WORD_SPANS(baseline_body))
    i_tokens = list(_WORD_SPANS(incoming_body))
    matcher = SequenceMatcher(
        None, [t[0] for t in b_tokens], [t[0] for t in i_tokens], autojunk=False
    )
    hunks: list[HunkDraft] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 < i2:
            b_start, b_end = b_tokens[i1][1], b_tokens[i2 - 1][2]
            original: str | None = baseline_body[b_start:b_end]
        else:
            b_start = b_tokens[i1][1] if i1 < len(b_tokens) else len(baseline_body)
            original = None
        if j1 < j2:
            i_start, i_end = i_tokens[j1][1], i_tokens[j2 - 1][2]
            proposed: str | None = incoming_body[i_start:i_end]
        else:
            proposed = None
        hunk_type = cast(
            HunkType, {"replace": "replacement", "delete": "deletion", "insert": "insertion"}[tag]
        )
        hunks.append(
            HunkDraft(
                hunk_type=hunk_type,
                position_in_body=b_start,
                original_text=original,
                proposed_text=proposed,
            )
        )
    return hunks


def _WORD_SPANS(s: str) -> list[tuple[str, int, int]]:
    """(word, start_offset, end_offset) for each whitespace-delimited token."""
    spans: list[tuple[str, int, int]] = []
    i, n = 0, len(s)
    while i < n:
        if s[i].isspace():
            i += 1
            continue
        start = i
        while i < n and not s[i].isspace():
            i += 1
        spans.append((s[start:i], start, i))
    return spans


# --------------------------------------------------------------------------- #
# Persistence helpers                                                           #
# --------------------------------------------------------------------------- #


async def _insert_change_with_hunks(
    conn: Any,
    session_id: str,
    *,
    node_id: str | None,
    proposed_parent_id: str | None,
    proposed_order_index: int | None,
    match_confidence: float | None,
    hunks: list[HunkDraft],
    received_node_id: str | None = None,
) -> None:
    change_id = await conn.fetchval(
        _INSERT_CHANGE,
        session_id,
        node_id,
        proposed_parent_id,
        proposed_order_index,
        match_confidence,
        len(hunks),
        received_node_id,
    )
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


# --------------------------------------------------------------------------- #
# Public entry point                                                            #
# --------------------------------------------------------------------------- #


async def import_revision(
    conn: Any, contract_id: str, path: str, request: RevisionImportRequest
) -> RevisionImportResponse:
    insertions, deletions = count_tracked_changes(path)
    if insertions or deletions:
        raise TrackedChangesNotSupported(
            "tracked-changes import not yet supported — coming next; "
            "accept all changes in Word and re-upload a clean copy"
        )

    if await conn.fetchval(_OPEN_SESSION_EXISTS, contract_id):
        raise SessionAlreadyOpen(
            "a revision review is already open for this contract — "
            "close it before importing another"
        )

    party = _SOURCE_TO_PARTY[request.source]
    baseline_snapshot_id = await conn.fetchval(_FIND_SHARED_POINTER, contract_id, party)
    if baseline_snapshot_id is None:
        raise BaselineMissing(f"send a version to {party} first — no baseline to diff against")
    baseline_snapshot_id = str(baseline_snapshot_id)

    baseline_snapshot = await get_snapshot(conn, baseline_snapshot_id)
    if baseline_snapshot is None or baseline_snapshot.tree is None:
        raise BaselineMissing(f"baseline snapshot {baseline_snapshot_id} is missing or empty")

    incoming_tree = build_tree(read_docx(path))
    incoming_nodes = incoming_to_clause_nodes(incoming_tree)
    baseline_nodes = baseline_to_clause_nodes(baseline_snapshot.tree)

    result: RevisionMatchResult = match_revision(baseline_nodes, incoming_nodes)

    incoming_by_index: dict[int, TreeNode] = {n.index: n for n in incoming_tree.nodes}
    baseline_by_id: dict[str, ClauseNode] = {n.id: n for n in baseline_nodes if n.id is not None}
    matched_incoming_to_baseline: dict[int, str] = {
        m.incoming_index: m.baseline_id for m in result.matches
    }

    db_source = _SOURCE_TO_DB_SOURCE[request.source]
    snapshot_count = int(await conn.fetchval(_SNAPSHOT_COUNT, contract_id))

    log.info(
        "revision_import.matched",
        contract_id=contract_id,
        source=db_source,
        baseline=len(baseline_nodes),
        incoming=len(incoming_nodes),
        matches=len(result.matches),
        new=len(result.new),
        deleted=len(result.deleted),
        abstains=len(result.abstains),
    )

    edited_matches = unchanged_matches = total_hunks = 0

    async with conn.transaction():
        as_received = await snapshot_tree(
            conn,
            contract_id,
            incoming_to_snapshot_nodes(incoming_tree),
            origin="as_received",
            label=request.source_filename,
            pointer=SnapshotPointerTarget(party=party, direction="received"),
        )

        session_id = await conn.fetchval(
            _INSERT_SESSION,
            contract_id,
            baseline_snapshot_id,
            db_source,
            request.source_filename,
            0,
        )
        session_id = str(session_id)

        for pair in result.matches:
            edited, hunks = _staged_match_hunks(pair, incoming_by_index, baseline_by_id)
            if not edited:
                unchanged_matches += 1
                continue
            edited_matches += 1
            total_hunks += len(hunks)
            await _insert_change_with_hunks(
                conn,
                session_id,
                node_id=pair.baseline_id,
                proposed_parent_id=None,
                proposed_order_index=None,
                match_confidence=pair.confidence,
                hunks=hunks,
            )

        for incoming_index in result.new:
            node = incoming_by_index[incoming_index]
            body = _treenode_text(node)
            parent_baseline = (
                matched_incoming_to_baseline.get(node.parent_index)
                if node.parent_index is not None
                else None
            )
            hunk = HunkDraft(
                hunk_type="insertion",
                position_in_body=0,
                original_text=None,
                proposed_text=body or None,
            )
            total_hunks += 1
            await _insert_change_with_hunks(
                conn,
                session_id,
                node_id=None,
                proposed_parent_id=parent_baseline,
                proposed_order_index=node.order_index,
                match_confidence=None,
                hunks=[hunk],
                # The as_received snapshot froze this incoming node with id = str(index)
                # (incoming_to_snapshot_nodes), so this links the change to the revised node.
                received_node_id=str(incoming_index),
            )

        for baseline_id in result.deleted:
            body = baseline_by_id[baseline_id].body if baseline_id in baseline_by_id else ""
            hunk = HunkDraft(
                hunk_type="deletion",
                position_in_body=0,
                original_text=body or None,
                proposed_text=None,
            )
            total_hunks += 1
            await _insert_change_with_hunks(
                conn,
                session_id,
                node_id=baseline_id,
                proposed_parent_id=None,
                proposed_order_index=None,
                match_confidence=None,
                hunks=[hunk],
            )

        for ab in result.abstains:
            node = incoming_by_index[ab.incoming_index]
            incoming_body = _treenode_text(node)
            if ab.best_baseline_id is not None and ab.best_baseline_id in baseline_by_id:
                baseline_body = baseline_by_id[ab.best_baseline_id].body
                hunks = extract_hunks(baseline_body, incoming_body) or [
                    HunkDraft(
                        hunk_type="replacement",
                        position_in_body=0,
                        original_text=baseline_body or None,
                        proposed_text=incoming_body or None,
                    )
                ]
            else:
                hunks = [
                    HunkDraft(
                        hunk_type="insertion",
                        position_in_body=0,
                        original_text=None,
                        proposed_text=incoming_body or None,
                    )
                ]
            total_hunks += len(hunks)
            await _insert_change_with_hunks(
                conn,
                session_id,
                node_id=None,
                proposed_parent_id=ab.best_baseline_id,
                proposed_order_index=None,
                match_confidence=ab.confidence,
                hunks=hunks,
                # Same as_received synthetic id linkage as NEW (closes the DEV_TODO
                # abstain→incoming-node item — exact, no body-match heuristic needed).
                received_node_id=str(ab.incoming_index),
            )

        changes_count = (
            edited_matches + len(result.new) + len(result.deleted) + len(result.abstains)
        )
        await conn.execute(
            "UPDATE counterparty_revision_sessions SET changes_count = $1 WHERE id = $2",
            changes_count,
            session_id,
        )

        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_REVISION_IMPORTED,
                entity_type="contract",
                entity_id=contract_id,
                actor=db_source,
                payload={
                    "session_id": session_id,
                    "source": db_source,
                    "baseline_snapshot_id": baseline_snapshot_id,
                    "as_received_snapshot_id": as_received.id,
                    "changes_count": changes_count,
                },
            ),
        )

    return RevisionImportResponse(
        session_id=session_id,
        contract_id=contract_id,
        source=db_source,
        parse_path="clean_diff",
        baseline_snapshot_id=baseline_snapshot_id,
        as_received_snapshot_id=as_received.id,
        received_pointer_party=party,
        version=snapshot_count + 1,
        status="reviewing",
        changes_count=changes_count,
        hunk_count=total_hunks,
        edited_matches=edited_matches,
        unchanged_matches=unchanged_matches,
        new=len(result.new),
        deleted=len(result.deleted),
        abstains=len(result.abstains),
    )


def _staged_match_hunks(
    pair: MatchedPair,
    incoming_by_index: dict[int, TreeNode],
    baseline_by_id: dict[str, ClauseNode],
) -> tuple[bool, list[HunkDraft]]:
    """(bodies-differ, hunks) for a matched pair. Empty hunks + False when the
    matched bodies are identical (an unchanged clause — no change row)."""
    incoming_body = _treenode_text(incoming_by_index[pair.incoming_index])
    baseline_node = baseline_by_id.get(pair.baseline_id)
    baseline_body = baseline_node.body if baseline_node is not None else ""
    if incoming_body == baseline_body:
        return False, []
    return True, extract_hunks(baseline_body, incoming_body)
