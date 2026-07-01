"""F16 defined-terms routes: request parsing, response shape, status codes.

main.py router registration is done centrally, serial after all agents land, so
these tests build a FRESH FastAPI app with only this router — the same fresh-app idiom
as tests/integration/test_cors_errors.py. The DB is faked: a connection that dispatches
by SQL (deal lookup, node fetch, term upsert, term list). Synthetic generic content
only (privacy rule). TestClient is used without its context manager so no app lifespan
or real pool is touched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.api import defined_terms as defined_terms_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(defined_terms_api.router)
client = TestClient(app)

_DEAL_ID = "deal-1"


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
        enumerator_format=None,
    )


class _FakeConn:
    """Dispatches asyncpg calls by SQL substring. `deal_id` None simulates an unknown
    contract; `nodes` feed extraction; `list_rows` feed the GET registry read."""

    def __init__(
        self,
        *,
        deal_id: str | None = _DEAL_ID,
        nodes: list[dict[str, Any]] | None = None,
        list_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._deal_id = deal_id
        self._nodes = nodes or []
        self._list_rows = list_rows or []
        self.upserts: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, _sql: str, *_args: Any) -> Any:
        return self._deal_id

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        if "FROM nodes" in sql:
            return self._nodes
        return self._list_rows

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any]:
        deal_id, term, definition, source_node_id = args
        self.upserts.append(args)
        return dict(
            id=f"dt-{term}",
            deal_id=deal_id,
            term=term,
            definition=definition,
            source_node_id=source_node_id,
        )


def _install(monkeypatch: Any, conn: _FakeConn) -> None:
    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    monkeypatch.setattr(defined_terms_api, "acquire", _fake_acquire)


def test_extract_returns_terms_found(monkeypatch: Any) -> None:
    conn = _FakeConn(
        nodes=[
            _node_record("n1", '"Widget Rate" means the rate per unit.'),
            _node_record("n2", 'The parties (the "Master Agreement") agree.'),
        ]
    )
    _install(monkeypatch, conn)

    resp = client.post("/contracts/c1/defined-terms/extract")

    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_id"] == "c1"
    assert body["deal_id"] == _DEAL_ID
    assert body["terms_found"] == 2
    terms = {t["term"]: t for t in body["terms"]}
    assert terms["Widget Rate"]["definition"] == "the rate per unit."
    assert terms["Widget Rate"]["source_node_id"] == "n1"
    assert terms["Master Agreement"]["definition"] is None


def test_extract_unknown_contract_returns_404(monkeypatch: Any) -> None:
    _install(monkeypatch, _FakeConn(deal_id=None))
    resp = client.post("/contracts/missing/defined-terms/extract")
    assert resp.status_code == 404


def test_extract_no_definitions_returns_empty(monkeypatch: Any) -> None:
    conn = _FakeConn(nodes=[_node_record("n1", 'The supplier delivers the "goods".')])
    _install(monkeypatch, conn)

    resp = client.post("/contracts/c1/defined-terms/extract")

    assert resp.status_code == 200
    assert resp.json()["terms_found"] == 0
    assert conn.upserts == []


def test_list_deal_defined_terms(monkeypatch: Any) -> None:
    rows = [
        dict(
            id="dt-1",
            deal_id=_DEAL_ID,
            term="Widget Rate",
            definition="the rate per unit.",
            source_node_id="n1",
        )
    ]
    _install(monkeypatch, _FakeConn(list_rows=rows))

    resp = client.get(f"/deals/{_DEAL_ID}/defined-terms")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == _DEAL_ID
    assert body["terms"][0]["term"] == "Widget Rate"


def test_list_contract_defined_terms_resolves_deal(monkeypatch: Any) -> None:
    rows = [
        dict(
            id="dt-1",
            deal_id=_DEAL_ID,
            term="Base Fee",
            definition=None,
            source_node_id=None,
        )
    ]
    _install(monkeypatch, _FakeConn(list_rows=rows))

    resp = client.get("/contracts/c1/defined-terms")

    assert resp.status_code == 200
    assert resp.json()["deal_id"] == _DEAL_ID
    assert resp.json()["terms"][0]["term"] == "Base Fee"


def test_list_contract_defined_terms_unknown_contract_404(monkeypatch: Any) -> None:
    _install(monkeypatch, _FakeConn(deal_id=None))
    resp = client.get("/contracts/missing/defined-terms")
    assert resp.status_code == 404
