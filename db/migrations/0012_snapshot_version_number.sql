-- Persisted lineage version numbers on contract_snapshots (DD-85 / DD-87 §1).
--
-- DD-70/DD-75 derived a snapshot's v-number with ROW_NUMBER() OVER (ORDER BY
-- created_at). DD-85 adds version-delete with GAP PRESERVATION (delete v2 →
-- lineage v1,v3,v4; next mint = v5), which ROW_NUMBER cannot represent: it would
-- renumber survivors (v3,v4 → v2,v3) after a middle-delete, killing the gap. So
-- the numbering basis moves from derived to STORED: a monotonic, never-reused
-- integer per contract, minted as COALESCE(MAX(version_number),0)+1.
--
-- Backfill reproduces the EXACT old ROW_NUMBER values (PARTITION BY contract_id
-- ORDER BY created_at), so existing lineages are visually unchanged. Forward-only;
-- idempotent (ADD COLUMN IF NOT EXISTS + WHERE version_number IS NULL backfill;
-- the SET NOT NULL / ADD CONSTRAINT re-run harmlessly once satisfied).
ALTER TABLE contract_snapshots
    ADD COLUMN IF NOT EXISTS version_number INTEGER;

UPDATE contract_snapshots cs
SET version_number = ranked.rn
FROM (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY contract_id ORDER BY created_at) AS rn
    FROM contract_snapshots
) ranked
WHERE cs.id = ranked.id
  AND cs.version_number IS NULL;

ALTER TABLE contract_snapshots
    ALTER COLUMN version_number SET NOT NULL;

ALTER TABLE contract_snapshots
    ADD CONSTRAINT contract_snapshots_version_unique UNIQUE (contract_id, version_number);
