"""Operator-organization route (F25 / DD-44): GET/PUT /organization over a fake connection
(the repo runs through a patched `acquire`, no live DB). TestClient is used without its
context manager so the app lifespan never runs (mirrors test_firm_profile_routes.py).

The org name is a DB-backed editable override; the resolved export author falls back to the
config value (here the neutral default, since the env carries no org name). Fixtures are
SYNTHETIC (public repo): no real firm / contract / party data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.api import settings as api
from backend.config.settings import DEFAULT_OPERATOR_ORG_NAME, Settings
from backend.services import operator_org_repo
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeConn:
    """A one-row singleton store backing the get/set repo SQL; ignores audit inserts."""

    def __init__(self, organization_name: str) -> None:
        self.organization_name = organization_name

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any]:
        return {"organization_name": self.organization_name}

    async def execute(self, sql: str, *args: Any) -> str:
        # The org upsert sets the name; the audit insert is ignored in this fake.
        if "operator_organization" in sql:
            self.organization_name = args[0]
        return "INSERT 0 1"


def _wire(monkeypatch: Any, conn: _FakeConn) -> None:
    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    monkeypatch.setattr(api, "acquire", fake_acquire)
    # Clean env so the export author resolves to the neutral default, not the host's.
    monkeypatch.setenv("DATABASE_URL", "postgresql://donna:donna@localhost:5432/donna")
    monkeypatch.delenv("DONNA_OPERATOR_ORG_NAME", raising=False)
    monkeypatch.delenv("DONNA_REDLINE_AUTHOR", raising=False)
    s = Settings(_env_file=None)
    monkeypatch.setattr(operator_org_repo, "get_settings", lambda: s)


app = FastAPI()
app.include_router(api.router)
client = TestClient(app)


def test_get_returns_resolved_org(monkeypatch: Any) -> None:
    _wire(monkeypatch, _FakeConn("Northwind Trading Ltd"))
    resp = client.get("/organization")
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_name"] == "Northwind Trading Ltd"
    assert body["export_author"] == "Northwind Trading Ltd"
    assert body["editable"] is True


def test_get_unset_falls_back_to_default(monkeypatch: Any) -> None:
    _wire(monkeypatch, _FakeConn(""))
    resp = client.get("/organization")
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_name"] == ""
    assert body["export_author"] == DEFAULT_OPERATOR_ORG_NAME
    assert "Donna" not in body["export_author"]


def test_put_upserts_and_returns_resolved(monkeypatch: Any) -> None:
    conn = _FakeConn("old name")
    _wire(monkeypatch, conn)
    resp = client.put("/organization", json={"organization_name": "  Northwind Trading Ltd  "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_name"] == "Northwind Trading Ltd"  # trimmed
    assert body["export_author"] == "Northwind Trading Ltd"
    assert conn.organization_name == "Northwind Trading Ltd"  # the upsert reached the store
