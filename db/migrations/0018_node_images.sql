-- 0018: node_images table for storing images extracted from imported Word documents.
-- Images are stored as bytea alongside their original EMU dimensions so export
-- can re-embed them at the correct size. ON DELETE CASCADE keeps images in sync
-- with their parent node.

CREATE TABLE node_images (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id     UUID    NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    order_index INT     NOT NULL DEFAULT 0,
    mime_type   TEXT    NOT NULL DEFAULT 'image/png',
    cx_emu      BIGINT,
    cy_emu      BIGINT,
    data        BYTEA   NOT NULL
);

CREATE INDEX node_images_node_id_idx ON node_images(node_id);

INSERT INTO schema_migrations (version) VALUES ('0018_node_images')
    ON CONFLICT DO NOTHING;
