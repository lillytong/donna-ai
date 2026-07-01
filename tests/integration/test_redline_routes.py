"""Redline export route (F15): status codes, headers, streamed docx, and the
baseline-resolution 4xx paths. The router is not registered in main.py (wired in
centrally post-merge), so the test mounts it on a local app. DB faked; render real.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

from backend.api import redline as redline_api
from backend.models.settings import StoredClient, StoredContract
from backend.services.export import filename
from fastapi import FastAPI
from fastapi.testclient import TestClient

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_MAGIC = b"PK\x03\x04"
_NOW = datetime(2026, 6, 24, tzinfo=UTC)
_BASE_TS = datetime(2026, 6, 1, tzinfo=UTC)
_CLIENT_NAME = "Acme"
_TODAY = date.today().strftime("%y%m%d")

app = FastAPI()
app.include_router(redline_api.router)
client = TestClient(app)


class _FakeConn:
    def __init__(self, *, pointer: dict[str, Any] | None) -> None:
        self.pointer = pointer

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "node_versions" in sql:
            return [
                {
                    "node_id": "e",
                    "body_before": "Old wording.",
                    "body_after": "New wording.",
                    "created_at": datetime(2026, 6, 10, tzinfo=UTC),
                    "is_deleted": False,
                }
            ]
        if "FROM nodes" in sql:
            return [
                {
                    "id": "e",
                    "parent_id": None,
                    "order_index": 100,
                    "content_type": "prose",
                    "heading": None,
                    "body": "New wording.",
                    "table_data": None,
                    "plain_text": "New wording.",
                    "role": "clause",
                    "has_placeholder": False,
                    "enumerator_format": None,
                }
            ]
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "snapshot_pointers" in sql:
            return self.pointer
        if "FROM contract_snapshots" in sql:
            return {
                "id": "snapB",
                "contract_id": "c1",
                "label": "shared",
                "tree": json.dumps(
                    [
                        {
                            "id": "e",
                            "parent_id": None,
                            "order_index": 100,
                            "content_type": "prose",
                            "heading": None,
                            "body": "Old wording.",
                            "is_deleted": False,
                        }
                    ]
                ),
                "origin": "export",
                "created_at": _BASE_TS,
            }
        return None


def _contract() -> StoredContract:
    return StoredContract(
        id="c1",
        client_id="cl1",
        deal_id="d1",
        contract_type_id="t1",
        name="Project Crimson JVA",
        status="drafting",
        style_config={},
        created_at=_NOW,
    )


def _install(monkeypatch: Any, *, pointer: dict[str, Any] | None) -> None:
    conn = _FakeConn(pointer=pointer)

    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    async def _fake_get(_conn: Any, _cid: str) -> StoredContract:
        return _contract()

    async def _fake_get_client(_conn: Any, _cid: str) -> StoredClient:
        return StoredClient(
            id="cl1",
            name=_CLIENT_NAME,
            relationship_type="client",
            status="active",
            created_at=_NOW,
        )

    async def _fake_list_snapshots(_conn: Any, _cid: str) -> list[Any]:
        return []

    monkeypatch.setattr(redline_api, "acquire", _fake_acquire)
    monkeypatch.setattr(redline_api, "get_contract", _fake_get)
    monkeypatch.setattr(filename, "get_client", _fake_get_client)
    monkeypatch.setattr(filename, "list_snapshots", _fake_list_snapshots)


def _document_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_redline_returns_tracked_changes_docx(monkeypatch: Any) -> None:
    _install(monkeypatch, pointer={"id": "snapB", "created_at": _BASE_TS})

    resp = client.post("/contracts/c1/redline-export", json={})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == _DOCX_MEDIA_TYPE
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="{_CLIENT_NAME}_Project Crimson JVA_{_TODAY}_v1_redline.docx"'
    )
    assert resp.content.startswith(_DOCX_MAGIC)
    xml = _document_xml(resp.content)
    assert "<w:ins " in xml and "<w:del " in xml


def test_redline_409_when_no_baseline(monkeypatch: Any) -> None:
    _install(monkeypatch, pointer=None)

    resp = client.post("/contracts/c1/redline-export", json={})

    assert resp.status_code == 409


def test_redline_404_when_override_snapshot_wrong_contract(monkeypatch: Any) -> None:
    _install(monkeypatch, pointer=None)

    resp = client.post("/contracts/other/redline-export", json={"snapshot_id": "snapB"})

    assert resp.status_code == 404


def test_redline_rejects_non_string_snapshot_id() -> None:
    resp = client.post("/contracts/c1/redline-export", json={"snapshot_id": 123})
    assert resp.status_code == 422
