-- Operator organization override (F25, DD-44 — makes the org identity editable).
--
-- Until now the operator's org name was a pure config value (DONNA_OPERATOR_ORG_NAME),
-- read-only in Settings. This adds a DB-backed OVERRIDE so the name can be edited in the
-- app: when the override is non-empty it wins over the env value; when blank the config
-- value (then the neutral default) still resolves. The explicit DONNA_REDLINE_AUTHOR env
-- override remains a separate, config-only author and continues to win when set.
--
-- Settings-style SINGLETON: the boolean PK + CHECK (id) pins the table to one row; the
-- seed INSERT creates it. Forward-only; idempotent (CREATE TABLE / INSERT both guarded).
CREATE TABLE IF NOT EXISTS operator_organization (
    id                BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    organization_name TEXT NOT NULL DEFAULT '',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO operator_organization (id) VALUES (true)
ON CONFLICT (id) DO NOTHING;
