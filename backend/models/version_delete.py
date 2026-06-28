"""Version-delete models (DD-85 / DD-87 §5) — the wipe-a-lineage-version contract.

A version (an immutable `contract_snapshots` row) can be hard-deleted. The call is
a two-step acknowledge (mirrors Mark-as-sent's drift gate, DD-72): `confirm=false`
returns a PREVIEW (`deleted=false`, the warnings the operator must read), and
`confirm=true` executes the wipe and returns what changed.

Delete behaves by position (DD-85):
  * latest  → rolls the working copy back to the predecessor version (`will_rollback`);
  * middle  → removes only that version, leaving a preserved gap, working copy untouched;
  * only    → snapshot removed; contract returns to the never-sent "Working copy" state.
"""

from __future__ import annotations

from pydantic import BaseModel


class SentRecord(BaseModel):
    """The send a deleted version carried (a `shared` pointer rested on it). Names the
    party + the send date for the DD-85 "erases the record of what was sent" warning."""

    party: str
    date: str


class ReviewDiscard(BaseModel):
    """The in-progress revision review a delete will discard (DD-94). Set when the target
    is the baseline OR the as_received snapshot of an OPEN ('reviewing') session — its
    change count + how many were already decided, for the non-blocking preview warning."""

    changes_count: int
    reviewed: int


class SnapshotDeleteResponse(BaseModel):
    """Preview (`confirm=false`) or execute (`confirm=true`) outcome of a version delete.

    On preview, `deleted`/`rolled_back` are false and `pointers_removed` is empty —
    `warnings` carries the non-blocking confirmations. On execute, `deleted=true`,
    `rolled_back` reflects whether the latest-delete rolled the working copy back, and
    `pointers_removed` names the lifecycle-tag sides (shared / received) that were
    DROPPED with the deleted version (DD-87 §4(b), amended): the tag is removed, never
    rolled back to the predecessor — no earlier version inherits it."""

    deleted: bool
    snapshot_id: str
    version_number: int
    is_latest: bool
    will_rollback: bool
    rolled_back: bool
    rollback_to_version: int | None = None
    sent_record: SentRecord | None = None
    review_discard: ReviewDiscard | None = None
    warnings: list[str]
    pointers_removed: list[str]
