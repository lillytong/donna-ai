"""Donna Q&A routes (F10): request parsing, response shape, deflection passthrough,
and the rate-limit -> 429 mapping. The service layer is mocked (no DB, no real Claude).
TestClient is used without its context manager so the app lifespan never runs (mirrors
tests/integration/test_clause_search_routes.py)."""

from __future__ import annotations

from typing import Any

from backend.api import donna as donna_api
from backend.models.donna import (
    DonnaChatResponse,
    DonnaClearResponse,
    DonnaContext,
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
    # F10b envelope: no-context read-and-explain comes back as mode "explain" (F10 preserved).
    async def fake_chat(_cid: str, _q: str, _ctx: DonnaContext | None) -> DonnaChatResponse:
        return DonnaChatResponse(
            reply="The cap is in 11.2.", mode="explain", citations=["n-liab"], draft_language=None
        )

    monkeypatch.setattr(donna_api, "chat", fake_chat)
    resp = client.post("/contracts/c1/donna/ask", json={"question": "Where's the cap?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "reply": "The cap is in 11.2.",
        "mode": "explain",
        "citations": ["n-liab"],
        "draft_language": None,
    }


def test_ask_softer_deflection_is_need_context(monkeypatch: Any) -> None:
    # No-context advice request -> the softer acquire-context deflection, not the old wall.
    async def fake_chat(_cid: str, _q: str, _ctx: DonnaContext | None) -> DonnaChatResponse:
        return DonnaChatResponse(
            reply="Tell me which clause you mean — select it.",
            mode="need_context",
            citations=[],
            draft_language=None,
        )

    monkeypatch.setattr(donna_api, "chat", fake_chat)
    resp = client.post("/contracts/c1/donna/ask", json={"question": "Should I accept clause 11?"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "need_context"


def test_ask_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake_chat(_cid: str, _q: str, _ctx: DonnaContext | None) -> DonnaChatResponse:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(donna_api, "chat", fake_chat)
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


def test_thread_propagates_kind_and_citations(monkeypatch: Any) -> None:
    async def fake_thread(_cid: str) -> DonnaThreadResponse:
        return DonnaThreadResponse(
            conversation_id="conv1",
            running_summary=None,
            messages=[
                DonnaMessage(role="user", content="Where's the cap?"),
                DonnaMessage(
                    role="assistant", content="In 11.2.", kind="answer", citations=["n-liab"]
                ),
            ],
        )

    monkeypatch.setattr(donna_api, "get_thread", fake_thread)
    resp = client.get("/contracts/c1/donna/thread")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    # User turn carries null meta; assistant turn rehydrates kind + cited ids.
    assert msgs[0]["kind"] is None and msgs[0]["citations"] is None
    assert msgs[1]["kind"] == "answer"
    assert msgs[1]["citations"] == ["n-liab"]


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
