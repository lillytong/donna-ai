-- F03c / DD-78: Donna's per-change revision recommendation already produces a one-line
-- rationale for her verdict (the model's `reasoning` line), but it was computed then
-- discarded — only the verdict + counter-language were persisted. Surface it: a NULLABLE
-- column to carry Donna's concise reason for accept/counter/keep alongside
-- `donna_verdict` / `donna_counter_text`, so the two-pane review can display it.
-- It is set when recommendations are generated and stays NULL for hunks Donna has not
-- (yet) analyzed. Additive, forward-only, idempotent.
ALTER TABLE counterparty_revision_hunks
    ADD COLUMN IF NOT EXISTS donna_rationale TEXT;
