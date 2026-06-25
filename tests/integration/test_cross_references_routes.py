"""F17 cross-reference routes: request parsing, response shape, status codes.

main.py router registration is done centrally, so these tests build a FRESH FastAPI
app with only this router — the same fresh-app idiom as test_defined_terms_routes.py.
The DB is faked: a connection that dispatches by SQL (contract lookup, node fetch,
delete, ref insert, ref list) and accumulates inserted rows so a POST .../extract
followed by a GET reads the same rows back. Synthetic generic content only (privacy
rule). TestClient is used without its context manager so no app lifespan / real pool
is touched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.api import cross_references as cross_references_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(cross_references_api.router)
client = TestClient(app)

_CONTRACT_ID = "c1"


def _node_record(
    node_id: str, body: str, *, order_index: int, parent_id: str | None = None
) -> dict[str, Any]:
    return dict(
        id=node_id,
        parent_id=parent_id,
        order_index=order_index,
        content_type="prose",
        heading=None,
        body=body,
        table_data=None,
        plain_text=body,
        role="clause",
        has_placeholder=False,
    )


class _FakeConn:
    """Dispatches asyncpg calls by SQL substring. `deal_id` None simulates an unknown
    contract; `nodes` feed extraction. Inserted rows accumulate in `stored` and are
    served back on the list read, so POST-then-GET round-trips through one fake DB."""

    def __init__(
        self,
        *,
        deal_id: str | None = _CONTRACT_ID,
        nodes: list[dict[str, Any]] | None = None,
    ) -> None:
        self._deal_id = deal_id
        self._nodes = nodes or []
        self.stored: list[dict[str, Any]] = []
        self.deletes = 0
        self._counter = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, _sql: str, *_args: Any) -> Any:
        return self._deal_id

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        if "FROM nodes" in sql:
            return self._nodes
        return sorted(self.stored, key=lambda r: r["source_node_id"])

    async def execute(self, _sql: str, *_args: Any) -> None:
        self.deletes += 1
        self.stored.clear()

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any]:
        source_node_id, source_contract_id, target_node_id, target_contract_id = args
        self._counter += 1
        row = dict(
            id=self._counter,
            source_node_id=source_node_id,
            source_contract_id=source_contract_id,
            target_node_id=target_node_id,
            target_contract_id=target_contract_id,
        )
        self.stored.append(row)
        return row


def _install(monkeypatch: Any, conn: _FakeConn) -> None:
    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    monkeypatch.setattr(cross_references_api, "acquire", _fake_acquire)


def _nodes_with_one_resolved_and_unresolved() -> list[dict[str, Any]]:
    # n1 (clause 1) -> "clause 2" resolves to n2; n3 (clause 3) -> "clause 99" + a
    # "Schedule I" both stay unresolved (no decimal clause number).
    return [
        _node_record("n1", "the rate applies as set out in clause 2.", order_index=10),
        _node_record("n2", "Payment terms apply.", order_index=20),
        _node_record("n3", "see clause 99 and Schedule I for details.", order_index=30),
    ]


def test_extract_finds_and_resolves_links(monkeypatch: Any) -> None:
    conn = _FakeConn(nodes=_nodes_with_one_resolved_and_unresolved())
    _install(monkeypatch, conn)

    resp = client.post(f"/contracts/{_CONTRACT_ID}/cross-references/extract")

    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_id"] == _CONTRACT_ID
    assert body["references_found"] == 3  # clause 2, clause 99, schedule I
    refs = body["cross_references"]

    resolved = next(r for r in refs if r["source_node_id"] == "n1")
    assert resolved["target_node_id"] == "n2"
    assert resolved["target_contract_id"] == _CONTRACT_ID
    assert resolved["resolved"] is True
    assert resolved["label"] == "clause 2"

    n3_refs = [r for r in refs if r["source_node_id"] == "n3"]
    assert len(n3_refs) == 2
    assert all(r["target_node_id"] is None for r in n3_refs)
    assert all(r["resolved"] is False for r in n3_refs)


def test_extract_then_list_round_trips_stored_rows(monkeypatch: Any) -> None:
    conn = _FakeConn(nodes=_nodes_with_one_resolved_and_unresolved())
    _install(monkeypatch, conn)

    client.post(f"/contracts/{_CONTRACT_ID}/cross-references/extract")
    resp = client.get(f"/contracts/{_CONTRACT_ID}/cross-references")

    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_id"] == _CONTRACT_ID
    rows = body["cross_references"]
    assert len(rows) == 3
    resolved = [r for r in rows if r["target_node_id"] is not None]
    unresolved = [r for r in rows if r["target_node_id"] is None]
    assert len(resolved) == 1
    assert resolved[0]["source_node_id"] == "n1"
    assert resolved[0]["target_node_id"] == "n2"
    assert resolved[0]["resolved"] is True
    assert len(unresolved) == 2
    assert all(r["resolved"] is False for r in unresolved)


def test_extract_is_idempotent_across_reruns(monkeypatch: Any) -> None:
    conn = _FakeConn(nodes=_nodes_with_one_resolved_and_unresolved())
    _install(monkeypatch, conn)

    client.post(f"/contracts/{_CONTRACT_ID}/cross-references/extract")
    client.post(f"/contracts/{_CONTRACT_ID}/cross-references/extract")
    resp = client.get(f"/contracts/{_CONTRACT_ID}/cross-references")

    # Two extracts each cleared then re-inserted; the stored set never duplicated.
    assert conn.deletes == 2
    assert len(resp.json()["cross_references"]) == 3


def test_extract_unknown_contract_returns_404(monkeypatch: Any) -> None:
    _install(monkeypatch, _FakeConn(deal_id=None))
    resp = client.post("/contracts/missing/cross-references/extract")
    assert resp.status_code == 404


def test_list_empty_contract_returns_empty(monkeypatch: Any) -> None:
    _install(monkeypatch, _FakeConn(nodes=[]))
    resp = client.get(f"/contracts/{_CONTRACT_ID}/cross-references")
    assert resp.status_code == 200
    assert resp.json()["cross_references"] == []
