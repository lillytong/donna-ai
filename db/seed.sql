-- Default seed data — generic and safe for the public repo (no client / deal /
-- contract data ever lives here). Loaded after schema.sql on a fresh database
-- (docker-entrypoint-initdb.d, or `psql -f db/seed.sql`).
--
-- Why this exists: the import Context step requires a contract type, so a brand-
-- new database with zero types dead-ends on a foreign-key error. These are the
-- §9 defaults; add/rename your own in Settings.

INSERT INTO contract_types (name, is_default)
SELECT * FROM (VALUES
    ('Licence', false),
    ('Offtake', false),
    ('Joint Venture', false),
    ('NDA', false),
    ('Amendment', false)
) AS v(name, is_default)
WHERE NOT EXISTS (SELECT 1 FROM contract_types);
