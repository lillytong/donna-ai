"""Persistence + resolution for the operator's organization identity (F25 / DD-44).

A settings-style SINGLE row (boolean singleton PK) holding the editable org-name OVERRIDE
over the DONNA_OPERATOR_ORG_NAME config value. DB integration + value resolution only, no
business logic; raw SQL + asyncpg per the project convention.

Resolution layering (single source of truth for the whole app):
  * organization name : DB override (if non-empty) -> config value -> '' (unset)
  * export author     : explicit DONNA_REDLINE_AUTHOR (if set) -> resolved org name ->
                        DEFAULT_OPERATOR_ORG_NAME. Never blank, never "Donna".
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import DEFAULT_OPERATOR_ORG_NAME, get_settings

_GET_ORG_NAME = "SELECT organization_name FROM operator_organization WHERE id = true"

# Singleton upsert: the boolean PK keeps this to one row, so the seeded row is updated in
# place and a missing row self-heals — get/set never depend on the seed having run.
_SET_ORG_NAME = """
INSERT INTO operator_organization (id, organization_name, updated_at)
VALUES (true, $1, now())
ON CONFLICT (id) DO UPDATE SET organization_name = EXCLUDED.organization_name, updated_at = now()
"""


async def get_org_name_override(conn: Any) -> str:
    """The stored DB override org name, or '' when unset (the config-fallback case)."""
    record = await conn.fetchrow(_GET_ORG_NAME)
    return record["organization_name"] if record is not None else ""


async def set_org_name_override(conn: Any, organization_name: str) -> None:
    """Upsert the single operator-organization row (idempotent singleton)."""
    await conn.execute(_SET_ORG_NAME, organization_name)


async def resolve_org_name(conn: Any) -> str:
    """The effective org name: DB override (if non-empty) else the config value else ''."""
    override = (await get_org_name_override(conn)).strip()
    return override or get_settings().operator_org_name.strip()


async def resolve_export_author(conn: Any) -> str:
    """The resolved redline/export author (DD-44). Never blank, never 'Donna'.

    Explicit DONNA_REDLINE_AUTHOR wins (a deliberate per-deployment author distinct from the
    org name); otherwise the effective org name; otherwise the neutral default.
    """
    explicit = get_settings().redline_author.strip()
    if explicit:
        return explicit
    return (await resolve_org_name(conn)) or DEFAULT_OPERATOR_ORG_NAME
