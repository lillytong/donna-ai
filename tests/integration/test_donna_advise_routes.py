"""Donna context-aware chat route (F10b): request parsing (with + without context),
response shape per mode, and the rate-limit -> 429 mapping. The service layer (`chat`)
is mocked (no DB, no real Claude). TestClient is used without its context manager so the
app lifespan never runs (mirrors tests/integration/test_donna_routes.py)."""

from __future__ import annotations

from typing import Any

from backend.api import donna as donna_api
from backend.models.donna import DonnaChatResponse, DonnaContext, DonnaMessage
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(donna_api.router)
client = TestClient(app)

_PATH = "/contracts/c1/donna/ask"


def test_ask_without_context_returns_explain(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_chat(cid: str, q: str, ctx: DonnaContext | None) -> DonnaChatResponse:
        seen["cid"], seen["q"], seen["ctx"] = cid, q, ctx
        return DonnaChatResponse(
            reply="The cap is in 11.2.", mode="explain", citations=["n-liab"], draft_language=None
        )

    monkeypatch.setattr(donna_api, "chat", fake_chat)
    resp = client.post(_PATH, json={"question": "Where's the cap?"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "explain"
    # No context block on the request -> the service receives None (no-context path).
    assert seen["ctx"] is None
    assert seen["cid"] == "c1"


def test_ask_with_context_returns_advise(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_chat(_cid: str, _q: str, ctx: DonnaContext | None) -> DonnaChatResponse:
        seen["ctx"] = ctx
        return DonnaChatResponse(
            reply="Counter at the 12-month cap — favorable-but-fair.",
            mode="advise",
            citations=["n-liab", "i-liab"],
            draft_language=None,
        )

    monkeypatch.setattr(donna_api, "chat", fake_chat)
    resp = client.post(
        _PATH,
        json={
            "question": "Should we accept their uncapped liability?",
            "context": {"node_ids": ["n-liab"], "issue_id": "i-liab"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "advise"
    assert body["citations"] == ["n-liab", "i-liab"]
    # The pointer is parsed into the context model and passed through to the service.
    assert seen["ctx"] is not None
    assert seen["ctx"].node_ids == ["n-liab"]
    assert seen["ctx"].issue_id == "i-liab"


def test_ask_with_context_returns_draft_language(monkeypatch: Any) -> None:
    async def fake_chat(_cid: str, _q: str, _ctx: DonnaContext | None) -> DonnaChatResponse:
        return DonnaChatResponse(
            reply="Here is a tighter version.",
            mode="draft",
            citations=["n-conf"],
            draft_language="Each party shall keep the other's Confidential Information secret.",
        )

    monkeypatch.setattr(donna_api, "chat", fake_chat)
    resp = client.post(
        _PATH,
        json={"question": "Tighten this clause", "context": {"node_ids": ["n-conf"]}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "draft"
    assert body["draft_language"].startswith("Each party shall keep")


def test_ask_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake_chat(_cid: str, _q: str, _ctx: DonnaContext | None) -> DonnaChatResponse:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(donna_api, "chat", fake_chat)
    resp = client.post(_PATH, json={"question": "anything", "context": {"node_ids": ["n1"]}})
    assert resp.status_code == 429


def test_ask_rejects_missing_question() -> None:
    assert client.post(_PATH, json={}).status_code == 422


# --- seed-brainstorm (the server-composed primed opening turn) -------------

_SEED_PATH = "/contracts/c1/donna/seed-brainstorm"


def test_seed_brainstorm_returns_stored_message(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_seed(cid: str, iid: str) -> DonnaMessage | None:
        seen["cid"], seen["iid"] = cid, iid
        return DonnaMessage(
            role="assistant",
            content="Let's brainstorm issue #3. Here's where I've landed…",
            kind="answer",
            citations=["n-liab"],
        )

    monkeypatch.setattr(donna_api, "seed_brainstorm", fake_seed)
    resp = client.post(_SEED_PATH, json={"issue_id": "i1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["seeded"] is True
    assert body["message"]["role"] == "assistant"
    assert body["message"]["kind"] == "answer"
    assert body["message"]["citations"] == ["n-liab"]
    assert (seen["cid"], seen["iid"]) == ("c1", "i1")


def test_seed_brainstorm_no_draft_is_noop(monkeypatch: Any) -> None:
    async def fake_seed(_cid: str, _iid: str) -> DonnaMessage | None:
        return None

    monkeypatch.setattr(donna_api, "seed_brainstorm", fake_seed)
    resp = client.post(_SEED_PATH, json={"issue_id": "i1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["seeded"] is False
    assert body["message"] is None


def test_seed_brainstorm_rejects_missing_issue_id() -> None:
    assert client.post(_SEED_PATH, json={}).status_code == 422
