"""Lifecycle badge + version/snapshot lineage models (F27, DD-75).

The operator-facing "where are we" surfaces over the snapshot/pointer machinery
(DD-48/DD-70/DD-71) — derived, never stored, no schema change:

  * `ContractBadge` — the persistent lifecycle badge (`Working copy` /
    `Sent to counterparty` / `Sent to legal` / `Sent to counterparty & legal` /
    `Your move` / `Signed`) + the passive "edited since sent" marker (DD-70 §5).
    Derived top-down, FIRST MATCH WINS (see `services/lineage.derive_status`).
  * `LineageView` — the v1→v2→…→vN chain: every snapshot numbered by its position
    on the timeline (ROW_NUMBER over `created_at`, DD-70), direction-tagged
    (`sent`/`received`), with the live working copy marked separately (never
    numbered). Received versions (Mode B `as_received` snapshots / `received`
    pointers, F03b) are real numbered entries; a greyed `received` placeholder slot
    is shown only for a side with no received version yet (the empty state).

Version numbers attach only to frozen snapshots; the working copy is never numbered
or locked (DD-70). The numbering rule (position over ALL snapshots) is receive-ready,
so interleaving receives never renumbers existing versions.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# The five operator-facing lifecycle labels (DD-75) + the engaged-receive fallthrough
# to "Working copy". Kept as a plain str on the badge (the DB is never the source of
# this value — it is derived), but enumerated here for the frontend contract.
BADGE_LABELS = (
    "Working copy",
    "Sent to counterparty",
    "Sent to legal",
    "Sent to counterparty & legal",
    "Your move",
    "Signed",
)


class ContractBadge(BaseModel):
    """The derived lifecycle badge for one contract (DD-70/DD-75).

    `version` is the v-number of the latest boundary snapshot (None for an unsent
    Working copy — the working copy is never numbered). `marker` is the passive
    "edited since sent" indicator (set only when the badge is a Sent state and the
    working copy has diverged since that send — DD-70 §5). `party` carries
    counterparty/legal/both for the Sent and Your-move states (frontend colour +
    wording). `based_on` is the engaged-inbound-revision provenance string
    ("vN received from counterparty"), populated only on the Phase-2 rule-4 path."""

    label: str
    version: int | None = None
    marker: bool = False
    party: str | None = None
    based_on: str | None = None


class PointerRow(BaseModel):
    """One of the four named DD-48 pointers as read back (party × direction →
    snapshot). `party` is the schema value (`counterparty`/`legal_team`/`internal`);
    `direction` is `shared`/`received`."""

    party: str
    direction: str
    snapshot_id: str


class LineageEntry(BaseModel):
    """One numbered snapshot on the lineage timeline (DD-70).

    `direction` = `sent` (a `shared` pointer points here, or origin=export/manual)
    or `received` (a `received` pointer points here, or origin=as_received).
    `party` = counterparty/legal/both/None (the side this boundary event involved).
    `pointer_labels` are the friendly names of the DD-48 pointers currently resting
    on this snapshot. `is_current_baseline` marks the snapshot the
    `last_shared_with_counterparty` pointer currently rests on — the F15 redline
    baseline (DD-60)."""

    version: int
    direction: str
    party: str | None
    created_at: datetime
    snapshot_id: str
    pointer_labels: list[str] = Field(default_factory=list)
    is_current_baseline: bool = False


class WorkingCopyEntry(BaseModel):
    """The live working copy — marked on the lineage view but NEVER numbered or
    locked (DD-70). `diverged_since_last_send` mirrors the badge marker: the working
    copy has edits since the most-recent `shared` snapshot."""

    label: str = "Working copy"
    diverged_since_last_send: bool = False


class ReservedSlot(BaseModel):
    """The empty-state `received` pointer slot, shown greyed (DD-75). Emitted only
    for a side (counterparty / legal) with no received version yet; once Mode B sets
    the matching `received` pointer (DD-48, F03b) that side renders a real numbered
    `LineageEntry` and its reserved slot is dropped. Always `populated=False`."""

    party: str
    direction: str = "received"
    label: str
    populated: bool = False


class LineageView(BaseModel):
    """The full lineage response: the current badge, the numbered send/receive
    timeline, the separately-marked working copy, and the greyed `received`
    placeholder slot(s) for any side without a received version yet."""

    contract_id: str
    badge: ContractBadge
    timeline: list[LineageEntry]
    working_copy: WorkingCopyEntry
    reserved: list[ReservedSlot]
