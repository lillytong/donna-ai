-- F10 follow-up: persist the assistant turn's answer treatment on donna_messages so a
-- thread reloaded from the DB rehydrates the same citation chips + kind styling a fresh
-- ask renders. Both nullable, set on assistant turns only (user messages leave them NULL).
-- `citations` is JSONB (node/issue ids), as DonnaAskResponse.citations. Pre-migration
-- rows keep NULL and render as plain grounded answers (expected). Idempotent.

ALTER TABLE donna_messages ADD COLUMN IF NOT EXISTS kind TEXT
    CHECK (kind IN ('answer','not_found','deflected'));
ALTER TABLE donna_messages ADD COLUMN IF NOT EXISTS citations JSONB;
