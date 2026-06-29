"""Import + tree routes: request parsing, response shape, status codes.

The DB and service boundaries are mocked — no live database. TestClient is used
without its context manager so the app lifespan (pool open/close) never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import imports
from backend.main import app
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.models.contract_tree import NodeRow
from backend.models.imports import (
    CandidateNode,
    ImportResult,
    PreviewResponse,
    StoredNode,
    TrackedChangeReport,
)
from fastapi.testclient import TestClient

client = TestClient(app)

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


class _FakeConn:
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, sql: str, *_args: Any) -> list[Any]:
        return []


@asynccontextmanager
async def _fake_acquire() -> AsyncIterator[_FakeConn]:
    yield _FakeConn()


async def _noop_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
    return StoredAuditEvent(
        id="audit-1",
        event_type=event.event_type,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        actor=event.actor,
        payload=event.payload,
        created_at=_NOW,
    )


def test_import_rejects_non_docx_body(monkeypatch: Any) -> None:
    monkeypatch.setattr(imports, "acquire", _fake_acquire)
    resp = client.post("/contracts/c1/import", content=b"not a docx")
    assert resp.status_code == 400


def test_import_returns_result(monkeypatch: Any) -> None:
    async def fake_import(_conn: Any, contract_id: str, _path: Any, **_kw: Any) -> ImportResult:
        return ImportResult(contract_id=contract_id, node_count=3, root_count=1, uncertain_count=1)

    monkeypatch.setattr(imports, "acquire", _fake_acquire)
    monkeypatch.setattr(imports, "import_docx", fake_import)

    resp = client.post("/contracts/c1/import", content=b"PK\x03\x04rest-of-docx")
    assert resp.status_code == 200
    assert resp.json() == {
        "contract_id": "c1",
        "node_count": 3,
        "root_count": 1,
        "uncertain_count": 1,
        "entity_candidates": None,
    }


def test_preview_rejects_non_docx_body() -> None:
    resp = client.post("/import/preview", content=b"not a docx")
    assert resp.status_code == 400


def test_preview_returns_candidate_tree(monkeypatch: Any) -> None:
    async def fake_preview(_path: Any) -> PreviewResponse:
        return PreviewResponse(
            nodes=[
                CandidateNode(
                    index=0,
                    parent_index=None,
                    order_index=100,
                    depth=0,
                    number="1",
                    content_type="prose",
                    heading="Definitions",
                    uncertain=False,
                ),
                CandidateNode(
                    index=1,
                    parent_index=0,
                    order_index=100,
                    depth=1,
                    number="1.1",
                    content_type="prose",
                    body="child",
                    uncertain=True,
                ),
            ],
            node_count=2,
            uncertain_count=1,
            tracked_changes=TrackedChangeReport(insertions=0, deletions=0, flattened=False),
        )

    monkeypatch.setattr(imports, "preview_docx", fake_preview)

    resp = client.post("/import/preview", content=b"PK\x03\x04rest-of-docx")
    assert resp.status_code == 200
    body = resp.json()
    assert body["node_count"] == 2
    assert body["uncertain_count"] == 1
    assert body["nodes"][1]["number"] == "1.1"
    assert body["tracked_changes"] == {"insertions": 0, "deletions": 0, "flattened": False}


def test_commit_persists_corrected_tree(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_insert(_conn: Any, contract_id: str, rows: list[NodeRow]) -> dict[int, str]:
        captured["contract_id"] = contract_id
        captured["rows"] = rows
        return {r.index: str(r.index) for r in rows}

    monkeypatch.setattr(imports, "acquire", _fake_acquire)
    monkeypatch.setattr(imports, "record_event", _noop_record)
    monkeypatch.setattr(imports, "insert_nodes", fake_insert)

    payload = {
        "nodes": [
            {"index": 0, "parent_index": None, "order_index": 100, "content_type": "prose"},
            {
                "index": 1,
                "parent_index": 0,
                "order_index": 100,
                "content_type": "prose",
                "uncertain": True,
            },
        ]
    }
    resp = client.post("/contracts/c1/commit", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {
        "contract_id": "c1",
        "node_count": 2,
        "root_count": 1,
        "uncertain_count": 1,
        "entity_candidates": None,
    }
    assert captured["contract_id"] == "c1"
    assert len(captured["rows"]) == 2


class _ExtractingFakeConn:
    """Commit-path fake that also supports F16 extraction (deal lookup, node fetch,
    term upsert) so a commit can auto-populate `defined_terms`. Upserts are captured."""

    def __init__(
        self, *, deal_id: str = "deal-1", nodes: list[dict[str, Any]] | None = None
    ) -> None:
        self._deal_id = deal_id
        self._nodes = nodes or []
        self.upserts: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, _sql: str, *_args: Any) -> Any:
        return self._deal_id

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        if "FROM nodes" in sql:
            return self._nodes
        return []

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any]:
        self.upserts.append(args)
        deal_id, term, definition, source_node_id = args
        return dict(
            id=f"dt-{term}",
            deal_id=deal_id,
            term=term,
            definition=definition,
            source_node_id=source_node_id,
        )


def _node_record(node_id: str, body: str) -> dict[str, Any]:
    return dict(
        id=node_id,
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body=body,
        table_data=None,
        plain_text=body,
        role="clause",
        has_placeholder=False,
    )


def test_commit_auto_extracts_defined_terms(monkeypatch: Any) -> None:
    conn = _ExtractingFakeConn(nodes=[_node_record("n1", '"Widget Rate" means the rate per unit.')])

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[_ExtractingFakeConn]:
        yield conn

    async def fake_insert(_conn: Any, _cid: str, rows: list[NodeRow]) -> dict[int, str]:
        return {r.index: str(r.index) for r in rows}

    monkeypatch.setattr(imports, "acquire", fake_acquire)
    monkeypatch.setattr(imports, "record_event", _noop_record)
    monkeypatch.setattr(imports, "insert_nodes", fake_insert)

    payload = {
        "nodes": [
            {"index": 0, "parent_index": None, "order_index": 100, "content_type": "prose"},
        ]
    }
    resp = client.post("/contracts/c1/commit", json=payload)

    assert resp.status_code == 200
    assert len(conn.upserts) == 1
    _deal_id, term, definition, source_node_id = conn.upserts[0]
    assert term == "Widget Rate"
    assert definition == "the rate per unit."
    assert source_node_id == "n1"


def test_commit_records_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_insert(_conn: Any, contract_id: str, rows: list[NodeRow]) -> dict[int, str]:
        return {r.index: str(r.index) for r in rows}

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return await _noop_record(_conn, event)

    monkeypatch.setattr(imports, "acquire", _fake_acquire)
    monkeypatch.setattr(imports, "record_event", capture_record)
    monkeypatch.setattr(imports, "insert_nodes", fake_insert)

    payload = {
        "nodes": [
            {"index": 0, "parent_index": None, "order_index": 100, "content_type": "prose"},
        ]
    }
    resp = client.post("/contracts/c1/commit", json=payload)
    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "committed"
    assert event.entity_type == "contract"
    assert event.entity_id == "c1"
    assert event.payload == {"node_count": 1}


def test_get_tree_returns_nested_tree(monkeypatch: Any) -> None:
    async def fake_fetch(_conn: Any, _contract_id: str) -> list[StoredNode]:
        return [
            StoredNode(
                id="a", parent_id=None, order_index=100, content_type="prose", heading="Definitions"
            ),
            StoredNode(id="a1", parent_id="a", order_index=100, content_type="prose", body="child"),
        ]

    monkeypatch.setattr(imports, "acquire", _fake_acquire)
    monkeypatch.setattr(imports, "fetch_nodes", fake_fetch)

    resp = client.get("/contracts/c1/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_id"] == "c1"
    assert body["nodes"][0]["id"] == "a"
    assert body["nodes"][0]["children"][0]["id"] == "a1"
