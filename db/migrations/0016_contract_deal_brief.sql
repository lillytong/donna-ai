-- Deal brief (F37, DD-95 — a per-deal global-context tier Donna distills + the operator edits).
--
-- A per-CONTRACT free-text brief Donna distills from ONE whole-contract read at import
-- (parties + roles, each party's business/interests, the partnership's economic spine, key
-- terms + interrelations, purpose). It fills the {deal_context} grounding slot for Donna's
-- recommendations / chat / brainstorm (Part B wires that) — the lawyer's "hold the whole deal
-- in mind" view vs clause-by-clause tunnel vision.
--
-- Sourcing mirrors F32 (firm profile): Donna-seeded-at-import, operator-reviewable/editable,
-- EDITS WIN. `operator_edited` records whether the operator has overwritten Donna's draft; an
-- automatic re-import re-distil respects it (never clobbers an edited brief), while a manual
-- Refresh forces a fresh distil and resets the flag. `model` / `generated_at` record the last
-- Donna distillation (both NULL until the first distil, or for an operator-only edit).
--
-- One brief per contract: contract_id is the PK. Forward-only; idempotent (CREATE TABLE
-- IF NOT EXISTS so a re-run is a no-op).
CREATE TABLE IF NOT EXISTS contract_deal_brief (
    contract_id     UUID PRIMARY KEY REFERENCES contracts(id),
    content         TEXT NOT NULL DEFAULT '',
    operator_edited BOOLEAN NOT NULL DEFAULT false,  -- edits win: true => auto re-distil skips
    model           TEXT,                            -- model that authored the Donna-seeded brief
    generated_at    TIMESTAMPTZ,                     -- when Donna last distilled (NULL until first)
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
