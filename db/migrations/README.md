# Schema migrations (DD-57)

Forward-only SQL deltas that evolve a database **already holding data**, without
the drop-and-rebuild that destroys it.

`../schema.sql` stays the canonical full snapshot a fresh database is built from.
This folder is the no-data-loss path for changing a live one.

## Discipline — every schema change does all three

1. Add `NNNN_short_description.sql` here (zero-padded, next number) — the **delta
   only** (`ALTER TABLE … ADD COLUMN …`, `CREATE TABLE …`, etc.). Forward-only;
   never drop a column/table that holds data without a planned dual-write.
2. Fold the same change into `../schema.sql` so it stays canonical for fresh
   installs.
3. Add `('NNNN_short_description')` to the `schema_migrations` seed block at the
   end of `../schema.sql`, so a fresh DB is stamped as already-current and the
   runner skips it there.

## Running

```bash
python -m backend.migrate
```

Applies every `*.sql` here not yet recorded in `schema_migrations`, in filename
order, each in its own transaction. Idempotent: re-running applies nothing new.

## Adopting on an existing unmanaged DB

A database built from an older `schema.sql` has no `schema_migrations` table. With
**no real data yet**, rebuild once from the current `schema.sql`
(`docker compose down -v && docker compose up -d db`) — that stamps the baseline.
From then on the DB is managed and every change flows through a migration file.
