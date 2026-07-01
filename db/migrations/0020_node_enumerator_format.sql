-- 0020: Add nullable nodes.enumerator_format for block enumerated items (F03f / DD-98 amended / DD-99).
-- A block enumerated item ((a)(b)(c) / (i)(ii)(iii)) imported from Word auto-numbering
-- stores its list FORMAT here; the marker glyph itself is DERIVED from position
-- (auto-renumber, same model as decimal clause numbers, DD-02) and never stored in body.
-- NULL = not an auto-numbered enumerated item (ordinary clause, or a literal-marker item
-- whose marker lives in body text). Additive + reversible; existing rows unaffected.

ALTER TABLE nodes ADD COLUMN IF NOT EXISTS enumerator_format TEXT
    CHECK (enumerator_format IN ('lowerLetter','upperLetter','lowerRoman','upperRoman'));

INSERT INTO schema_migrations (version) VALUES ('0020_node_enumerator_format')
    ON CONFLICT DO NOTHING;
