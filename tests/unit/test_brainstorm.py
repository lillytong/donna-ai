"""Pure logic for the ephemeral brainstorm overlay (F10b, DD-73/DD-77): the close-distillation
parse (valid -> BrainstormSummary; honest-empty / unparseable -> None), and the delete_contract
cascade ordering (brainstorm_summaries cleared before the issues they reference). No LLM, no
live DB."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.services.donna.brainstorm import parse_summary
from backend.services.settings_repo import delete_contract

# --- parse_summary ----------------------------------------------------------


def test_parse_reads_summary_fields() -> None:
    text = (
        '{"summary": {"question": "Where should the cap land?", '
        '"conclusion": "Open at a 12-month cap.", '
        '"fallbacks": "Considered uncapped; passed over as unacceptable."}}'
    )
    summary = parse_summary(text)
    assert summary is not None
    assert summary.question == "Where should the cap land?"
    assert summary.conclusion == "Open at a 12-month cap."
    assert summary.fallbacks.startswith("Considered uncapped")


def test_parse_tolerates_surrounding_prose() -> None:
    text = (
        'Here you go:\n{"summary": {"question": "q", "conclusion": "c", "fallbacks": ""}}\n'
        "hope that helps"
    )
    summary = parse_summary(text)
    assert summary is not None
    assert summary.fallbacks == ""


def test_parse_honest_empty_summary_null_is_none() -> None:
    # Dismissed without substantive exploration -> the model returns summary: null.
    assert parse_summary('{"summary": null}') is None


def test_parse_blank_question_and_conclusion_is_none() -> None:
    # A summary with nothing in either substantive field is not worth storing.
    assert (
        parse_summary('{"summary": {"question": "  ", "conclusion": "", "fallbacks": ""}}') is None
    )


def test_parse_unparseable_is_none() -> None:
    assert parse_summary("not json at all") is None


# --- delete_contract cascade ordering (DD-63/DD-77) -------------------------


class _RecordingConn:
    """Records every execute() in order and returns a DELETE command tag so _exec_count
    parses a count (mirrors tests/unit/test_node_delete._FakeConn)."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, sql: str, *_args: Any) -> str:
        self.sql.append(sql)
        return "DELETE 1"


async def test_delete_contract_clears_brainstorm_summaries_before_issues() -> None:
    conn = _RecordingConn()
    result = await delete_contract(conn, "c1")

    assert result is not None  # the final "DELETE FROM contracts" returned a row
    bs_idx = next(i for i, s in enumerate(conn.sql) if "DELETE FROM brainstorm_summaries" in s)
    issues_idx = next(i for i, s in enumerate(conn.sql) if "DELETE FROM issues" in s)
    # FK ordering: brainstorm_summaries (FK issue_id) must clear before its issues.
    assert bs_idx < issues_idx


# --- brainstorm_turn firm-profile mandate grounding (F32 v1 / DD-90) ---------
# The global operator-authored firm profile is appended to the brainstorm prompt as the firm's
# standing MANDATE so Donna grounds every exploratory turn in the firm's identity + red-lines.
# Synthetic profile — NOT real firm/contract data (public repo).

_MANDATE_MARK = "FIRM PROFILE / MANDATE"
_PROFILE = "We are a licensing firm. Standing red-line: never accept uncapped liability."
_REPLY = '{"reply": "One angle: cap at fees paid.", "kind": "answer", "citations": []}'


def _run_brainstorm_turn(
    monkeypatch: Any, model_text: str, firm_profile: str = "", deal_brief: Any = None
) -> str:
    """Patch brainstorm_turn's I/O seams, capture the prompt sent to `complete`, return it."""
    import asyncio

    from backend.models.brainstorm import BrainstormTurnRequest
    from backend.services import deal_brief_repo
    from backend.services.donna import brainstorm as svc

    prompts: list[str] = []

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[Any]:
        yield object()

    async def fake_nodes(*_a: Any) -> list[Any]:
        return []

    async def fake_issues(*_a: Any) -> list[Any]:
        return []

    async def fake_get_issue(*_a: Any) -> None:
        return None

    async def fake_firm_profile(_conn: Any) -> str:
        return firm_profile

    async def fake_get_brief(_conn: Any, _cid: str) -> Any:
        return deal_brief

    class _Result:
        text = model_text

    async def fake_complete(**kwargs: Any) -> Any:
        prompts.append(kwargs["messages"][0]["content"])
        return _Result()

    monkeypatch.setattr(svc, "acquire", fake_acquire)
    monkeypatch.setattr(svc, "fetch_nodes", fake_nodes)
    monkeypatch.setattr(svc, "list_issues", fake_issues)
    monkeypatch.setattr(svc, "get_issue", fake_get_issue)
    monkeypatch.setattr(svc, "get_firm_profile", fake_firm_profile)
    monkeypatch.setattr(deal_brief_repo, "get_brief", fake_get_brief)
    monkeypatch.setattr(svc, "render", lambda *_a, **_k: "prompt")
    monkeypatch.setattr(svc, "complete", fake_complete)

    request = BrainstormTurnRequest(issue_id="i1", message="Where should the cap land?")
    asyncio.run(svc.brainstorm_turn("c1", request))
    return prompts[0]


def test_brainstorm_turn_injects_firm_profile_mandate(monkeypatch: Any) -> None:
    prompt = _run_brainstorm_turn(monkeypatch, _REPLY, firm_profile=_PROFILE)
    assert _MANDATE_MARK in prompt  # the labelled mandate block is present
    assert _PROFILE in prompt  # the operator's profile text reaches the model


def test_brainstorm_turn_empty_firm_profile_not_injected(monkeypatch: Any) -> None:
    prompt = _run_brainstorm_turn(monkeypatch, _REPLY, firm_profile="")
    assert _MANDATE_MARK not in prompt  # unset profile -> no-op


# --- brainstorm_turn per-deal deal-brief grounding (F37 / DD-95) -------------
# Donna's whole-deal brief is appended to the brainstorm prompt as the per-deal GLOBAL context
# alongside the firm mandate. Synthetic brief — NOT real firm/contract data (public repo).

_DEAL_BRIEF_MARK = "DEAL BRIEF"


def test_brainstorm_turn_injects_deal_brief(monkeypatch: Any) -> None:
    from backend.models.deal_brief import DealBrief

    brief = DealBrief(contract_id="c1", content="Parties: a licensor and a licensee.")
    prompt = _run_brainstorm_turn(monkeypatch, _REPLY, deal_brief=brief)
    assert _DEAL_BRIEF_MARK in prompt  # the labelled deal-brief block is present
    assert "a licensor and a licensee" in prompt  # the brief content reaches the model


def test_brainstorm_turn_no_deal_brief_not_injected(monkeypatch: Any) -> None:
    prompt = _run_brainstorm_turn(monkeypatch, _REPLY, deal_brief=None)
    assert _DEAL_BRIEF_MARK not in prompt  # no brief -> no-op
