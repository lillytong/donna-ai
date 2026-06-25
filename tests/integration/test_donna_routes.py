"""Donna Q&A routes (F10): request parsing, response shape, deflection passthrough,
and the rate-limit -> 429 mapping. The service layer is mocked (no DB, no real Claude).
TestClient is used without its context manager so the app lifespan never runs (mirrors
tests/integration/test_clause_search_routes.py)."""

from __future__ import annotations

from typing import Any

from backend.api import donna as donna_api
from backend.models.donna import (
    DonnaAskResponse,
    DonnaClearResponse,
    DonnaMessage,
    DonnaThreadResponse,
)
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(donna_api.router)
client = TestClient(app)


def test_ask_returns_answer_with_citations(monkeypatch: Any) -> None:
    async def fake_ask(_cid: str, _q: str) -> DonnaAskResponse:
        return DonnaAskResponse(
            answer="The cap is in 11.2.", citations=["n-liab"], deflected=False, kind="answer"
        )

    monkeypatch.setattr(donna_api, "ask", fake_ask)
    resp = client.post("/contracts/c1/donna/ask", json={"question": "Where's the cap?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "answer": "The cap is in 11.2.",
        "citations": ["n-liab"],
        "deflected": False,
        "kind": "answer",
    }


def test_ask_passes_through_deflection(monkeypatch: Any) -> None:
    async def fake_ask(_cid: str, _q: str) -> DonnaAskResponse:
        return DonnaAskResponse(
            answer="That's a position call — raise it as an issue or ask a lawyer.",
            citations=[],
            deflected=True,
            kind="deflected",
        )

    monkeypatch.setattr(donna_api, "ask", fake_ask)
    resp = client.post("/contracts/c1/donna/ask", json={"question": "Should I accept clause 11?"})
    assert resp.status_code == 200
    assert resp.json()["deflected"] is True
    assert resp.json()["kind"] == "deflected"


def test_ask_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake_ask(_cid: str, _q: str) -> DonnaAskResponse:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(donna_api, "ask", fake_ask)
    resp = client.post("/contracts/c1/donna/ask", json={"question": "anything"})
    assert resp.status_code == 429


def test_ask_rejects_missing_question() -> None:
    resp = client.post("/contracts/c1/donna/ask", json={})
    assert resp.status_code == 422


def test_thread_returns_history(monkeypatch: Any) -> None:
    async def fake_thread(_cid: str) -> DonnaThreadResponse:
        return DonnaThreadResponse(
            conversation_id="conv1",
            running_summary="Earlier: discussed the cap.",
            messages=[
                DonnaMessage(role="user", content="Where's the cap?"),
                DonnaMessage(role="assistant", content="In 11.2."),
            ],
        )

    monkeypatch.setattr(donna_api, "get_thread", fake_thread)
    resp = client.get("/contracts/c1/donna/thread")
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == "conv1"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"


def test_clear_thread_returns_cleared(monkeypatch: Any) -> None:
    seen: dict[str, str] = {}

    async def fake_clear(cid: str) -> DonnaClearResponse:
        seen["cid"] = cid
        return DonnaClearResponse(cleared=True)

    monkeypatch.setattr(donna_api, "clear_thread", fake_clear)
    resp = client.delete("/contracts/c1/donna/thread")
    assert resp.status_code == 200
    assert resp.json() == {"cleared": True}
    assert seen["cid"] == "c1"
