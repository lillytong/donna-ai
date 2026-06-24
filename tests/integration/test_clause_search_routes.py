"""Clause-search route: request parsing, response shape, the hallucinated-id
guard, and the rate-limit -> 429 mapping.

The DB and the LLM are mocked (no live database, no real Claude call). TestClient
is used without its context manager so the app lifespan never runs (mirrors
tests/integration/test_issues_routes.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.api import clause_search as clause_search_api
from backend.models.imports import StoredNode
from backend.models.llm import CompletionResult, TokenUsage
from backend.services import clause_search as clause_search_svc
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(clause_search_api.router)
client = TestClient(app)


@asynccontextmanager
async def _fake_acquire() -> AsyncIterator[object]:
    yield object()


def _nodes() -> list[StoredNode]:
    return [
        StoredNode(
            id="h1",
            parent_id=None,
            order_index=0,
            content_type="prose",
            heading="Confidentiality",
            role="clause",
        ),
        StoredNode(
            id="b1",
            parent_id="h1",
            order_index=1,
            content_type="prose",
            body="Each party shall keep the other's information secret.",
            role="clause",
        ),
    ]


def _patch_load(monkeypatch: Any) -> None:
    async def fake_fetch(_conn: Any, _contract_id: str) -> list[StoredNode]:
        return _nodes()

    monkeypatch.setattr(clause_search_svc, "acquire", _fake_acquire)
    monkeypatch.setattr(clause_search_svc, "fetch_nodes", fake_fetch)


def test_returns_matched_node(monkeypatch: Any) -> None:
    _patch_load(monkeypatch)

    async def fake_complete(**_kwargs: Any) -> CompletionResult:
        return CompletionResult(text='{"node_id": "h1"}', usage=TokenUsage())

    monkeypatch.setattr(clause_search_svc, "complete", fake_complete)

    resp = client.post("/contracts/c1/clause-search", json={"query": "secrecy"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["node_id"] == "h1"
    assert body["matched_text"] == "Confidentiality"


def test_hallucinated_id_is_rejected(monkeypatch: Any) -> None:
    _patch_load(monkeypatch)

    async def fake_complete(**_kwargs: Any) -> CompletionResult:
        return CompletionResult(text='{"node_id": "not-in-contract"}', usage=TokenUsage())

    monkeypatch.setattr(clause_search_svc, "complete", fake_complete)

    resp = client.post("/contracts/c1/clause-search", json={"query": "anything"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["node_id"] is None
    assert body["matched_text"] is None


def test_no_match_returns_nulls(monkeypatch: Any) -> None:
    _patch_load(monkeypatch)

    async def fake_complete(**_kwargs: Any) -> CompletionResult:
        return CompletionResult(text='{"node_id": null}', usage=TokenUsage())

    monkeypatch.setattr(clause_search_svc, "complete", fake_complete)

    resp = client.post("/contracts/c1/clause-search", json={"query": "nothing relevant"})
    assert resp.status_code == 200
    assert resp.json() == {"node_id": None, "matched_text": None}


def test_rate_limit_maps_to_429(monkeypatch: Any) -> None:
    _patch_load(monkeypatch)

    async def fake_complete(**_kwargs: Any) -> CompletionResult:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(clause_search_svc, "complete", fake_complete)

    resp = client.post("/contracts/c1/clause-search", json={"query": "secrecy"})
    assert resp.status_code == 429


def test_rejects_missing_query() -> None:
    resp = client.post("/contracts/c1/clause-search", json={})
    assert resp.status_code == 422
