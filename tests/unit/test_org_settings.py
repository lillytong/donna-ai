"""Operator organization identity — config-resolution layer (F25, DD-44).

The org name is now an editable, DB-backed override (see test_operator_org.py for the DB
resolver). This file covers the CONFIG fallback: `export_author` resolves explicit
DONNA_REDLINE_AUTHOR → org name → neutral default, never blank, never "Donna". `redline_author`
is kept RAW (empty unless DONNA_REDLINE_AUTHOR is set) so it signals an explicit author.

No live DB: settings are constructed from a clean env.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import DEFAULT_OPERATOR_ORG_NAME, Settings


def _settings(monkeypatch: Any, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql://donna:donna@localhost:5432/donna")
    for key in ("DONNA_OPERATOR_ORG_NAME", "DONNA_REDLINE_AUTHOR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_org_name_resolves_to_export_author(monkeypatch: Any) -> None:
    s = _settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Northwind Trading Ltd")
    assert s.operator_org_name == "Northwind Trading Ltd"
    assert s.export_author == "Northwind Trading Ltd"
    # redline_author is the raw explicit-author signal — empty when no override is set.
    assert s.redline_author == ""


def test_explicit_redline_author_overrides_org_name(monkeypatch: Any) -> None:
    s = _settings(
        monkeypatch,
        DONNA_OPERATOR_ORG_NAME="Org A",
        DONNA_REDLINE_AUTHOR="Legal Department",
    )
    assert s.redline_author == "Legal Department"
    assert s.export_author == "Legal Department"


def test_unset_org_falls_back_to_default_never_blank_never_donna(monkeypatch: Any) -> None:
    s = _settings(monkeypatch)
    assert s.operator_org_name == ""
    assert s.redline_author == ""
    assert s.export_author == DEFAULT_OPERATOR_ORG_NAME
    assert s.export_author.strip() != ""
    assert "Donna" not in s.export_author
