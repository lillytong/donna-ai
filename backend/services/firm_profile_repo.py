"""Persistence for the global firm profile (F32 v1 / DD-90) — the operator-authored,
firm-level free-text mandate injected into Donna's revision-recommendation grounding. DB
integration only, no business logic. A settings-style SINGLE row (boolean singleton PK);
raw SQL + asyncpg per the project convention."""

from __future__ import annotations

from typing import Any

_GET_FIRM_PROFILE = "SELECT content FROM firm_profile WHERE id = true"

# Singleton upsert: the boolean PK keeps this to one row, so the seeded row is updated in
# place and a missing row self-heals — get/set never depend on the seed having run.
_SET_FIRM_PROFILE = """
INSERT INTO firm_profile (id, content, updated_at)
VALUES (true, $1, now())
ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, updated_at = now()
"""


async def get_firm_profile(conn: Any) -> str:
    """The single global firm-profile document, or '' when unset (the no-op grounding case)."""
    record = await conn.fetchrow(_GET_FIRM_PROFILE)
    return record["content"] if record is not None else ""


async def set_firm_profile(conn: Any, content: str) -> None:
    """Upsert the single global firm-profile row (idempotent singleton)."""
    await conn.execute(_SET_FIRM_PROFILE, content)
