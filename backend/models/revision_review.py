"""Models for Mode B revision review + decision (F03c — the READ + DECISION side
of the counterparty/legal revision loop; F03b stages the data).

Two phases per §11 step 6 / DD-78:
  - Phase 1 (structural foundation): the matcher's ABSTAIN bucket (the
    match-confirm queue, ranked by ascending `match_confidence`) plus any
    tree-shape anomalies (6a — no staged source yet, so always empty; kept in the
    contract for the frontend).
  - Phase 2 (content review): the settled changes (edited-match / new / deleted)
    in document order, each with its hunks and current decision state so the UI
    can resume.

`change_kind` is DERIVED from the staged columns (F03b never wrote a kind column):
  - abstain : node_id IS NULL AND proposed_order_index IS NULL  (match_confidence set)
  - new     : node_id IS NULL AND proposed_order_index IS NOT NULL
  - edited  : node_id IS NOT NULL AND match_confidence IS NOT NULL
  - deleted : node_id IS NOT NULL AND match_confidence IS NULL

The stored hunk `verdict` column is CHECK-constrained to
`pending|accepted|rejected|modified`; the operator-facing four-action vocabulary
(DD-27: accept|counter|edit|keep) and the whole-node vocabulary (accept|reject|edit)
map onto it in the service.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.models.contract_tree import Role
from backend.models.revision_import import (
    ChangeStatus,
    HunkType,
    Significance,
    StoredRevisionSession,
)

ChangeKind = Literal["edited", "new", "deleted", "abstain"]
StoredHunkVerdict = Literal["pending", "accepted", "rejected", "modified"]

# Node-level overlay kinds for the two-pane document view (F03c rework). A clause may
# carry more than one. "shifted" (a reordered/moved clause) is in the legend but is NOT
# currently derivable from the staged data — see `derive_document_change_kinds` and the
# DEV_TODO follow-up; it is never emitted today.
DocumentChangeKind = Literal["added", "deleted", "modified", "shifted"]

# Operator-facing action vocabularies (mapped onto StoredHunkVerdict in the service).
HunkDecisionAction = Literal["accept", "counter", "edit", "keep"]
NodeDecisionAction = Literal["accept", "reject", "edit"]
MatchConfirmAction = Literal["confirm", "new", "rematch"]


class ReviewHunk(BaseModel):
    """One decision unit (a `counterparty_revision_hunks` row) projected for review."""

    id: str
    change_id: str
    hunk_type: HunkType
    significance: Significance
    position_in_body: int | None
    original_text: str | None
    proposed_text: str | None
    donna_verdict: str | None
    donna_counter_text: str | None
    donna_rationale: str | None
    verdict: StoredHunkVerdict
    final_text: str | None
    # Cross-document consistency (DD-89, F34): the synthetic id of the cluster of identical
    # counterparty edits this hunk belongs to (stable hash of the shared `revision_cluster`
    # key), set ONLY when the same edit recurs in >1 clause; `cluster_size` is that member
    # count. Derived at read time (no schema change) so the frontend can collapse the members
    # into ONE grouped review stop ("this change appears in N clauses").
    cluster_id: str | None = None
    cluster_size: int = 1


class ChangeContextSide(BaseModel):
    """Structural context for ONE side (baseline or incoming) of a review change —
    where the clause sits and what surrounds it, so the operator judges every change
    in context, not as a floating tracked-change fragment (F03c UX).

    - `number` / `heading`: clause identity ("4.2" / "Payment Terms"), the card header.
    - `breadcrumb`: the ancestor-heading chain (which section the clause lives under).
    - `body`: the FULL clause text the hunk offsets (`position_in_body`) index into, so
      an edited card can render the diff IN PLACE within the surrounding sentences.
    - `children_preview` / `prev_label` / `next_label`: what sits under / beside the
      clause (the abstain disambiguator; the new/deleted "where it lands" neighbours).

    `found=False` ⇒ no resolvable node for this side (an abstain with no baseline
    candidate, or the side that doesn't apply to this change kind); the rest stay
    empty/None so a card degrades gracefully rather than erroring."""

    side: Literal["their", "baseline"]
    found: bool
    number: str | None = None
    heading: str | None = None
    breadcrumb: list[str] = Field(default_factory=list)
    children_preview: list[str] = Field(default_factory=list)
    body: str | None = None
    prev_label: str | None = None
    next_label: str | None = None


class ChangeContext(BaseModel):
    """Both sides of a change's structural context. Which side is populated depends on
    the change kind: edited / deleted ⇒ `baseline` (the live node located by `node_id`);
    new ⇒ `their` (the incoming node, body-matched in the as_received tree); abstain ⇒
    both (the candidate baseline + the incoming clause)."""

    their: ChangeContextSide
    baseline: ChangeContextSide


class ReviewChange(BaseModel):
    """One navigation unit (a `counterparty_revision_changes` row) + its hunks and
    derived `change_kind`. Used for both the Phase-1 abstain queue and the Phase-2
    content stream."""

    id: str
    session_id: str
    change_kind: ChangeKind
    node_id: str | None
    proposed_parent_id: str | None
    proposed_order_index: int | None
    match_confidence: float | None
    # The incoming (revised / as_received) node id this change came from — the synthetic
    # as_received snapshot id. Persisted on new/abstain rows (F03b migration 0011); NULL
    # on edited/deleted (keyed to baseline node_id) and on rows staged before 0011.
    received_node_id: str | None = None
    hunk_count: int
    hunks_decided: int
    status: ChangeStatus
    hunks: list[ReviewHunk]
    # Read-only structural enrichment, populated for EVERY change (both phases).
    context: ChangeContext | None = None


class TreeAnomaly(BaseModel):
    """A residual 6a tree-shape anomaly (DD-78). F03b stages none yet, so the list
    is always empty — kept so the frontend builds to a stable shape."""

    node_id: str
    reason: str


class ReviewPhase1(BaseModel):
    """Structural-foundation phase: the abstain match-confirm queue (ranked by
    ascending `match_confidence` — most-uncertain first) + tree-shape anomalies."""

    abstains: list[ReviewChange]
    tree_anomalies: list[TreeAnomaly]


class ReviewPayload(BaseModel):
    """Full review payload for one session: the session record, Phase-1 (abstains +
    anomalies) and Phase-2 (settled changes in document order)."""

    session: StoredRevisionSession
    phase1: ReviewPhase1
    phase2: list[ReviewChange]


class ConfirmMatchRequest(BaseModel):
    """6b match-confirm (abstain resolution). `baseline_node_id` is required for
    `rematch` (the operator-chosen baseline) and ignored otherwise."""

    action: MatchConfirmAction
    baseline_node_id: str | None = None


class HunkDecideRequest(BaseModel):
    """Phase-2 edited-match hunk verdict (DD-27 four actions). `final_text` is
    required for `edit`; ignored for `accept`/`keep`; for `counter` the staged
    `donna_counter_text` is used (unavailable → 422 — generation is deferred)."""

    verdict: HunkDecisionAction
    final_text: str | None = None


class NodeDecideRequest(BaseModel):
    """Whole-node decision for a new/deleted change (single decision over its one
    hunk). `final_text` is required for `edit`."""

    verdict: NodeDecisionAction
    final_text: str | None = None


class ClusterDecideRequest(BaseModel):
    """DD-89 grouped-stop decision: ONE verdict applied to every member hunk of a cluster
    (decide-once → fans to all). Same four-action vocabulary as `HunkDecideRequest`; `edit`
    requires `final_text` (used as the applied text for every member); `counter` uses each
    member's OWN staged `donna_counter_text` (members were judged once, so the counter is
    consistent). Per-clause divergence is a peel-off via the per-hunk decide, not this route."""

    verdict: HunkDecisionAction
    final_text: str | None = None


class ApplyResult(BaseModel):
    """Receipt for `POST .../apply`: what landed where (F08 paths) and which
    rejections seeded issues (§11 step 9)."""

    session_id: str
    status: str
    edits_applied: int
    nodes_inserted: int
    nodes_deleted: int
    issues_created: int
    issue_ids: list[str]


# --------------------------------------------------------------------------- #
# Two-pane document view (F03c rework) — the read-only data spine for rendering #
# the full revised document with changed clauses highlighted, plus the         #
# match-confirm before/after overlay. Hunk-level redline text stays out of this #
# payload (it is fetched on click via the existing review payload).            #
# --------------------------------------------------------------------------- #


class DocumentNode(BaseModel):
    """One node in a document tree, flattened to reading order. `clause_number` is the
    derived 1-based dotted sibling path ("4.2"); `depth` is the nesting level (roots = 0).
    `role` is the DD-54 structural classification: snapshots do not store role, so on the
    BASELINE side it is recovered by joining the (real) node id to live `nodes.role`; on
    the REVISED side (as_received synthetic ids that don't join) and for genuinely new,
    unclassified clauses it falls back to the default `clause` (frontend uses a generic
    label there)."""

    node_id: str
    clause_number: str | None
    role: Role
    depth: int
    text: str | None
    # True when the node is a clause HEADING (heading set, empty/null body) — import's
    # `typeLabel === "Heading"` signal (`heading and not body`), surfaced so the review
    # pane bolds headings exactly like the first-import Source panel. `text` carries the
    # heading string for such a node, so it still renders.
    is_heading: bool = False


class DocumentChange(BaseModel):
    """A change overlay entry keyed to the staged change row. `node_id` is the change's
    stored node id — the baseline node for edited/deleted, NULL for added; the stable
    join key against the `baseline` tree. `kinds` is the node-level classification
    (`derive_document_change_kinds`). `decided` = the change is fully resolved."""

    change_id: str
    node_id: str | None
    proposed_parent_id: str | None
    # The revised-side (as_received synthetic) node id for an added clause, so the
    # frontend can render it from the role-resolved revised tree and target the
    # role-override endpoint. Set on added (NEW) changes; NULL on edited/deleted (and on
    # rows staged before F03b migration 0011). See migration 0011.
    received_node_id: str | None = None
    kinds: list[DocumentChangeKind]
    decided: bool
    hunk_count: int
    hunks_decided: int


class AbstainMatch(BaseModel):
    """A match-confirm overlay entry for one abstain change: both sides of the proposed
    (low-confidence) match so the UI can highlight them together. `baseline_node_id` is
    the baseline candidate (`proposed_parent_id`); `proposed_received_node_id` is the
    incoming node recovered by body-match (lossy — NULL or first-of-duplicates where the
    staged data can't disambiguate; see the DEV_TODO abstain-linkage follow-up)."""

    change_id: str
    baseline_node_id: str | None
    proposed_received_node_id: str | None
    confidence: float | None


class ProjectedNode(BaseModel):
    """One node in the PROJECTED reading order — the single linear sequence the frontend
    renders without grafting by `proposed_parent_id`. The projected document is the
    baseline with every NON-REJECTED change applied:
      - unchanged baseline clause  -> change_id/change_kind None, numbered.
      - edited baseline clause      -> change_kind "modified", numbered, text is the
        baseline body (the redline is fetched on click via the review payload).
      - added clause                -> change_kind "added", inserted at its REAL revised
        position (from the matcher index, NOT the staged NULL parent). Numbered when
        pending/accepted; EMITTED but UNNUMBERED (numbered=False, clause_number None) when
        rejected — a struck trace, the symmetric mirror of an accepted/pending deletion
        shown-in-place; it consumes no sibling position so survivors renumber as if absent.
      - deleted baseline clause     -> change_kind "deleted"; KEPT + numbered when the
        deletion was rejected/modified (clause survives), shown-in-place but UNNUMBERED
        (clause_number None) when accepted/pending (clause removed from the projected tree).
    `clause_number` is the role-aware DD-02/DD-43
    number of the PROJECTED tree, so it shifts as verdicts change (insert pushes the next
    section down; rejecting the insert renumbers it back). `node_id` is the baseline node
    id for baseline-derived nodes and the revised synthetic id for added nodes (the
    role-override target). `numbered` is the internal flag the numbering pass reads; it is
    surfaced so the frontend can distinguish a kept-but-renumbered clause from a removed one."""

    node_id: str
    clause_number: str | None
    role: Role
    depth: int
    text: str | None
    change_id: str | None = None
    change_kind: DocumentChangeKind | None = None
    numbered: bool = True
    # Heading flag mirrored from the source `DocumentNode` (see `DocumentNode.is_heading`),
    # so the projected pane bolds headings like the first-import Source panel.
    is_heading: bool = False


class RevisionDocumentView(BaseModel):
    """The two-pane document payload: the baseline + revised document trees as ordered
    nodes, the change overlay keyed to the revised side, the abstain match-confirm pairs,
    and the `projected` linear reading order (baseline + non-rejected changes, role-aware
    numbered) the frontend renders directly. Light enough to render a 460+ node document —
    no hunk redline text here."""

    baseline: list[DocumentNode]
    revised: list[DocumentNode]
    changes: list[DocumentChange]
    abstain_matches: list[AbstainMatch]
    projected: list[ProjectedNode]


class NodeRoleOverrideRequest(BaseModel):
    """Operator override of a REVISED-side node's classification (Mode B Phase 1).

    `role` is validated against the DD-54 `Role` taxonomy by Pydantic (a bad value
    is a 422). `None` CLEARS the override, reverting the node to the render-time
    auto-classification (matched-node inheritance / new-node default)."""

    role: Role | None = None


class NodeRoleOverrideResult(BaseModel):
    """The resolved override after an upsert/clear: `role` is the persisted override
    (None = cleared, node falls back to auto-classification)."""

    node_id: str
    role: Role | None
