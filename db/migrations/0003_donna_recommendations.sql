-- DD-68 (F11): Donna's auto-generated issue recommendation is a DRAFT held apart
-- from the exported issue fields. issues.recommended_position / donna_counter_language
-- are read by the F31 issue-list export, so an unconfirmed auto-draft must never land
-- there (a draft would leak into an export — §2.4 / DD-50). The draft lives here; on
-- operator confirm ([Use Donna's language]) the service copies draft -> issues.* so the
-- export only ever carries operator-confirmed language. One draft per issue (UNIQUE).

CREATE TABLE donna_recommendations (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id                   UUID NOT NULL UNIQUE REFERENCES issues(id),
    rationale                  TEXT NOT NULL,
    draft_recommended_position TEXT,
    draft_counter_language     TEXT,
    citations                  JSONB,            -- grounding citations (node/issue ids), as F10
    model                      TEXT NOT NULL,    -- tier/model that generated it (DD-35)
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed                  BOOLEAN NOT NULL DEFAULT false
);
