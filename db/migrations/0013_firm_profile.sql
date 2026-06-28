-- Firm profile v1 (F32, DD-90 — pull-forward of the operator-seeded "Fixed" half).
--
-- A single GLOBAL, firm-level free-text document (who the firm is, commercial
-- interests, standing positions / red-lines) injected into Donna's revision-
-- recommendation grounding as the firm's standing MANDATE. v1 is operator-authored
-- only (Fixed mode); the Donna-evolving half is deferred to v2.
--
-- Settings-style SINGLETON: the boolean PK + CHECK (id) pins the table to one row;
-- the seed INSERT creates it. Forward-only; idempotent (CREATE TABLE / INSERT both
-- IF-NOT-EXISTS / ON CONFLICT so a re-run is a no-op).
CREATE TABLE IF NOT EXISTS firm_profile (
    id         BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    content    TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO firm_profile (id) VALUES (true)
ON CONFLICT (id) DO NOTHING;
