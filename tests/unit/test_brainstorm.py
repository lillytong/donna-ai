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
