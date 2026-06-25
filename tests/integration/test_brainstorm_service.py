"""Brainstorm overlay service (F10b, DD-73/DD-77): the stateless turn persists NOTHING, the
on-close distillation stores a brainstorm_summaries row (or writes none on an empty/dismissed
transcript), the history read, and the delete_contract cascade. The LLM (`complete`) and the
repo read boundaries are mocked — no live database (mirrors tests/integration/test_pipeline.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.models.brainstorm import (
    BrainstormSummary,
    BrainstormTurnRequest,
    StoredBrainstormSummary,
)
from backend.models.donna import DonnaTurn
from backend.models.issues import StoredIssue
from backend.models.llm import CompletionResult, TokenUsage
from backend.services.donna import brainstorm
from backend.services.settings_repo import delete_contract

_NOW = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _issue(**kw: Any) -> StoredIssue:
    base: dict[str, Any] = dict(
        id="i1",
        contract_id="c1",
        node_id=None,
        title="Liability cap",
        status="open",
        initiator="operator",
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        created_at=_NOW,
    )
    base.update(kw)
    return StoredIssue(**base)


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage=TokenUsage())


class _SpyConn:
    """Records every execute/fetchrow/fetchval so a test can assert what (if anything) was
    written. Returns canned rows where a read is exercised."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.row: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []

    async def execute(self, sql: str, *_args: Any) -> str:
        self.calls.append(("execute", sql))
        return "DELETE 1"

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        self.calls.append(("fetchrow", sql))
        return self.row

    async def fetchval(self, sql: str, *_args: Any) -> Any:
        self.calls.append(("fetchval", sql))
        return None

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        self.calls.append(("fetch", sql))
        return self.rows


def _fake_acquire(conn: Any) -> Any:
    @asynccontextmanager
    async def _acquire() -> AsyncIterator[Any]:
        yield conn

    return _acquire


# --- brainstorm_turn: persists NOTHING --------------------------------------


async def test_brainstorm_turn_persists_nothing(monkeypatch: Any) -> None:
    conn = _SpyConn()
    monkeypatch.setattr(brainstorm, "acquire", _fake_acquire(conn))
    monkeypatch.setattr(brainstorm, "fetch_nodes", lambda _c, _cid: _async([]))
    monkeypatch.setattr(brainstorm, "list_issues", lambda _c, _cid: _async([_issue()]))
    monkeypatch.setattr(brainstorm, "get_issue", lambda _c, _iid: _async(_issue()))

    async def fake_complete(**_kw: Any) -> CompletionResult:
        return _completion(
            '{"reply": "Let us weigh a 12-month cap.", "mode": "advise", '
            '"citations": ["i1"], "draft_language": null}'
        )

    monkeypatch.setattr(brainstorm, "complete", fake_complete)

    out = await brainstorm.brainstorm_turn(
        "c1",
        BrainstormTurnRequest(
            issue_id="i1",
            turns=[DonnaTurn(question="start?", answer="the cap")],
            message="what about 12 months?",
        ),
    )

    assert out.reply.startswith("Let us weigh")
    assert out.citations == ["i1"]
    # The turn reads grounding through mocked repos; it issues NO write of its own and never
    # touches the conversation/message tables (DD-77: nothing persists until close).
    assert all(verb != "execute" for verb, _sql in conn.calls)


# --- close: distil + store, honest-empty writes no row ----------------------


async def test_close_brainstorm_stores_and_returns_summary(monkeypatch: Any) -> None:
    conn = _SpyConn()
    stored = StoredBrainstormSummary(
        id="bs1", issue_id="i1", question="q", conclusion="c", fallbacks="f", created_at=_NOW
    )
    seen: dict[str, Any] = {}
    monkeypatch.setattr(brainstorm, "acquire", _fake_acquire(conn))
    monkeypatch.setattr(brainstorm, "get_issue", lambda _c, _iid: _async(_issue()))

    async def fake_distill(_conn: Any, _iid: str, _turns: Any) -> BrainstormSummary:
        return BrainstormSummary(question="q", conclusion="c", fallbacks="f")

    async def fake_insert(
        _conn: Any, iid: str, summary: BrainstormSummary
    ) -> StoredBrainstormSummary:
        seen["iid"], seen["summary"] = iid, summary
        return stored

    monkeypatch.setattr(brainstorm, "distill_brainstorm_summary", fake_distill)
    monkeypatch.setattr(brainstorm, "insert_brainstorm_summary", fake_insert)

    out = await brainstorm.close_brainstorm("c1", "i1", [DonnaTurn(question="q", answer="a")])
    assert out is stored
    assert seen["iid"] == "i1"
    assert seen["summary"].conclusion == "c"


