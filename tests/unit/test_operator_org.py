"""Operator-organization repo (F25 / DD-44): the editable org-name DB override and the
value resolvers (org name, export author). No live DB, no LLM — a fake connection serves
one singleton row and records execute() SQL + args.

Resolution layering under test:
  * org name     : DB override (if non-empty) → config value → ''
  * export author: explicit DONNA_REDLINE_AUTHOR → resolved org name → neutral default;
                   never blank, never "Donna".

Fixtures are SYNTHETIC (public repo): no real firm / contract / party names or values.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import DEFAULT_OPERATOR_ORG_NAME, Settings
from backend.services import operator_org_repo
from backend.services.operator_org_repo import (
    get_org_name_override,
    resolve_export_author,
    resolve_org_name,
    set_org_name_override,
)


class _FakeConn:
    """Serves one singleton row and records execute() SQL + args."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._row

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "INSERT 0 1"


def _settings(monkeypatch: Any, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql://donna:donna@localhost:5432/donna")
    for key in ("DONNA_OPERATOR_ORG_NAME", "DONNA_REDLINE_AUTHOR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _patch_settings(monkeypatch: Any, **env: str) -> None:
    s = _settings(monkeypatch, **env)
    monkeypatch.setattr(operator_org_repo, "get_settings", lambda: s)


# --- repo: get / set -------------------------------------------------------


async def test_get_returns_stored_override() -> None:
    conn = _FakeConn({"organization_name": "Northwind Trading Ltd"})
    assert await get_org_name_override(conn) == "Northwind Trading Ltd"


async def test_get_returns_empty_when_no_row() -> None:
    conn = _FakeConn(None)
    assert await get_org_name_override(conn) == ""


async def test_set_issues_singleton_upsert() -> None:
    conn = _FakeConn(None)
    await set_org_name_override(conn, "Northwind Trading Ltd")
    assert len(conn.executes) == 1
    sql, args = conn.executes[0]
    assert "INSERT INTO operator_organization" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert args == ("Northwind Trading Ltd",)


# --- resolve_org_name: DB override wins over config ------------------------


async def test_db_override_wins_over_config(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Config Org")
    conn = _FakeConn({"organization_name": "DB Override Org"})
    assert await resolve_org_name(conn) == "DB Override Org"


async def test_blank_override_falls_back_to_config(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Config Org")
    conn = _FakeConn({"organization_name": "   "})
    assert await resolve_org_name(conn) == "Config Org"


async def test_no_override_no_config_resolves_empty(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch)
    conn = _FakeConn({"organization_name": ""})
    assert await resolve_org_name(conn) == ""


# --- resolve_export_author: precedence + never blank / never Donna ---------


async def test_export_author_uses_db_override(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Config Org")
    conn = _FakeConn({"organization_name": "DB Override Org"})
    author = await resolve_export_author(conn)
    assert author == "DB Override Org"
    assert "Donna" not in author


async def test_explicit_redline_author_wins_over_override(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch, DONNA_REDLINE_AUTHOR="Legal Department")
    conn = _FakeConn({"organization_name": "DB Override Org"})
    assert await resolve_export_author(conn) == "Legal Department"


async def test_export_author_unset_is_default_never_blank_never_donna(monkeypatch: Any) -> None:
    _patch_settings(monkeypatch)
    conn = _FakeConn({"organization_name": ""})
    author = await resolve_export_author(conn)
    assert author == DEFAULT_OPERATOR_ORG_NAME
    assert author.strip() != ""
    assert "Donna" not in author
