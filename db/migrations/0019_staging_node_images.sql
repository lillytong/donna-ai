-- 0019: Staging table for images extracted at preview time.
-- Images are written here during preview_docx and moved to node_images at commit.
-- Cleaned up automatically when the contract is committed.
-- Keyed by (contract_id, node_index) where node_index is the TreeNode.index
-- (sequential flat position) so it can be resolved via insert_nodes' id_map.

CREATE TABLE staging_node_images (
    contract_id  UUID NOT NULL,
    node_index   INT  NOT NULL,
    mime_type    TEXT NOT NULL DEFAULT 'image/png',
    cx_emu       BIGINT,
    cy_emu       BIGINT,
    data         BYTEA NOT NULL,
    PRIMARY KEY (contract_id, node_index)
);

INSERT INTO schema_migrations (version) VALUES ('0019_staging_node_images')
    ON CONFLICT DO NOTHING;
