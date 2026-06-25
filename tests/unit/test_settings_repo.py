"""Repo-level cascade logic for delete_contract — SQL order and count parsing.

No live DB: a fake connection records execute() calls and returns asyncpg-style
command-tag strings ("DELETE N"), so the FK deletion order and the count parsing
are exercised without Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.services import settings_repo


class _FakeConn:
    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return f"DELETE {self._counts[len(self.calls) - 1]}"


async def test_delete_contract_cascades_in_fk_order() -> None:
    # issues, footnotes, node_versions, nodes, contract (comments removed, DD-67)
    conn = _FakeConn([3, 2, 5, 12, 1])

    result = await settings_repo.delete_contract(conn, "contract-1")

    assert result is not None
    assert (result.issues, result.nodes) == (3, 12)
    tables = [sql.split("FROM")[1].split()[0] for sql, _ in conn.calls]
    assert tables == [
        "issues",
        "footnotes",
        "node_versions",
        "nodes",
        "contracts",
    ]
    assert all(args == ("contract-1",) for _, args in conn.calls)


async def test_delete_contract_missing_returns_none() -> None:
    conn = _FakeConn([0, 0, 0, 0, 0])  # no rows anywhere -> contract did not exist

    result = await settings_repo.delete_contract(conn, "missing")

    assert result is None
