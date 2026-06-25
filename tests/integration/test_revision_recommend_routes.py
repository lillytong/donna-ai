"""F03c per-change revision recommendation: the engine orchestration over a session with an
edited + a new + a deleted change (LLM + DB mocked), and the thin route's response shape +
not-found / rate-limit mappings. TestClient is used without its context manager so the app
lifespan never runs (mirrors test_donna_recommendations_routes.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.api import revision_recommend as recommend_api
from backend.models.llm import CompletionResult, TokenUsage
from backend.models.revision_recommend import RevisionRecommendSummary, VerdictTally
from backend.services.donna import revision_recommend as svc
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Engine orchestration (DB + LLM mocked)                                        #
# --------------------------------------------------------------------------- #


def _change(cid: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=cid,
        node_id=None,
        proposed_parent_id=None,
        proposed_order_index=None,
        match_confidence=None,
        status="pending",
    )
    base.update(kw)
    return base


def _hunk(hid: str, change_id: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=hid,
        change_id=change_id,
        hunk_type="replacement",
        original_text="our text",
        proposed_text="their text",
    )
    base.update(kw)
    return base


class _FakeConn:
    """Serves the session/changes/hunks reads and records advisory UPDATEs with their SQL."""

    def __init__(
        self,
        session_row: dict[str, Any] | None,
        change_rows: list[dict[str, Any]],
        hunk_rows: list[dict[str, Any]],
    ) -> None:
        self._session = session_row
        self._changes = change_rows
        self._hunks = hunk_rows
        self.in_txn = False
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.in_txn = True
        try:
            yield
        finally:
            self.in_txn = False

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._session

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "counterparty_revision_changes" in sql:
            return self._changes
        if "counterparty_revision_hunks" in sql:
            wanted = set(args[0])
            return [h for h in self._hunks if str(h["change_id"]) in wanted]
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        assert self.in_txn  # advisory writes happen inside the transaction
        self.executes.append((sql, args))
        return "UPDATE 1"


def _wire(monkeypatch: Any, conn: _FakeConn, responses: list[str]) -> list[str]:
    """Patch the engine's I/O seams: acquire yields `conn`, the contract/nodes/patterns
    lookups are stubbed, and `complete` pops canned JSON. Returns the caller log for assertions."""

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    queue = list(responses)
    callers: list[str] = []

    async def fake_complete(**kwargs: Any) -> CompletionResult:
        callers.append(kwargs["caller"])
        return CompletionResult(text=queue.pop(0), usage=TokenUsage())

    async def fake_nodes(_conn: Any, _cid: str) -> list[Any]:
        return []

    async def fake_patterns(_conn: Any, _cid: str) -> list[Any]:
        return []

    async def fake_contract(_conn: Any, _cid: str) -> None:
        return None

    monkeypatch.setattr(svc, "acquire", fake_acquire)
    monkeypatch.setattr(svc, "complete", fake_complete)
    monkeypatch.setattr(svc, "fetch_nodes", fake_nodes)
    monkeypatch.setattr(svc, "fetch_patterns_for_issue", fake_patterns)
    monkeypatch.setattr(svc, "get_contract", fake_contract)
    return callers


_COUNTER = (
    '{"verdict": "counter", "significance": "substantive",'
    ' "reasoning": "Uncapped is deal-breaking.",'
    ' "counter_language": "Liability shall not exceed the fees paid."}'
)
_ACCEPT = (
    '{"verdict": "accept", "significance": "substantive",'
    ' "reasoning": "A fair addition.", "counter_language": null}'
)
_KEEP = (
    '{"verdict": "keep", "significance": "substantive",'
    ' "reasoning": "We need this clause.", "counter_language": null}'
)


async def test_engine_analyzes_edited_new_deleted_and_skips_decided(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [
        _change("ch-edit", node_id="n1", match_confidence=0.9, status="pending"),
        _change("ch-new", proposed_order_index=2, proposed_parent_id="n0", status="pending"),
        _change("ch-del", node_id="n2", match_confidence=None, status="partial"),
        _change("ch-done", node_id="n3", match_confidence=0.9, status="complete"),
        _change("ch-abstain", proposed_parent_id="n4", match_confidence=0.2, status="pending"),
    ]
    hunks = [
        _hunk("h-edit", "ch-edit", original_text="capped", proposed_text="uncapped"),
        _hunk("h-new", "ch-new", hunk_type="insertion", original_text=None, proposed_text="new"),
        _hunk("h-del", "ch-del", hunk_type="deletion", original_text="old", proposed_text=None),
        _hunk("h-done", "ch-done"),  # decided change → must be skipped
        _hunk("h-abstain", "ch-abstain"),  # unresolved abstain → must be skipped
    ]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER, _ACCEPT, _KEEP])

    summary = await svc.recommend_session("s1")

    assert summary.changes_analyzed == 3  # edited + new + deleted; decided + abstain skipped
    assert summary.hunks_analyzed == 3
    assert summary.by_verdict == VerdictTally(accept=1, counter=1, keep=1)

    written = {args[0]: (sql, args) for sql, args in conn.executes}
    assert set(written) == {"h-edit", "h-new", "h-del"}  # only the analyzable hunks written
    assert "h-done" not in written and "h-abstain" not in written

    for sql, args in conn.executes:
        # advisory columns + significance ONLY — never the applied verdict/final_text (DD-64)
        assert "donna_verdict" in sql and "donna_counter_text" in sql and "significance" in sql
        assert "final_text" not in sql
        _hid, verdict, counter, significance = args
        # counter-language present IFF verdict == counter
        assert (counter is not None) == (verdict == "counter")
        assert significance in ("trivial", "substantive")

    # the counter hunk carries staged language; accept/keep carry none
    assert written["h-edit"][1][1] == "counter" and written["h-edit"][1][2] is not None
    assert written["h-new"][1][1] == "accept" and written["h-new"][1][2] is None
    assert written["h-del"][1][1] == "keep" and written["h-del"][1][2] is None


async def test_engine_raises_when_session_missing(monkeypatch: Any) -> None:
    conn = _FakeConn(None, [], [])
    _wire(monkeypatch, conn, [])
    try:
        await svc.recommend_session("missing")
        raise AssertionError("expected SessionNotFound")
    except svc.SessionNotFound:
        pass
    assert conn.executes == []  # nothing written


async def test_engine_no_pending_changes_writes_nothing(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch-done", node_id="n3", match_confidence=0.9, status="complete")]
    conn = _FakeConn(session, changes, [_hunk("h-done", "ch-done")])
    _wire(monkeypatch, conn, [])

    summary = await svc.recommend_session("s1")

    assert summary.changes_analyzed == 0 and summary.hunks_analyzed == 0
    assert conn.executes == []


# --------------------------------------------------------------------------- #
# Route (service mocked)                                                         #
# --------------------------------------------------------------------------- #

app = FastAPI()
app.include_router(recommend_api.router)
client = TestClient(app)
_PATH = "/revisions/sessions/s1/recommend"


def test_route_returns_summary(monkeypatch: Any) -> None:
    async def fake(session_id: str) -> RevisionRecommendSummary:
        assert session_id == "s1"
        return RevisionRecommendSummary(
            session_id="s1",
            changes_analyzed=2,
            hunks_analyzed=3,
            by_verdict=VerdictTally(accept=1, counter=1, keep=1),
        )

    monkeypatch.setattr(recommend_api, "recommend_session", fake)
    resp = client.post(_PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["changes_analyzed"] == 2
    assert body["by_verdict"] == {"accept": 1, "counter": 1, "keep": 1}


def test_route_maps_missing_session_to_404(monkeypatch: Any) -> None:
    async def fake(_session_id: str) -> RevisionRecommendSummary:
        raise svc.SessionNotFound("s1")

    monkeypatch.setattr(recommend_api, "recommend_session", fake)
    assert client.post(_PATH).status_code == 404


def test_route_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake(_session_id: str) -> RevisionRecommendSummary:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(recommend_api, "recommend_session", fake)
    assert client.post(_PATH).status_code == 429
