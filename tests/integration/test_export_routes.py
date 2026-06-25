"""Export route (F15b): status codes, headers, streamed body, and the snapshot +
pointer side effects. DB faked (a conn recording the snapshot insert / pointer
upsert), render real, record_event stubbed. TestClient used without its context
manager so the app lifespan never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import export
from backend.main import app
from backend.models.imports import StoredNode
from backend.models.settings import StoredContract
from backend.services import snapshot
from backend.services.export import clean_copy
from fastapi.testclient import TestClient

client = TestClient(app)

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_MAGIC = b"PK\x03\x04"
_NOW = datetime(2026, 6, 24, tzinfo=UTC)
_POINTER_SQL = "snapshot_pointers"


class _FakeConn:
    """Records the snapshot insert and any pointer upsert cut_snapshot issues."""

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
        return {
            "id": "s1",
            "contract_id": args[0],
            "label": args[1],
            "origin": args[3],
            "created_at": _NOW,
        }

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

    async def fake_record(_conn: Any, _event: Any) -> None:
        return None

    monkeypatch.setattr(export, "acquire", _fake_acquire)
    monkeypatch.setattr(export, "fetch_nodes", fake_fetch)
    monkeypatch.setattr(export, "get_contract", fake_get)
    monkeypatch.setattr(snapshot, "record_event", fake_record)


def _pointer_upserts(conn: _FakeConn) -> list[tuple[Any, ...]]:
    return [args for sql, args in conn.executes if _POINTER_SQL in sql]


def _spy_cut_snapshot(monkeypatch: Any) -> list[Any]:
    """Record any call to the snapshot service so grabs can assert it never fired."""
    calls: list[Any] = []
    real = snapshot.cut_snapshot

    async def _spy(conn: Any, contract_id: str, request: Any) -> Any:
        calls.append((contract_id, request))
        return await real(conn, contract_id, request)

    monkeypatch.setattr(clean_copy, "cut_snapshot", _spy)
    return calls


def test_export_returns_docx(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())

    resp = client.post("/contracts/c1/export", json={"recipient": "counterparty"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == _DOCX_MEDIA_TYPE
    assert resp.headers["content-disposition"] == 'attachment; filename="Project Crimson JVA.docx"'
    assert resp.content.startswith(_DOCX_MAGIC)


def test_send_cuts_snapshot(monkeypatch: Any) -> None:
    """A send (counterparty/legal) cuts exactly one origin='export' snapshot (DD-61)."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())
    calls = _spy_cut_snapshot(monkeypatch)

    resp = client.post("/contracts/c1/export", json={"recipient": "counterparty"})

    assert resp.status_code == 200
    assert len(calls) == 1
    assert len(conn.inserts) == 1
    contract_id, _label, _tree, origin = conn.inserts[0]
    assert contract_id == "c1"
    assert origin == "export"


def test_export_counterparty_advances_shared_pointer(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())

    resp = client.post("/contracts/c1/export", json={"recipient": "counterparty"})

    assert resp.status_code == 200
    upserts = _pointer_upserts(conn)
    assert len(upserts) == 1
    contract_id, party, direction, snapshot_id = upserts[0]
    assert (contract_id, party, direction) == ("c1", "counterparty", "shared")
    assert snapshot_id == "s1"


def test_export_legal_advances_legal_team_shared_pointer(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())

    resp = client.post("/contracts/c1/export", json={"recipient": "legal"})

    assert resp.status_code == 200
    upserts = _pointer_upserts(conn)
    assert len(upserts) == 1
    _cid, party, direction, _sid = upserts[0]
    assert (party, direction) == ("legal_team", "shared")


def test_grab_copy_only_cuts_no_snapshot(monkeypatch: Any) -> None:
    """Copy-only is a pure download (DD-61): no snapshot, no pointer, no stamping."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())
    calls = _spy_cut_snapshot(monkeypatch)

    resp = client.post("/contracts/c1/export", json={"recipient": "copy_only"})

    assert resp.status_code == 200
    assert calls == []  # snapshot service never called
    assert conn.inserts == []  # no snapshot row created (no snapshot SQL fired)
    assert _pointer_upserts(conn) == []  # no pointer advanced


def test_grab_internal_cuts_no_snapshot(monkeypatch: Any) -> None:
    """Internal is a pure download (DD-61): no snapshot, no pointer, no stamping."""
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=_contract())
    calls = _spy_cut_snapshot(monkeypatch)

    resp = client.post("/contracts/c1/export", json={"recipient": "internal"})

    assert resp.status_code == 200
    assert calls == []  # snapshot service never called
    assert conn.inserts == []  # no snapshot row created (no snapshot SQL fired)
    assert _pointer_upserts(conn) == []  # no pointer advanced


def test_export_404_when_no_nodes(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=[])

    resp = client.post("/contracts/missing/export", json={"recipient": "counterparty"})

    assert resp.status_code == 404
    assert conn.inserts == []  # no snapshot cut when there is nothing to export


def test_export_filename_falls_back_to_id_when_contract_absent(monkeypatch: Any) -> None:
    conn = _FakeConn()
    _install(monkeypatch, conn, nodes=_nodes(), contract=None)

    resp = client.post("/contracts/c1/export", json={"recipient": "internal"})

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="c1.docx"'


def test_export_rejects_unknown_recipient() -> None:
    resp = client.post("/contracts/c1/export", json={"recipient": "bogus"})
    assert resp.status_code == 422
