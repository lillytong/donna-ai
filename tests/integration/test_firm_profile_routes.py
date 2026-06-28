"""Firm profile route (F32 v1 / DD-90): GET/PUT /firm-profile over a fake connection (the
repo is exercised through a patched `acquire`, no live DB). TestClient is used without its
context manager so the app lifespan never runs (mirrors test_revision_recommend_routes.py).

Fixtures are SYNTHETIC (public repo): no real firm / contract / party data."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.api import firm_profile as api
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeConn:
    """A one-row singleton store backing the get/set repo SQL."""

    def __init__(self, content: str) -> None:
        self.content = content

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any]:
        return {"content": self.content}

    async def execute(self, _sql: str, *args: Any) -> str:
        self.content = args[0]
        return "INSERT 0 1"


def _wire(monkeypatch: Any, conn: _FakeConn) -> None:
    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    monkeypatch.setattr(api, "acquire", fake_acquire)


app = FastAPI()
app.include_router(api.router)
client = TestClient(app)


def test_get_returns_content(monkeypatch: Any) -> None:
    _wire(monkeypatch, _FakeConn("We are a licensing firm."))
    resp = client.get("/firm-profile")
    assert resp.status_code == 200
    assert resp.json() == {"content": "We are a licensing firm."}


def test_put_upserts_and_returns_updated_content(monkeypatch: Any) -> None:
    conn = _FakeConn("old")
    _wire(monkeypatch, conn)
    resp = client.put("/firm-profile", json={"content": "new mandate"})
    assert resp.status_code == 200
    assert resp.json() == {"content": "new mandate"}
    assert conn.content == "new mandate"  # the upsert reached the store


def test_put_accepts_empty_content(monkeypatch: Any) -> None:
    conn = _FakeConn("something")
    _wire(monkeypatch, conn)
    resp = client.put("/firm-profile", json={"content": ""})
    assert resp.status_code == 200
    assert resp.json() == {"content": ""}
