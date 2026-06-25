"""Donna clause-drafting route (F08d): request parsing, response shape, the not-found and
rate-limit mappings. The service layer is mocked (no DB, no real Claude). TestClient is used
without its context manager so the app lifespan never runs (mirrors the F11 route tests)."""

from __future__ import annotations

from typing import Any

from backend.api import clause_draft as draft_api
from backend.models.clause_draft import ClauseDraft, ClauseDraftRequest
from backend.services.donna.drafting import ContractNotFound
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(draft_api.router)
client = TestClient(app)

_PATH = "/contracts/c1/nodes/draft"
_BODY = {"description": "Add a notice clause", "anchor_node_id": "n-term", "mode": "sub"}


def test_draft_returns_clause(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_draft(cid: str, req: ClauseDraftRequest) -> ClauseDraft:
        seen["cid"] = cid
        seen["req"] = req
        return ClauseDraft(
            heading="Notice", body="Any notice shall be in writing.", citations=["n-term"]
        )

    monkeypatch.setattr(draft_api, "draft_clause", fake_draft)
    resp = client.post(_PATH, json=_BODY)
    assert resp.status_code == 200
    # the request body reached the service intact
    assert seen["cid"] == "c1"
    assert seen["req"].description == "Add a notice clause"
    assert seen["req"].anchor_node_id == "n-term"
    assert seen["req"].mode == "sub"
    body = resp.json()
    assert body["heading"] == "Notice"
    assert body["body"] == "Any notice shall be in writing."
    assert body["citations"] == ["n-term"]


def test_draft_maps_unknown_contract_to_404(monkeypatch: Any) -> None:
    async def fake_draft(_cid: str, _req: ClauseDraftRequest) -> ClauseDraft:
        raise ContractNotFound("c1")

    monkeypatch.setattr(draft_api, "draft_clause", fake_draft)
    assert client.post(_PATH, json=_BODY).status_code == 404


def test_draft_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake_draft(_cid: str, _req: ClauseDraftRequest) -> ClauseDraft:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(draft_api, "draft_clause", fake_draft)
    assert client.post(_PATH, json=_BODY).status_code == 429
