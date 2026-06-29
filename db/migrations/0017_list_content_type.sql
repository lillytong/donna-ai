-- 0017: Add 'list' to nodes.content_type check constraint.
-- Enables bullet-list paragraphs detected on import to be stored with their own
-- content type rather than being collapsed into 'prose'. The constraint is
-- dropped and recreated to include the new value; existing rows are unaffected.

ALTER TABLE nodes DROP CONSTRAINT IF EXISTS nodes_content_type_check;
ALTER TABLE nodes ADD CONSTRAINT nodes_content_type_check
    CHECK (content_type IN ('prose','table','attachment','list'));

INSERT INTO schema_migrations (version) VALUES ('0017_list_content_type')
    ON CONFLICT DO NOTHING;
