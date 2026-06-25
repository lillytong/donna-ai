-- F10b / DD-73 (amended by DD-77): the per-issue brainstorm summary store.
--
-- Brainstorm is an EPHEMERAL overlay (DD-73, reaffirms DD-42): the raw back-and-forth
-- is never persisted. On close Donna distils ONE compact, operator-facing summary —
-- the question explored, the position/landing concluded, and the key fallbacks
-- considered (with why each was passed over) — and stores THAT instead.
--
-- A LINKED TABLE (not a column on issues): an issue can be brainstormed more than once
-- over its life, so each pass appends a row — the rows are the issue's brainstorm
-- history (DD-77, linked-table-vs-field). Distinct from negotiation_patterns (DD-76):
-- that store is abstract, cross-deal, Donna's silent learning substrate; this is
-- concrete, this-issue, operator-facing continuity. Both fire on close; neither
-- replaces the other.
--
-- The summary is operator-facing continuity, NOT a grounding source: Donna still
-- grounds advice only on the committed ledger + clause text, never a transcript or a
-- summary (DD-42/DD-73 §5). Cascaded with the issue on contract delete (DD-63).

CREATE TABLE brainstorm_summaries (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id   UUID NOT NULL REFERENCES issues(id),
    question   TEXT,                                  -- what the brainstorm set out to resolve
    conclusion TEXT,                                  -- the position / landing concluded
    fallbacks  TEXT,                                  -- key fallbacks considered + why passed over
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX brainstorm_summaries_issue_idx ON brainstorm_summaries (issue_id);
