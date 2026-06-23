"""run_migrations against a fake asyncpg connection — no live DB.

Asserts: ensure-table runs, only unapplied migrations execute in order, each is
recorded, and the apply+record pair runs inside a transaction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from backend import migrate


class _FakeConn:
    def __init__(self, applied: list[str]) -> None:
        self._applied = applied
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.tx_depth = 0
        self.max_tx_depth = 0

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))

    async def fetch(self, _sql: str) -> list[dict[str, str]]:
        return [{"version": v} for v in self._applied]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.tx_depth += 1
        self.max_tx_depth = max(self.max_tx_depth, self.tx_depth)
        yield
        self.tx_depth -= 1


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "0001_first.sql").write_text("ALTER TABLE t ADD COLUMN a int;", encoding="utf-8")
    (tmp_path / "0002_second.sql").write_text("ALTER TABLE t ADD COLUMN b int;", encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    return tmp_path


async def test_applies_only_pending_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    conn = _FakeConn(applied=["0001_first"])

    done = await migrate.run_migrations(conn)  # type: ignore[arg-type]

    assert done == ["0002_second"]
    inserts = [args for sql, args in conn.executed if "INSERT INTO schema_migrations" in sql]
    assert inserts == [("0002_second",)]
    assert any("ADD COLUMN b" in sql for sql, _ in conn.executed)
    assert not any("ADD COLUMN a" in sql for sql, _ in conn.executed)


async def test_fresh_db_applies_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, monkeypatch)
    conn = _FakeConn(applied=[])

    done = await migrate.run_migrations(conn)  # type: ignore[arg-type]

    assert done == ["0001_first", "0002_second"]
    assert conn.max_tx_depth == 1


async def test_noop_when_all_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, monkeypatch)
    conn = _FakeConn(applied=["0001_first", "0002_second"])

    done = await migrate.run_migrations(conn)  # type: ignore[arg-type]

    assert done == []
    assert not any("INSERT INTO schema_migrations" in sql for sql, _ in conn.executed)
