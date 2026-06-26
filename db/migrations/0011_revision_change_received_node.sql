-- Mode B Path-B import: persist the incoming (revised / as_received) node linkage on
-- the staged change row for NEW and ABSTAIN changes.
--
-- The as_received snapshot froze each incoming node with a synthetic id = its flat
-- document-order index (`incoming_to_snapshot_nodes` -> `str(node.index)`). NEW and
-- ABSTAIN change rows previously stored NO reference back to that revised node (only
-- the baseline candidate via proposed_parent_id), so F03c had to RECOVER the incoming
-- node by body-matching the as_received tree — ambiguous exactly when ≥2 nodes share
-- text (duplicate headings, the low-info case the matcher abstains on).
--
-- This column carries `str(incoming_index)` (== the as_received synthetic node id) on
-- NEW + ABSTAIN rows, giving the frontend an exact handle to (a) render the added node
-- from the role-resolved revised tree and (b) target the revised-node role-override
-- endpoint. EDITED / DELETED rows leave it NULL (they key to the baseline node_id).
--
-- Forward-only / additive / idempotent. No backfill: rows staged before this migration
-- keep received_node_id = NULL (re-import to populate).
ALTER TABLE counterparty_revision_changes
    ADD COLUMN IF NOT EXISTS received_node_id TEXT;
