"""Export route (F15b, DD-71): export is now a pure grab — no body, no recipient,
no snapshot, no pointer. It only stamps `last_export_at` (DD-72 drift marker) and
streams the .docx. DB faked (a conn recording any execute), render real. TestClient
used without its context manager so the app lifespan never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

from backend.api import export
from backend.main import app
from backend.models.imports import StoredNode
from backend.models.settings import StoredClient, StoredContract
from backend.services.export import filename
from fastapi.testclient import TestClient

client = TestClient(app)

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_MAGIC = b"PK\x03\x04"
_NOW = datetime(2026, 6, 24, tzinfo=UTC)
_CLIENT_NAME = "Acme"
_TODAY = date.today().strftime("%y%m%d")


class _FakeConn:
    """Records every execute (so the export's last_export_at stamp is observable)
    and every fetchrow (which a snapshot cut WOULD issue — asserted never to fire)."""

    def __init__(self) -> None:
        self.inserts: list[tuple[Any, ...]] = []
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, _sql: str, *_args: Any) -> list[Any]:
        return []

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any]:
        self.inserts.append(args)
        return {"id": "s1", "contract_id": args[0] if args else None, "created_at": _NOW}

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "UPDATE 1"


def _contract() -> StoredContract:
    return StoredContract(
        id="c1",
        client_id="cl1",
        deal_id="d1",
        contract_type_id="t1",
        name="Project Crimson JVA",
        status="drafting",
        style_config={"levels": {"0": {"bold": True, "caps": True}}},
        created_at=_NOW,
    )


def _nodes() -> list[StoredNode]:
    return [
        StoredNode(
            id="a", parent_id=None, order_index=100, content_type="prose", heading="Definitions"
        ),
        StoredNode(
            id="b", parent_id="a", order_index=100, content_type="prose", body="Meaning of terms."
        ),
    ]


def _install(
    monkeypatch: Any,
    conn: _FakeConn,
    *,
    nodes: list[StoredNode],
    contract: StoredContract | None = None,
) -> None:
    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    async def fake_fetch(_conn: Any, _cid: str) -> list[StoredNode]:
        return nodes

    async def fake_get(_conn: Any, _cid: str) -> StoredContract | None:
        return contract

    async def fake_get_client(_conn: Any, _cid: str) -> StoredClient:
        return StoredClient(
            id="cl1",
            name=_CLIENT_NAME,
            relationship_type="client",
            status="active",
            created_at=_NOW,
        )

    async def fake_list_snapshots(_conn: Any, _cid: str) -> list[Any]:
        return []

    monkeypatch.setattr(export, "acquire", _fake_acquire)
    monkeypatch.setattr(export, "fetch_nodes", fake_fetch)
    monkeypatch.setattr(export, "get_contract", fake_get)
    monkeypatch.setattr(filename, "get_client", fake_get_client)
    monkeypatch.setattr(filename, "list_snapshots", fake_list_snapshots)


def _last_export_stamps(conn: _FakeConn) -> list[tuple[Any, ...]]:
    return [args for sql, args in conn.executes if "last_export_at" in sql]


def test_export_returns_docx(monkeypatch: Any) -> None:
    """POST with no body streams the clean copy at the working version (v1)."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())

    resp = client.post("/contracts/c1/export")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == _DOCX_MEDIA_TYPE
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="{_CLIENT_NAME}_Project Crimson JVA_{_TODAY}_v1.docx"'
    )
    assert resp.content.startswith(_DOCX_MAGIC)


def test_export_cuts_no_snapshot_or_pointer(monkeypatch: Any) -> None:
    """DD-71: export is a pure grab — it cuts no snapshot and advances no pointer."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())

    resp = client.post("/contracts/c1/export")

    assert resp.status_code == 200
    assert conn.inserts == []  # no snapshot insert (fetchrow) fired
    assert not any("snapshot_pointers" in sql for sql, _ in conn.executes)


def test_export_stamps_last_export_at(monkeypatch: Any) -> None:
    """DD-72: each export stamps contracts.last_export_at for the drift marker."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())

    resp = client.post("/contracts/c1/export")

    assert resp.status_code == 200
    stamps = _last_export_stamps(conn)
    assert len(stamps) == 1
    assert stamps[0] == ("c1",)


def test_export_404_when_no_nodes(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=[])

    resp = client.post("/contracts/missing/export")

    assert resp.status_code == 404
    assert _last_export_stamps(conn) == []  # nothing stamped when there is nothing to export


def test_export_filename_falls_back_to_generic_when_contract_absent(monkeypatch: Any) -> None:
    """No contract row → generic placeholder fields and working version v1."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=None)

    resp = client.post("/contracts/c1/export")

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="Client_Contract_{_TODAY}_v1.docx"'
    )
