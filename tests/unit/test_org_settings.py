"""Operator organization identity (F25, DD-44).

The org name is a config value (not a DB entity). It must flow to the export
read-site (`services/export/redline.py: redline_author or operator_actor`) so every
tracked change is authored by the operator org — never blank, never "Donna". Built
without editing that read-site: the Settings validator populates `redline_author`.

No live DB: settings are constructed from a clean env, and the export read-site is
exercised via its real `_resolve_author` against an injected Settings.
"""

from __future__ import annotations

from typing import Any

from backend.api.settings import get_organization
from backend.config.settings import DEFAULT_OPERATOR_ORG_NAME, Settings
from backend.services.export import redline


def _settings(monkeypatch: Any, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql://donna:donna@localhost:5432/donna")
    for key in ("DONNA_OPERATOR_ORG_NAME", "DONNA_REDLINE_AUTHOR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_org_name_flows_to_export_author(monkeypatch: Any) -> None:
    s = _settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Northwind Trading Ltd")
    assert s.operator_org_name == "Northwind Trading Ltd"
    assert s.export_author == "Northwind Trading Ltd"
    assert s.redline_author == "Northwind Trading Ltd"


def test_explicit_redline_author_overrides_org_name(monkeypatch: Any) -> None:
    s = _settings(
        monkeypatch,
        DONNA_OPERATOR_ORG_NAME="Org A",
        DONNA_REDLINE_AUTHOR="Legal Department",
    )
    assert s.export_author == "Legal Department"


def test_unset_org_falls_back_to_default_never_blank_never_donna(monkeypatch: Any) -> None:
    s = _settings(monkeypatch)
    assert s.operator_org_name == ""
    assert s.export_author == DEFAULT_OPERATOR_ORG_NAME
    assert s.export_author.strip() != ""
    assert "Donna" not in s.export_author


def test_export_read_site_resolves_to_org_name(monkeypatch: Any) -> None:
    s = _settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Northwind Trading Ltd")
    monkeypatch.setattr(redline, "get_settings", lambda: s)
    assert redline._resolve_author() == "Northwind Trading Ltd"
    assert "Donna" not in redline._resolve_author()


def test_export_read_site_never_donna_when_unset(monkeypatch: Any) -> None:
    s = _settings(monkeypatch)
    monkeypatch.setattr(redline, "get_settings", lambda: s)
    author = redline._resolve_author()
    assert author == DEFAULT_OPERATOR_ORG_NAME
    assert author.strip() != ""
    assert "Donna" not in author


async def test_organization_endpoint_returns_resolved_org(monkeypatch: Any) -> None:
    s = _settings(monkeypatch, DONNA_OPERATOR_ORG_NAME="Northwind Trading Ltd")
    monkeypatch.setattr("backend.api.settings.get_settings", lambda: s)
    result = await get_organization()
    assert result.organization_name == "Northwind Trading Ltd"
    assert result.export_author == "Northwind Trading Ltd"
    assert result.editable is False
    assert "Donna" not in result.export_author


async def test_organization_endpoint_unset_is_never_blank(monkeypatch: Any) -> None:
    s = _settings(monkeypatch)
    monkeypatch.setattr("backend.api.settings.get_settings", lambda: s)
    result = await get_organization()
    assert result.organization_name == ""
    assert result.export_author == DEFAULT_OPERATOR_ORG_NAME
    assert "Donna" not in result.export_author
