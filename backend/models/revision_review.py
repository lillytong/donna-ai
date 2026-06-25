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

from backend.models.revision_import import (
    ChangeStatus,
    HunkType,
    Significance,
    StoredRevisionSession,
)

ChangeKind = Literal["edited", "new", "deleted", "abstain"]
StoredHunkVerdict = Literal["pending", "accepted", "rejected", "modified"]

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
    verdict: StoredHunkVerdict
    final_text: str | None


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
