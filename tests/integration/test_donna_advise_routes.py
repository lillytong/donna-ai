"""Donna context-aware chat route (F10b): request parsing (with + without context),
response shape per mode, and the rate-limit -> 429 mapping. The service layer (`chat`)
is mocked (no DB, no real Claude). TestClient is used without its context manager so the
app lifespan never runs (mirrors tests/integration/test_donna_routes.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import donna as donna_api
from backend.models.brainstorm import BrainstormTurnResponse, StoredBrainstormSummary
from backend.models.donna import DonnaChatResponse, DonnaContext
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(donna_api.router)
client = TestClient(app)

_PATH = "/contracts/c1/donna/ask"
_NOW = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


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


# --- brainstorm overlay (DD-73/DD-77: stateless turn, distil-on-close, history) ---

_BRAINSTORM_PATH = "/contracts/c1/donna/brainstorm"
_CLOSE_PATH = "/contracts/c1/donna/brainstorm/close"


def test_brainstorm_turn_returns_reply(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_turn(cid: str, payload: Any) -> BrainstormTurnResponse:
        seen["cid"], seen["payload"] = cid, payload
        return BrainstormTurnResponse(reply="Let's weigh a 12-month cap.", citations=["n-liab"])

    monkeypatch.setattr(donna_api, "brainstorm_turn", fake_turn)
    resp = client.post(
        _BRAINSTORM_PATH,
        json={
            "issue_id": "i1",
            "turns": [{"question": "where do we start?", "answer": "with the cap"}],
            "message": "what about a 12-month cap?",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"].startswith("Let's weigh")
    assert body["citations"] == ["n-liab"]
    # The running transcript + new message are parsed into the request and passed through.
    assert seen["cid"] == "c1"
    assert seen["payload"].issue_id == "i1"
    assert seen["payload"].message == "what about a 12-month cap?"
    assert len(seen["payload"].turns) == 1


def test_brainstorm_turn_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake_turn(_cid: str, _payload: Any) -> BrainstormTurnResponse:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(donna_api, "brainstorm_turn", fake_turn)
    resp = client.post(_BRAINSTORM_PATH, json={"issue_id": "i1", "message": "hi"})
    assert resp.status_code == 429


def test_brainstorm_turn_rejects_missing_message() -> None:
    assert client.post(_BRAINSTORM_PATH, json={"issue_id": "i1"}).status_code == 422


def test_brainstorm_close_stores_and_returns_summary(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def fake_close(cid: str, iid: str, turns: Any) -> StoredBrainstormSummary:
        seen["cid"], seen["iid"], seen["turns"] = cid, iid, turns
        return StoredBrainstormSummary(
            id="bs1",
            issue_id=iid,
            question="Where should the liability cap land?",
            conclusion="Open at a 12-month cap.",
            fallbacks="Considered uncapped; passed over as unacceptable.",
            created_at=_NOW,
        )

    monkeypatch.setattr(donna_api, "close_brainstorm", fake_close)
    resp = client.post(
        _CLOSE_PATH,
        json={"issue_id": "i1", "turns": [{"question": "q", "answer": "a"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "bs1"
    assert body["conclusion"] == "Open at a 12-month cap."
    assert (seen["cid"], seen["iid"]) == ("c1", "i1")
    assert len(seen["turns"]) == 1


def test_brainstorm_close_empty_distillation_is_204(monkeypatch: Any) -> None:
    async def fake_close(_cid: str, _iid: str, _turns: Any) -> StoredBrainstormSummary | None:
        return None

    monkeypatch.setattr(donna_api, "close_brainstorm", fake_close)
    resp = client.post(_CLOSE_PATH, json={"issue_id": "i1", "turns": []})
    assert resp.status_code == 204
    assert resp.content == b""


def test_brainstorm_summaries_returns_history(monkeypatch: Any) -> None:
    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[object]:
        yield object()

    async def fake_list(_conn: Any, issue_id: str) -> list[StoredBrainstormSummary]:
        return [
            StoredBrainstormSummary(
                id="bs2",
                issue_id=issue_id,
                question="q2",
                conclusion="c2",
                fallbacks="f2",
                created_at=_NOW,
            )
        ]

    monkeypatch.setattr(donna_api, "acquire", fake_acquire)
    monkeypatch.setattr(donna_api, "list_brainstorm_summaries", fake_list)
    resp = client.get("/issues/i1/brainstorm-summaries")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["summaries"]) == 1
    assert body["summaries"][0]["id"] == "bs2"
