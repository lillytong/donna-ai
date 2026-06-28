-- Revision-session → as_received snapshot link (DD-94, amends DD-85/DD-87).
--
-- A revision session diffs an as_received (received counterparty/legal) snapshot
-- against its baseline. The session's only real snapshot FK was baseline_snapshot_id;
-- its link to the as_received version was a non-FK text/pointer indirection, so
-- deleting that version neither blocked nor cascaded — leaving a dangling 'reviewing'
-- session. This adds the direct, FK-correct link so version-delete's cascade can
-- discard the dependent open review (DD-94 §3).
--
-- Nullable: legacy sessions and the brief import window before the id is set carry
-- NULL. Forward-only; idempotent (ADD COLUMN IF NOT EXISTS).
ALTER TABLE counterparty_revision_sessions
    ADD COLUMN IF NOT EXISTS as_received_snapshot_id UUID REFERENCES contract_snapshots(id);
