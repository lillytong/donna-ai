-- F30 / DD-76 (amends DD-55, DD-73): the negotiation-pattern store.
--
-- Compact, operator-GLOBAL insights distilled on issue-close from the COMMITTED
-- issue ledger (never the raw brainstorm transcript — DD-76 reconciles DD-55's
-- "distil on brainstorm-close" with DD-73's ephemeral-brainstorm rule: the trigger
-- is issue-close and the input is the committed issue data, which is grounding-safe
-- by construction). NO contract_id: patterns transcend a single contract — they are
-- the operator's accumulated negotiating knowledge.
--
-- `subject_ref` is POLYMORPHIC and intentionally carries no FK (it points at two
-- different tables by subject_type, so a single FK can't express it):
--   * operator_style        -> NULL                (how THIS operator negotiates)
--   * counterparty_behavior -> clients.id          (how a given counterparty behaves)
--   * deal_type_norm        -> contract_types.id   (norms for an agreement type)
--   * legal_team_tendency   -> NULL                (operator-global legal-team behaviour)
-- It is derived deterministically from the closed issue's contract context, NEVER from
-- the model (the LLM only proposes subject_type + insight) — so a hallucinated id can
-- never reach this column.
--
-- Patterns are a RETRIEVAL INPUT for Donna, never authoritative and never exported
-- (§2.4): they inform her grounding alongside the cited ledger, visibly distinct from it.

CREATE TABLE negotiation_patterns (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type            TEXT NOT NULL CHECK (subject_type IN
                            ('operator_style','counterparty_behavior','deal_type_norm','legal_team_tendency')),
    subject_ref             UUID,                                  -- polymorphic; NO FK (see header)
    insight                 TEXT NOT NULL,                         -- 1-3 sentence compact principle, never a transcript
    evidence_count          INTEGER NOT NULL DEFAULT 1,            -- reinforcement events supporting this pattern
    confidence              REAL NOT NULL DEFAULT 0.5,             -- 0..1; bumped on reinforcement, drives retrieval ordering
    contradiction_flag      BOOLEAN NOT NULL DEFAULT false,        -- consolidation surfaces contradictions, never silent overwrite
    last_reinforced_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_reinforced_deal_id UUID REFERENCES deals(id),            -- the deal whose issue-close last reinforced it (TTL bookkeeping)
    is_deleted              BOOLEAN NOT NULL DEFAULT false,        -- soft-delete (consolidation prune)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Merge-first lookup + retrieval both scan by (subject_type, subject_ref); the small
-- live set per subject is fetched on every distillation and every issue-open.
CREATE INDEX negotiation_patterns_subject_idx
    ON negotiation_patterns (subject_type, subject_ref)
    WHERE is_deleted = false;
