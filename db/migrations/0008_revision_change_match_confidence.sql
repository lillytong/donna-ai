-- F03b / DD-64: the Mode B Path-B matcher emits an ABSTAIN band (low-confidence /
-- thin-margin pairs the operator must confirm in structural triage). The staging
-- `counterparty_revision_changes` table had no field to carry the matcher's
-- composite confidence, so a NULLABLE column is added. It is set on EDITED matches
-- and on ABSTAIN rows (where proposed_parent_id carries the provisional candidate),
-- and stays NULL for genuinely-new and deleted rows. Additive, forward-only.
ALTER TABLE counterparty_revision_changes
    ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION;
