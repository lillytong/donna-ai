-- donna.ai schema — source of truth for the data model (SPEC §6).
-- The DB is canonical; Word documents are an export artifact (principle §2.3).
-- Loaded automatically by docker-compose on first DB init.
--
-- Conventions:
--   * UUID primary keys (gen_random_uuid()).
--   * timestamptz everywhere; created_at defaults to now().
--   * Enumerations are TEXT + CHECK (cheaper to evolve than native ENUM types).
--   * No users/accounts table in v1 — single-operator local, `actor` is a value
--     not an FK (DD-53). Identity + auth arrive together at the v1.1 portal.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector (embeddings; Phase 2)

-- ============================================================================
-- Phase 0 — clients, deals, contracts, the node tree, import-spine entities
-- ============================================================================

CREATE TABLE clients (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    relationship_type TEXT NOT NULL DEFAULT 'counterparty'
                      CHECK (relationship_type IN ('counterparty','partner','licensee','other')),
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','archived')),
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE deals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID NOT NULL REFERENCES clients(id),
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','signed','closed')),
    -- Which party the operator is in this deal; governs what Donna's
    -- auto-detection flags as unfavorable (DD-50). Set once per deal.
    position    TEXT CHECK (position IN
                ('customer','vendor','buyer','seller',
                 'licensor','licensee','receiving_party','disclosing_party')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contract_types (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,             -- e.g. "Licence Agreement", "Offtake Agreement"
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- style_templates.config / contracts.style_config JSONB schema is locked in
-- DD-37: { font, numbering_scheme, body_font_size_pt, indent_per_level_pt,
-- page_breaks_before_articles, levels: { "<depth>": { bold, caps, underline,
-- font_size_pt } } }. `caps` is a render-time uppercase transform, never stored
-- uppercase (content integrity §2.1, DD-37).
CREATE TABLE style_templates (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    config     JSONB NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contracts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id             UUID NOT NULL REFERENCES clients(id),
    deal_id               UUID NOT NULL REFERENCES deals(id),
    contract_type_id      UUID NOT NULL REFERENCES contract_types(id),
    name                  TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'drafting'
                          CHECK (status IN ('drafting','under negotiation','signed')),
    current_version_label TEXT,
    style_template_id     UUID REFERENCES style_templates(id),  -- nullable: inherits template
    style_config          JSONB NOT NULL DEFAULT '{}'::jsonb,    -- per-contract overrides on top
    origin                TEXT CHECK (origin IN ('us','our_legal','counterparty')),  -- who drafted the baseline (first upload); sets Donna's starting redline stance, distinct from per-revision source (DD-47)
    last_export_at        TIMESTAMPTZ,  -- DD-72: stamped on every clean-copy export; Mark-as-sent compares node.updated_at against it for the "edited since last export" drift warning
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The universal addressable unit. Self-referential adjacency list, arbitrary
-- depth. NO stored clause number — derived from tree position (DD-02).
CREATE TABLE nodes (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id    UUID NOT NULL REFERENCES contracts(id),
    parent_id      UUID REFERENCES nodes(id),            -- null = root
    order_index    INTEGER NOT NULL,                     -- gap-based (OQ-07)
    content_type   TEXT NOT NULL DEFAULT 'prose'
                   CHECK (content_type IN ('prose','table','attachment')),
    -- Structural role (DD-54, DD-56). Only `clause` is numbered; front-matter
    -- (title/date/parties/recital/agreement_statement), back-matter
    -- (appendix_title/appendix/signature_block), and the cross-cutting
    -- `drafting_note` are excluded from the clause tree + numbering.
    -- `appendix_title` is a schedule/annex/exhibit divider (back-matter level 0,
    -- DD-56). `drafting_note` is also export-excluded from any counterparty
    -- document (§12). TOC is dropped on import, never stored. NOTE: an existing
    -- local DB needs these columns added (ALTER TABLE) or a recreate — schema.sql
    -- is the source of truth.
    role           TEXT NOT NULL DEFAULT 'clause'
                   CHECK (role IN ('title','date','parties','recital',
                          'agreement_statement','clause','appendix',
                          'appendix_title','signature_block','drafting_note')),
    has_placeholder BOOLEAN NOT NULL DEFAULT false,      -- fill-in blank (F28 alert)
    heading        TEXT,
    body           TEXT,                                 -- prose nodes: semantic markup
    table_data     JSONB,                                -- table nodes: [[cell,...],...] rows; never flattened
    plain_text     TEXT,                                 -- derived projection; never source of truth
    file_reference TEXT,                                 -- attachment nodes only
    is_deleted     BOOLEAN NOT NULL DEFAULT false,
    deleted_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contract_id, parent_id, order_index)
);
CREATE INDEX nodes_contract_idx ON nodes (contract_id);
CREATE INDEX nodes_parent_idx   ON nodes (parent_id);

-- Structured footnote bodies, anchored to a node ([^N] markers, DD).
CREATE TABLE footnotes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id      UUID NOT NULL REFERENCES nodes(id),
    anchor_index INTEGER NOT NULL,        -- matches the [^N] marker in the body
    body         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only history of every node-body change. Structural moves are NOT
-- versioned here — they are reconstructed from snapshots (OQ-08).
CREATE TABLE node_versions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id     UUID NOT NULL REFERENCES nodes(id),
    snapshot_id UUID,                     -- nullable until next snapshot is cut; FK added below
    body_before TEXT,
    body_after  TEXT,
    actor       TEXT NOT NULL CHECK (actor IN ('user','ai','principal')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deal-scoped defined terms (shared across all contracts in the deal).
CREATE TABLE defined_terms (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id        UUID NOT NULL REFERENCES deals(id),
    term           TEXT NOT NULL,
    definition     TEXT,
    source_node_id UUID REFERENCES nodes(id),
    UNIQUE (deal_id, term)
);

-- Explicit links between nodes; may cross contracts within a deal. The displayed
-- number renders from the target's CURRENT position, so refs never break on
-- renumber and ripple-flag on change (DD-11).
CREATE TABLE cross_references (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_node_id     UUID NOT NULL REFERENCES nodes(id),
    source_contract_id UUID NOT NULL REFERENCES contracts(id),
    target_node_id     UUID REFERENCES nodes(id),       -- nullable: contract-name-only refs
    target_contract_id UUID REFERENCES contracts(id)
);

-- Shared commercial values, defined once at the deal (DD-12).
CREATE TABLE deal_parameters (
    id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id UUID NOT NULL REFERENCES deals(id),
    key     TEXT NOT NULL,
    value   TEXT,
    unit    TEXT,
    notes   TEXT,
    UNIQUE (deal_id, key)
);

CREATE TABLE parameter_references (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id           UUID NOT NULL REFERENCES nodes(id),
    deal_parameter_id UUID NOT NULL REFERENCES deal_parameters(id),
    mention_text      TEXT NOT NULL,     -- the literal text in the clause
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Snapshots & version pointers (Phase 3 export / Mode B baselines)
-- ============================================================================

-- Immutable point-in-time capture. OQ-08 resolved: full-tree-dump — `tree`
-- stores the complete node set (topology + bodies) so structural diffs (DD-03)
-- can reconstruct insert/delete/move, which node_versions does not record.
-- tree JSONB shape: [ { id, parent_id, order_index, content_type, heading,
-- body, is_deleted } ] for every node in the contract at capture time.
CREATE TABLE contract_snapshots (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id UUID NOT NULL REFERENCES contracts(id),
    label       TEXT,
    tree        JSONB NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('export','as_received','manual')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE node_versions
    ADD CONSTRAINT node_versions_snapshot_fk
    FOREIGN KEY (snapshot_id) REFERENCES contract_snapshots(id);

-- Four named pointers per contract (DD-48). `shared` pointers double as the
-- per-source diff baselines (DD-47); `received` are immutable as-sent records.
CREATE TABLE snapshot_pointers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id UUID NOT NULL REFERENCES contracts(id),
    party       TEXT NOT NULL CHECK (party IN ('counterparty','legal_team','internal')),
    direction   TEXT NOT NULL CHECK (direction IN ('shared','received')),
    snapshot_id UUID NOT NULL REFERENCES contract_snapshots(id),
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contract_id, party, direction)
);

-- ============================================================================
-- Phase 2 — counterparty revision review (staging) & issues
-- Table names keep the `counterparty_` prefix; the rename to revision_* with
-- the source generalization is propagated at Phase 2 build (DD-47). The
-- `source` column is added now per the SPEC §6 entity.
-- ============================================================================

CREATE TABLE counterparty_revision_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id             UUID NOT NULL REFERENCES contracts(id),
    baseline_snapshot_id    UUID NOT NULL REFERENCES contract_snapshots(id),
    source                  TEXT NOT NULL DEFAULT 'counterparty'
                            CHECK (source IN ('counterparty','legal_team','internal')),  -- DD-47
    source_filename         TEXT,
    parse_path              TEXT NOT NULL CHECK (parse_path IN ('tracked_changes','clean_diff')),
    status                  TEXT NOT NULL DEFAULT 'reviewing'
                            CHECK (status IN ('reviewing','completed')),
    changes_count           INTEGER NOT NULL DEFAULT 0,
    changes_reviewed_count  INTEGER NOT NULL DEFAULT 0,
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per node with at least one edit (navigation unit). node_id nullable =
-- a proposed new node not in the baseline.
CREATE TABLE counterparty_revision_changes (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id           UUID NOT NULL REFERENCES counterparty_revision_sessions(id),
    node_id              UUID REFERENCES nodes(id),
    proposed_parent_id   UUID REFERENCES nodes(id),    -- for new nodes: insert point;
                                                       -- for ABSTAIN rows: the provisional
                                                       -- best-baseline candidate (F03b/DD-64)
    proposed_order_index INTEGER,                      -- for new nodes: sibling position
    -- F03b/DD-64: the matcher's composite confidence — set on EDITED matches and on
    -- ABSTAIN rows (the low-confidence pair the operator must confirm), NULL otherwise.
    match_confidence     DOUBLE PRECISION,
    hunk_count           INTEGER NOT NULL DEFAULT 0,
    hunks_decided        INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','partial','complete'))
);

-- One row per individual text edit within a node (decision unit).
CREATE TABLE counterparty_revision_hunks (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_id        UUID NOT NULL REFERENCES counterparty_revision_changes(id),
    hunk_type        TEXT NOT NULL CHECK (hunk_type IN ('insertion','deletion','replacement')),
    significance     TEXT NOT NULL CHECK (significance IN ('trivial','substantive')),
    position_in_body INTEGER,                          -- char offset for inline rendering
    original_text    TEXT,
    proposed_text    TEXT,
    donna_verdict    TEXT CHECK (donna_verdict IN ('accept','counter','keep')),
    donna_counter_text TEXT,                           -- null for trivial hunks
    verdict          TEXT NOT NULL DEFAULT 'pending'
                     CHECK (verdict IN ('pending','accepted','rejected','modified')),
    final_text       TEXT,
    decided_at       TIMESTAMPTZ
);

-- Mode B classification editing, Phase 1 (DD: session-scoped override store). The
-- REVISED side's role is derived at render time (matched nodes inherit the baseline
-- role; new nodes default 'clause'); the as_received snapshot carries no role and has
-- synthetic ids. An override row WINS over that inheritance for its node_id and is
-- reversible (clearing = DELETE). node_id is the synthetic as_received id (TEXT), not
-- a live nodes.id, so no FK to nodes. Role-only now; designed to grow parent/order
-- columns in the later indent/reparent phase.
CREATE TABLE counterparty_revision_node_overrides (
    session_id UUID NOT NULL REFERENCES counterparty_revision_sessions(id),
    node_id    TEXT NOT NULL,         -- synthetic as_received node id (revised side)
    role       TEXT,                  -- operator role override (nullable; null = cleared)
    PRIMARY KEY (session_id, node_id)
);

-- An open negotiation point. decision/auto_flag/donna_research_citations JSONB
-- shapes are documented in SPEC §6.
CREATE TABLE issues (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id                     UUID NOT NULL REFERENCES contracts(id),
    node_id                         UUID REFERENCES nodes(id),  -- nullable = free-floating; mutable
    title                           TEXT NOT NULL,
    our_position                    TEXT,
    their_position                  TEXT,
    options_on_table                TEXT,
    recommended_position            TEXT,
    donna_counter_language          TEXT,
    status                          TEXT NOT NULL DEFAULT 'open'
                                    CHECK (status IN ('open','closed')),
    initiator                       TEXT NOT NULL
                                    CHECK (initiator IN ('operator','counterparty','donna')),
    auto_flag                       JSONB,    -- non-null only when initiator='donna' (DD-50)
    authority                       TEXT NOT NULL DEFAULT 'within-operator-authority'
                                    CHECK (authority IN ('within-operator-authority','needs-principal')),
    needs_legal_review              BOOLEAN NOT NULL DEFAULT false,  -- DD-47
    category                        TEXT NOT NULL DEFAULT 'commercial'
                                    CHECK (category IN
                                    ('commercial','legal','operational','counterparty_proposed_edit')),
    counterparty_revision_session_id UUID REFERENCES counterparty_revision_sessions(id),
    opened_in_snapshot_id           UUID REFERENCES contract_snapshots(id),
    resolved_in_snapshot_id         UUID REFERENCES contract_snapshots(id),
    decision                        JSONB,    -- populated on resolution (SPEC §6)
    donna_research_citations        JSONB,    -- null unless live research invoked
    impact                          TEXT,
    priority                        INTEGER,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at                     TIMESTAMPTZ
);
CREATE INDEX issues_contract_idx ON issues (contract_id);
CREATE INDEX issues_node_idx     ON issues (node_id);

-- (The issue_comments table was removed in DD-67: the issue description is
-- editable inline, so a separate comment thread is redundant.)

-- ============================================================================
-- Donna conversation state (DD-40) — windowed last-10-turns + rolling summary
-- ============================================================================

CREATE TABLE donna_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id     UUID NOT NULL REFERENCES contracts(id),
    running_summary TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- `kind` + `citations` persist the assistant turn's F10 answer treatment so a thread
-- reloaded from the DB rehydrates the same citation chips + kind styling a fresh ask
-- renders (without them a reloaded answer falls back to plain grounded text). Both are
-- nullable and set on assistant turns only — user messages leave them NULL. `citations`
-- is JSONB (node/issue ids), as F10's DonnaAskResponse.citations.
CREATE TABLE donna_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES donna_conversations(id),
    role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content         TEXT NOT NULL,
    kind            TEXT CHECK (kind IN ('answer','not_found','deflected')),
    citations       JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

-- ============================================================================
-- Brainstorm summaries (F10b, DD-73; storage shape per DD-77) — the per-issue
-- distilled summary of an EPHEMERAL brainstorm. Brainstorm's raw back-and-forth is
-- never persisted (reaffirms DD-42); on close Donna distils ONE compact, operator-
-- facing summary (question explored / position concluded / fallbacks considered) and
-- stores it here. A LINKED TABLE (not an issues column): an issue can be brainstormed
-- repeatedly, so each pass appends a row — the rows are the issue's brainstorm history.
-- Distinct from negotiation_patterns (DD-76, cross-deal learning); this is concrete,
-- this-issue, operator-facing continuity. The summary is NOT a grounding source —
-- Donna still grounds only on the committed ledger + clause text (DD-42/DD-73 §5).
-- ============================================================================

CREATE TABLE brainstorm_summaries (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id   UUID NOT NULL REFERENCES issues(id),
    question   TEXT,                                  -- what the brainstorm set out to resolve
    conclusion TEXT,                                  -- the position / landing concluded
    fallbacks  TEXT,                                  -- key fallbacks considered + why passed over
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX brainstorm_summaries_issue_idx ON brainstorm_summaries (issue_id);

-- ============================================================================
-- Negotiation patterns (F30, DD-76; amends DD-55/DD-73) — compact, operator-GLOBAL
-- insights distilled on issue-close from the COMMITTED issue ledger (never the raw
-- brainstorm transcript). NO contract_id — patterns transcend a single contract.
-- `subject_ref` is polymorphic (no FK): NULL for operator_style/legal_team_tendency,
-- clients.id for counterparty_behavior, contract_types.id for deal_type_norm — derived
-- deterministically from the closed issue's contract context, never from the model.
-- Patterns are a RETRIEVAL INPUT for Donna: never authoritative, never exported (§2.4).
-- ============================================================================

CREATE TABLE negotiation_patterns (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type            TEXT NOT NULL CHECK (subject_type IN
                            ('operator_style','counterparty_behavior','deal_type_norm','legal_team_tendency')),
    subject_ref             UUID,                                  -- polymorphic; NO FK
    insight                 TEXT NOT NULL,                         -- 1-3 sentence compact principle
    evidence_count          INTEGER NOT NULL DEFAULT 1,            -- reinforcement events
    confidence              REAL NOT NULL DEFAULT 0.5,             -- 0..1; bumped on reinforcement
    contradiction_flag      BOOLEAN NOT NULL DEFAULT false,        -- surfaced, never silent overwrite
    last_reinforced_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_reinforced_deal_id UUID REFERENCES deals(id),
    is_deleted              BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX negotiation_patterns_subject_idx
    ON negotiation_patterns (subject_type, subject_ref)
    WHERE is_deleted = false;

-- ============================================================================
-- Embeddings (pgvector) — built in Phase 2 (nodes) / Phase 2+ (comments).
-- VECTOR DIMENSION IS PROVISIONAL: tied to the Phase-2 embedding-model choice
-- (e.g. Voyage 1024, OpenAI 1536). Confirm/alter before first embed. [FLAGGED]
-- ============================================================================

CREATE TABLE node_embeddings (
    node_id     UUID PRIMARY KEY REFERENCES nodes(id),
    embedding   VECTOR(1024),
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()  -- staleness: nodes.updated_at > embedded_at
);

-- (comment_embeddings was removed in DD-67 alongside the issue_comments table.)

-- ============================================================================
-- Audit log — append-only; never updated. Every content/issue mutation.
-- ============================================================================

CREATE TABLE audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   UUID,
    actor       TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id);

-- ============================================================================
-- Migration bookkeeping (DD-57). This file is the canonical fresh-install
-- snapshot; db/migrations/*.sql evolve an existing DB without drop-rebuild.
-- A fresh DB built from this file is stamped with the baseline below, so the
-- runner (python -m backend.migrate) treats it as already current and applies
-- only deltas authored afterward. When you add db/migrations/NNNN_*.sql: fold
-- the same DDL into this file AND append ('NNNN_...') to the seed block below.
-- ============================================================================

CREATE TABLE schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES
    ('0000_baseline'),
    ('0001_issue_status_binary'),
    ('0002_drop_issue_comments'),
    ('0003_donna_recommendations'),
    ('0004_donna_message_meta'),
    ('0005_contract_last_export_at'),
    ('0006_negotiation_patterns'),
    ('0007_brainstorm_summaries');
