"""Mark-as-sent service (DD-71): cuts a snapshot of the working copy + advances the
right DD-48 pointer(s), and the DD-72 drift gate. DB faked: a conn that answers the
drift probe + the snapshot insert and records pointer upserts. record_event stubbed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.mark_sent import MarkSentRequest
from backend.services import mark_sent as mark_sent_svc
from backend.services import snapshot as snapshot_svc

_NOW = datetime(2026, 6, 24, tzinfo=UTC)


class _FakeConn:
    def __init__(self, *, drift: bool, snapshot_count: int) -> None:
        self._drift = drift
        self._snapshot_count = snapshot_count
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.snapshot_inserts = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, _sql: str, *_args: Any) -> list[Any]:
        return []  # _FETCH_TREE → empty tree is fine for the cut

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "next_version" in sql:  # the _DRIFT probe
            return {
                "last_export_at": None if self._drift else _NOW,
                # next minted v = COALESCE(MAX(version_number),0)+1; no gaps here, so = count+1
                "next_version": self._snapshot_count + 1,
                "drift": self._drift,
            }
        if "INSERT INTO contract_snapshots" in sql:
            self.snapshot_inserts += 1
            return {
                "id": "snapNEW",
                "contract_id": args[0],
                "label": args[1],
                "origin": args[3],
                "created_at": _NOW,
            }
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "UPDATE 1"

    def pointer_upserts(self) -> list[tuple[Any, ...]]:
        return [args for sql, args in self.executes if "snapshot_pointers" in sql]


@pytest.fixture(autouse=True)
def _stub_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    monkeypatch.setattr(snapshot_svc, "record_event", _noop)
    monkeypatch.setattr(mark_sent_svc, "record_event", _noop)


async def test_counterparty_cuts_snapshot_and_advances_pointer() -> None:
    conn = _FakeConn(drift=False, snapshot_count=0)

    result = await mark_sent_svc.mark_sent(conn, "c1", MarkSentRequest(recipient="counterparty"))

    assert result.marked is True
    assert result.drift is False
    assert result.snapshot_id == "snapNEW"
    assert result.version == 1  # first snapshot → v1 (DD-70)
    assert result.pointers == ["counterparty"]
    assert conn.snapshot_inserts == 1
    upserts = conn.pointer_upserts()
    assert len(upserts) == 1
    _cid, party, direction, snapshot_id = upserts[0]
    assert (party, direction, snapshot_id) == ("counterparty", "shared", "snapNEW")


async def test_legal_advances_legal_team_pointer() -> None:
    conn = _FakeConn(drift=False, snapshot_count=2)

    result = await mark_sent_svc.mark_sent(conn, "c1", MarkSentRequest(recipient="legal"))

    assert result.marked is True
    assert result.version == 3  # 2 prior snapshots → this is v3
    assert result.pointers == ["legal_team"]
    upserts = conn.pointer_upserts()
    assert len(upserts) == 1
    _cid, party, direction, _sid = upserts[0]
    assert (party, direction) == ("legal_team", "shared")


async def test_both_is_one_snapshot_two_pointers() -> None:
    conn = _FakeConn(drift=False, snapshot_count=1)

    result = await mark_sent_svc.mark_sent(conn, "c1", MarkSentRequest(recipient="both"))

    assert result.marked is True
    assert result.pointers == ["counterparty", "legal_team"]
    assert conn.snapshot_inserts == 1  # ONE snapshot
    upserts = conn.pointer_upserts()
    assert len(upserts) == 2  # TWO pointers
    parties = {args[1] for args in upserts}
    assert parties == {"counterparty", "legal_team"}
    # both pointers reference the same single snapshot
    assert {args[3] for args in upserts} == {"snapNEW"}


async def test_drift_returns_preview_without_cutting() -> None:
    """Edited since last export + not acknowledged → marked=False, nothing cut."""
    conn = _FakeConn(drift=True, snapshot_count=0)

    result = await mark_sent_svc.mark_sent(conn, "c1", MarkSentRequest(recipient="counterparty"))

    assert result.marked is False
    assert result.drift is True
    assert result.snapshot_id is None
    assert result.version == 1  # the version that WOULD be minted
    assert conn.snapshot_inserts == 0
    assert conn.pointer_upserts() == []


async def test_drift_acknowledged_marks() -> None:
    conn = _FakeConn(drift=True, snapshot_count=0)

    result = await mark_sent_svc.mark_sent(
        conn, "c1", MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
    )

    assert result.marked is True
    assert result.drift is True  # drift still reported, but the operator marked anyway
    assert conn.snapshot_inserts == 1
    assert len(conn.pointer_upserts()) == 1


async def test_no_drift_marks_silently() -> None:
    conn = _FakeConn(drift=False, snapshot_count=0)

    result = await mark_sent_svc.mark_sent(conn, "c1", MarkSentRequest(recipient="counterparty"))

    assert result.marked is True
    assert result.drift is False
    assert conn.snapshot_inserts == 1
