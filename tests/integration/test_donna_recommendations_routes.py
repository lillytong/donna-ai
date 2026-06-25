"""Donna recommendation routes (F11, DD-68): request parsing, response shape, the
not-found and rate-limit mappings. The service layer is mocked (no DB, no real Claude).
TestClient is used without its context manager so the app lifespan never runs (mirrors
tests/integration/test_donna_routes.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.api import donna_recommendations as rec_api
from backend.models.recommendations import (
    RecommendationConfirmResponse,
    StoredRecommendation,
)
from backend.services.donna.recommendations import IssueNotFound
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(rec_api.router)
client = TestClient(app)

_PATH = "/contracts/c1/issues/i1/recommendation"


def _stored(**kw: Any) -> StoredRecommendation:
    base: dict[str, Any] = dict(
        id="r1",
        issue_id="i1",
        rationale="Cap is favorable-but-fair.",
        draft_recommended_position="Keep the twelve-month cap.",
        draft_counter_language="Liability shall not exceed the fees paid.",
        citations=["n-liab"],
        model="claude-opus-4-8",
        generated_at=datetime(2026, 6, 25, tzinfo=UTC),
        confirmed=False,
    )
    base.update(kw)
    return StoredRecommendation(**base)


def test_generate_returns_draft(monkeypatch: Any) -> None:
    async def fake_generate(cid: str, iid: str) -> StoredRecommendation:
        assert (cid, iid) == ("c1", "i1")
        return _stored()

    monkeypatch.setattr(rec_api, "generate_recommendation", fake_generate)
    resp = client.post(_PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["issue_id"] == "i1"
    assert body["citations"] == ["n-liab"]
    assert body["confirmed"] is False


def test_generate_maps_unknown_issue_to_404(monkeypatch: Any) -> None:
    async def fake_generate(_cid: str, _iid: str) -> StoredRecommendation:
        raise IssueNotFound("i1")

    monkeypatch.setattr(rec_api, "generate_recommendation", fake_generate)
    assert client.post(_PATH).status_code == 404


def test_generate_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake_generate(_cid: str, _iid: str) -> StoredRecommendation:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(rec_api, "generate_recommendation", fake_generate)
    assert client.post(_PATH).status_code == 429


def test_get_returns_current_draft(monkeypatch: Any) -> None:
    async def fake_get(iid: str) -> StoredRecommendation | None:
        assert iid == "i1"
        return _stored(confirmed=True)

    monkeypatch.setattr(rec_api, "get_recommendation", fake_get)
    resp = client.get(_PATH)
    assert resp.status_code == 200
    assert resp.json()["confirmed"] is True


def test_get_404_when_no_draft(monkeypatch: Any) -> None:
    async def fake_get(_iid: str) -> StoredRecommendation | None:
        return None

    monkeypatch.setattr(rec_api, "get_recommendation", fake_get)
    assert client.get(_PATH).status_code == 404


def test_confirm_returns_copied_fields(monkeypatch: Any) -> None:
    seen: dict[str, str] = {}

    async def fake_confirm(iid: str) -> RecommendationConfirmResponse | None:
        seen["iid"] = iid
        return RecommendationConfirmResponse(
            issue_id=iid,
            confirmed=True,
            recommended_position="Keep the twelve-month cap.",
            donna_counter_language="Liability shall not exceed the fees paid.",
        )

    monkeypatch.setattr(rec_api, "confirm_recommendation", fake_confirm)
    resp = client.post(_PATH + "/confirm")
    assert resp.status_code == 200
    assert seen["iid"] == "i1"
    body = resp.json()
    assert body["confirmed"] is True
    assert body["recommended_position"] == "Keep the twelve-month cap."


def test_confirm_404_when_no_draft(monkeypatch: Any) -> None:
    async def fake_confirm(_iid: str) -> RecommendationConfirmResponse | None:
        return None

    monkeypatch.setattr(rec_api, "confirm_recommendation", fake_confirm)
    assert client.post(_PATH + "/confirm").status_code == 404
