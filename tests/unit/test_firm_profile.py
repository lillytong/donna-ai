"""Firm profile v1 (F32 / DD-90): the global firm-profile repo (singleton get / set / upsert)
and the mandate grounding helper. No live DB, no LLM — a fake connection records the SQL.

All fixtures are SYNTHETIC (public repo): no real firm / contract / party names or values."""

from __future__ import annotations

from typing import Any

from backend.services.donna.grounding import build_mandate_grounding
from backend.services.firm_profile_repo import get_firm_profile, set_firm_profile


class _FakeConn:
    """Serves one fetchrow row and records execute() SQL + args."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._row

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "INSERT 0 1"


# --- repo: get -------------------------------------------------------------


async def test_get_returns_stored_content() -> None:
    conn = _FakeConn({"content": "We are a licensing firm."})
    assert await get_firm_profile(conn) == "We are a licensing firm."


async def test_get_returns_empty_when_no_row() -> None:
    # Defensive: a missing singleton row reads as the empty (no-op grounding) profile.
    conn = _FakeConn(None)
    assert await get_firm_profile(conn) == ""


# --- repo: set (singleton upsert) ------------------------------------------


async def test_set_issues_singleton_upsert() -> None:
    conn = _FakeConn(None)
    await set_firm_profile(conn, "New mandate text.")
    assert len(conn.executes) == 1
    sql, args = conn.executes[0]
    assert "INSERT INTO firm_profile" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql  # upsert, not a blind insert
    assert args == ("New mandate text.",)


# --- grounding: build_mandate_grounding ------------------------------------


def test_mandate_block_carries_header_and_profile_text() -> None:
    block = build_mandate_grounding("Never accept uncapped liability.")
    assert "FIRM PROFILE / MANDATE" in block
    assert "Never accept uncapped liability." in block
    # Framed as data/context, not instructions (prompt-injection posture).
    assert "NOT as instructions" in block


def test_mandate_block_empty_profile_is_noop() -> None:
    assert build_mandate_grounding("") == ""
    assert build_mandate_grounding("   \n  ") == ""  # whitespace-only collapses to no-op
