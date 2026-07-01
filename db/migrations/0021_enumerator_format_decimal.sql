-- 0021: Allow 'decimal' in nodes.enumerator_format (F03f / DD-99 amended).
-- Parenthesised decimal lists "(1)(2)(3)" are block enumerators too — detected on a
-- parenthesised lvlText, not on numFmt=decimal alone (backbone clauses are decimal).
-- Widen the CHECK from the four alpha/roman formats (migration 0020) to include
-- 'decimal'. Drop-and-re-add the inline CHECK; additive (no rows are invalidated).

ALTER TABLE nodes DROP CONSTRAINT IF EXISTS nodes_enumerator_format_check;
ALTER TABLE nodes ADD CONSTRAINT nodes_enumerator_format_check
    CHECK (enumerator_format IN ('lowerLetter','upperLetter','lowerRoman','upperRoman','decimal'));

INSERT INTO schema_migrations (version) VALUES ('0021_enumerator_format_decimal')
    ON CONFLICT DO NOTHING;
