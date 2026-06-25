"""Mark-as-sent route (DD-71): HTTP shape + the drift gate. The router is not
registered in main.py (wired centrally post-merge), so the test mounts it on a
local app. DB faked end-to-end through the real service; record_event stubbed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api import mark_sent as mark_sent_api
from backend.services import mark_sent as mark_sent_svc
from backend.services import snapshot as snapshot_svc
from fastapi import FastAPI
from fastapi.testclient import TestClient

_NOW = datetime(2026, 6, 24, tzinfo=UTC)

app = FastAPI()
app.include_router(mark_sent_api.router)
client = TestClient(app)


class _FakeConn:
    def __init__(self, *, drift: bool, snapshot_count: int) -> None:
        self._drift = drift
        self._snapshot_count = snapshot_count
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, _sql: str, *_args: Any) -> list[Any]:
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "snapshot_count" in sql:
            return {
                "last_export_at": None if self._drift else _NOW,
                "snapshot_count": self._snapshot_count,
                "drift": self._drift,
            }
        if "INSERT INTO contract_snapshots" in sql:
            return {
                "id": "snapNEW",
                "contract_id": args[0],
                "label": args[1],
                "origin": args[3],
                "created_at": _NOW,
            }
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "UPDATE 1"


def _install(monkeypatch: pytest.MonkeyPatch, *, drift: bool, snapshot_count: int = 0) -> None:
    conn = _FakeConn(drift=drift, snapshot_count=snapshot_count)

    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    monkeypatch.setattr(mark_sent_api, "acquire", _fake_acquire)
    monkeypatch.setattr(snapshot_svc, "record_event", _noop)
    monkeypatch.setattr(mark_sent_svc, "record_event", _noop)


def test_mark_sent_counterparty(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, drift=False)

    resp = client.post("/contracts/c1/mark-sent", json={"recipient": "counterparty"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["marked"] is True
    assert body["drift"] is False
    assert body["pointers"] == ["counterparty"]
    assert body["version"] == 1
    assert body["snapshot_id"] == "snapNEW"


def test_mark_sent_both_two_pointers(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, drift=False, snapshot_count=1)

    resp = client.post("/contracts/c1/mark-sent", json={"recipient": "both"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pointers"] == ["counterparty", "legal_team"]
    assert body["version"] == 2


def test_mark_sent_drift_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, drift=True)

    resp = client.post("/contracts/c1/mark-sent", json={"recipient": "counterparty"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["marked"] is False
    assert body["drift"] is True
    assert body["snapshot_id"] is None


def test_mark_sent_drift_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, drift=True)

    resp = client.post(
        "/contracts/c1/mark-sent",
        json={"recipient": "counterparty", "acknowledge_drift": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["marked"] is True
    assert body["snapshot_id"] == "snapNEW"


def test_mark_sent_rejects_unknown_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, drift=False)
    resp = client.post("/contracts/c1/mark-sent", json={"recipient": "bogus"})
    assert resp.status_code == 422
