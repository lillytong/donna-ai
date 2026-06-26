-- Mode B classification editing, Phase 1 (role/type override only).
--
-- The REVISED (as_received) side of a revision review has its `role` derived at
-- RENDER time (revision_review.get_document_view): matched revised nodes inherit
-- their baseline node's operator-confirmed role, genuinely-new nodes default to
-- 'clause'. The as_received snapshot is an immutable JSONB tree of SnapshotNodes
-- with SYNTHETIC ids (models/snapshots.py) and carries NO role, so an operator's
-- re-typing of a revised node had nowhere to persist.
--
-- A session-scoped OVERRIDE STORE (this table) keeps the snapshot immutable and the
-- edit reversible: an override row WINS over the render-time inheritance for its
-- node_id; clearing it (DELETE) reverts to the auto-classification. node_id is the
-- synthetic as_received id (TEXT), not a live `nodes.id`, so there is no FK to nodes.
--
-- Forward-only / additive / idempotent. Designed to grow for the later indent/
-- reparent phase (a future migration can add parent/order override columns); kept
-- role-only here on purpose.
CREATE TABLE IF NOT EXISTS counterparty_revision_node_overrides (
    session_id UUID NOT NULL REFERENCES counterparty_revision_sessions(id),
    node_id    TEXT NOT NULL,         -- synthetic as_received node id (revised side)
    role       TEXT,                  -- operator role override (nullable; null = cleared)
    PRIMARY KEY (session_id, node_id)
);