async def test_close_brainstorm_empty_distillation_writes_no_row(monkeypatch: Any) -> None:
    conn = _SpyConn()
    insert_called = False
    monkeypatch.setattr(brainstorm, "acquire", _fake_acquire(conn))
    monkeypatch.setattr(brainstorm, "get_issue", lambda _c, _iid: _async(_issue()))

    async def fake_distill(_conn: Any, _iid: str, _turns: Any) -> None:
        return None

    async def fake_insert(*_a: Any, **_k: Any) -> StoredBrainstormSummary:
        nonlocal insert_called
        insert_called = True
        raise AssertionError("must not insert on an empty distillation")

    monkeypatch.setattr(brainstorm, "distill_brainstorm_summary", fake_distill)
    monkeypatch.setattr(brainstorm, "insert_brainstorm_summary", fake_insert)

    out = await brainstorm.close_brainstorm("c1", "i1", [])
    assert out is None
    assert insert_called is False


async def test_close_brainstorm_rejects_foreign_issue(monkeypatch: Any) -> None:
    conn = _SpyConn()
    monkeypatch.setattr(brainstorm, "acquire", _fake_acquire(conn))
    monkeypatch.setattr(
        brainstorm, "get_issue", lambda _c, _iid: _async(_issue(contract_id="other"))
    )

    out = await brainstorm.close_brainstorm("c1", "i1", [DonnaTurn(question="q", answer="a")])
    assert out is None


async def test_distill_empty_transcript_skips_llm(monkeypatch: Any) -> None:
    called = False

    async def fake_complete(**_kw: Any) -> CompletionResult:
        nonlocal called
        called = True
        return _completion('{"summary": null}')

    monkeypatch.setattr(brainstorm, "complete", fake_complete)
    out = await brainstorm.distill_brainstorm_summary(_SpyConn(), "i1", [])
    assert out is None
    assert called is False  # no transcript -> no LLM spend


# --- repo helpers -----------------------------------------------------------


async def test_insert_brainstorm_summary_writes_and_returns_row() -> None:
    conn = _SpyConn()
    conn.row = {
        "id": "bs1",
        "issue_id": "i1",
        "question": "q",
        "conclusion": "c",
        "fallbacks": "f",
        "created_at": _NOW,
    }
    out = await brainstorm.insert_brainstorm_summary(
        conn, "i1", BrainstormSummary(question="q", conclusion="c", fallbacks="f")
    )
    assert out.id == "bs1"
    assert any("INSERT INTO brainstorm_summaries" in sql for verb, sql in conn.calls)


async def test_list_brainstorm_summaries_reads_history() -> None:
    conn = _SpyConn()
    conn.rows = [
        {
            "id": "bs2",
            "issue_id": "i1",
            "question": "q2",
            "conclusion": "c2",
            "fallbacks": "f2",
            "created_at": _NOW,
        }
    ]
    out = await brainstorm.list_brainstorm_summaries(conn, "i1")
    assert [s.id for s in out] == ["bs2"]
    assert any("FROM brainstorm_summaries" in sql and "ORDER BY" in sql for _v, sql in conn.calls)


# --- delete_contract cascade (DD-63/DD-77) ----------------------------------


class _CascadeConn:
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, sql: str, *_args: Any) -> str:
        self.sql.append(sql)
        return "DELETE 1"


async def test_delete_contract_cascades_brainstorm_summaries() -> None:
    conn = _CascadeConn()
    result = await delete_contract(conn, "c1")
    assert result is not None
    bs = [s for s in conn.sql if "DELETE FROM brainstorm_summaries" in s]
    assert len(bs) == 1
    # Scoped through the issues of THIS contract (FK issue_id).
    assert "WHERE issue_id IN" in bs[0] and "contract_id = $1" in bs[0]


def _async(value: Any) -> Any:
    async def _coro() -> Any:
        return value

    return _coro()
