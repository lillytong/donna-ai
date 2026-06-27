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


class SnapshotDeleteResponse(BaseModel):
    """Preview (`confirm=false`) or execute (`confirm=true`) outcome of a version delete.

    On preview, `deleted`/`rolled_back` are false and `pointers_rolled_back` is empty —
    `warnings` carries the non-blocking confirmations. On execute, `deleted=true`,
    `rolled_back` reflects whether the latest-delete rolled the working copy back, and
    `pointers_rolled_back` names the redline-baseline / shared pointer sides that moved
    to (or were cleared with) the deleted version."""

    deleted: bool
    snapshot_id: str
    version_number: int
    is_latest: bool
    will_rollback: bool
    rolled_back: bool
    rollback_to_version: int | None = None
    sent_record: SentRecord | None = None
    warnings: list[str]
    pointers_rolled_back: list[str]
