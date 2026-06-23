"""Forward-only SQL migration runner. Infra only — no business logic.

`db/schema.sql` stays the canonical full snapshot a fresh database is built from
(DD-57). This runner is the no-data-loss path for evolving a database that
already holds data: it applies every `db/migrations/*.sql` not yet recorded in
`schema_migrations`, in filename order, each inside its own transaction.

A fresh database built from `schema.sql` is pre-stamped with the current
baseline (schema.sql seeds `schema_migrations`), so this runner is a no-op there
and only applies deltas authored after that baseline.

Run with: python -m backend.migrate
"""

import asyncio
from pathlib import Path

import asyncpg

from backend.config.settings import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def discover_migrations(migrations_dir: Path) -> list[tuple[str, str]]:
    """(version, sql) for every db/migrations/*.sql, sorted by filename.

    The version is the filename stem, so NNNN_description.sql sorts and records
    by its numeric prefix.
    """
    return [
        (path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(migrations_dir.glob("*.sql"))
    ]


def pending_migrations(
    available: list[tuple[str, str]], applied: set[str]
) -> list[tuple[str, str]]:
    """Available migrations not yet recorded, preserving sorted order."""
    return [(version, sql) for version, sql in available if version not in applied]


async def _applied_versions(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {str(row["version"]) for row in rows}


async def run_migrations(conn: asyncpg.Connection) -> list[str]:
    """Apply every pending migration in order; return the versions applied."""
    await conn.execute(_ENSURE_TABLE)
    applied = await _applied_versions(conn)
    pending = pending_migrations(discover_migrations(MIGRATIONS_DIR), applied)
    done: list[str] = []
    for version, sql in pending:
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
        done.append(version)
    return done


async def main() -> None:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        applied = await run_migrations(conn)
    finally:
        await conn.close()
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("No pending migrations.")


if __name__ == "__main__":
    asyncio.run(main())
