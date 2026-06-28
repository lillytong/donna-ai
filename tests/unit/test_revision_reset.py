"""DD-86 "Start over" reset (no live DB): a FakeConn routes SQL by substring and
records executes, so the staging mutations are asserted without Postgres. Covers the
blanket verdict/final_text reset, the abstain re-stage (matcher-driven), Donna-column
and working-copy non-interference, and the 404/409 guards."""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from backend.models.revision_match import Abstention, RevisionMatchResult
from backend.models.snapshots import SnapshotNode, StoredSnapshot
from backend.services.import_ import revision_reset as svc

_SESSION = {
    "id": "s1",
    "contract_id": "c1",
    "baseline_snapshot_id": "snap-1",
    "source": "counterparty",
    "status": "reviewing",
}


def _snapshot(snapshot_id: str, nodes: list[SnapshotNode]) -> StoredSnapshot:
    return StoredSnapshot(
        id=snapshot_id,
        contract_id="c1",
        label=None,
        origin="as_received",
        created_at=datetime.datetime(2026, 6, 25),
        tree=nodes,
    )


def _node(**kw: Any) -> SnapshotNode:
    base: dict[str, Any] = dict(
        id="b3",
        parent_id=None,
        order_index=0,
        content_type="prose",
        heading=None,
        body="baseline body",
        is_deleted=False,
    )
    base.update(kw)
    return SnapshotNode(**base)


class FakeConn:
    def __init__(
        self,
        *,
        session: dict[str, Any] | None = None,
        change_links: list[dict[str, Any]] | None = None,
        received_id: str | None = "recv-1",
    ) -> None:
        self._session = session
        self._change_links = change_links or []
        self._received_id = received_id
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.inserted_hunks: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM counterparty_revision_sessions" in sql:
            return self._session
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "FROM snapshot_pointers" in sql:
            return self._received_id
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "received_node_id" in sql:
            return self._change_links
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        if "INSERT INTO counterparty_revision_hunks" in sql:
            self.inserted_hunks.append(args)
        return "OK"

    def find(self, needle: str) -> tuple[str, tuple[Any, ...]]:
        return next((sql, a) for sql, a in self.executes if needle in sql)

    def has(self, needle: str) -> bool:
        return any(needle in sql for sql, _ in self.executes)


class _Settings:
    operator_actor = "operator"


@pytest.fixture(autouse=True)
def _silence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    monkeypatch.setattr(svc, "record_event", _noop)
    monkeypatch.setattr(svc, "get_settings", lambda: _Settings())


def _patch_matcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    abstains: list[Abstention],
    baseline_nodes: list[SnapshotNode] | None = None,
    incoming_nodes: list[SnapshotNode] | None = None,
) -> None:
    baseline = _snapshot("snap-1", baseline_nodes or [_node(id="b3", body="baseline body")])
    received = _snapshot(
        "recv-1",
        incoming_nodes or [_node(id="5", parent_id=None, order_index=0, body="incoming body")],
    )

    async def fake_get_snapshot(_conn: Any, snapshot_id: str) -> StoredSnapshot | None:
        return baseline if snapshot_id == "snap-1" else received

    def fake_match(_baseline: Any, _incoming: Any) -> RevisionMatchResult:
        return RevisionMatchResult(matches=[], new=[], deleted=[], abstains=abstains)

    monkeypatch.setattr(svc, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(svc, "match_revision", fake_match)


# --- blanket reset ----------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_blanket_clears_verdicts_progress_and_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_abstains(_conn: Any, _session: Any) -> int:
        return 0

    monkeypatch.setattr(svc, "_restage_abstains", _no_abstains)
    conn = FakeConn(session=dict(_SESSION))

    await svc.reset_session(conn, "c1", "s1")

    hunk_sql, hunk_args = conn.find("UPDATE counterparty_revision_hunks")
    assert "verdict = 'pending'" in hunk_sql and "final_text = NULL" in hunk_sql
    assert hunk_args == ("s1",)
    _, prog_args = conn.find("UPDATE counterparty_revision_changes")
    assert prog_args == ("s1",)
    _, sess_args = conn.find("changes_reviewed_count = 0")
    assert sess_args == ("s1",)


@pytest.mark.asyncio
async def test_reset_preserves_donna_recommendations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_abstains(_conn: Any, _session: Any) -> int:
        return 0

    monkeypatch.setattr(svc, "_restage_abstains", _no_abstains)
    conn = FakeConn(session=dict(_SESSION))

    await svc.reset_session(conn, "c1", "s1")

    for sql, _ in conn.executes:
        assert "donna_verdict" not in sql
        assert "donna_counter_text" not in sql
        assert "donna_rationale" not in sql


@pytest.mark.asyncio
async def test_reset_never_touches_live_working_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_matcher(
        monkeypatch,
        abstains=[Abstention(incoming_index=5, best_baseline_id="b3", confidence=0.4)],
    )
    conn = FakeConn(session=dict(_SESSION), change_links=[{"id": "ch5", "received_node_id": "5"}])

    await svc.reset_session(conn, "c1", "s1")

    for sql, _ in conn.executes:
        assert "UPDATE nodes" not in sql
        assert "INSERT INTO nodes" not in sql
        assert "DELETE FROM nodes" not in sql
        # Every mutation targets a staging table only.
        assert "counterparty_revision_" in sql


# --- abstain re-stage (the Phase-1 match-confirm reset) ---------------------


@pytest.mark.asyncio
async def test_restage_restores_abstain_shape_and_hunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_matcher(
        monkeypatch,
        abstains=[Abstention(incoming_index=5, best_baseline_id="b3", confidence=0.4)],
    )
    conn = FakeConn(change_links=[{"id": "ch5", "received_node_id": "5"}])

    restaged = await svc._restage_abstains(
        conn,
        svc._ResetSession(
            id="s1",
            contract_id="c1",
            baseline_snapshot_id="snap-1",
            source="counterparty",
            status="reviewing",
        ),
    )

    assert restaged == 1
    sql, args = conn.find("SET node_id = NULL")
    # (change_id, candidate, confidence, hunk_count) — node_id NULL + confidence set =
    # back in the derived abstain bucket.
    assert args[0] == "ch5" and args[1] == "b3" and args[2] == 0.4
    assert "match_confidence = $3" in sql and "proposed_order_index = NULL" in sql
    assert conn.has("DELETE FROM counterparty_revision_hunks")
    assert len(conn.inserted_hunks) >= 1


@pytest.mark.asyncio
async def test_restage_noop_when_no_received_pointer() -> None:
    conn = FakeConn(received_id=None)
    restaged = await svc._restage_abstains(
        conn,
        svc._ResetSession(
            id="s1",
            contract_id="c1",
            baseline_snapshot_id="snap-1",
            source="counterparty",
            status="reviewing",
        ),
    )
    assert restaged == 0 and not conn.executes


@pytest.mark.asyncio
async def test_reset_after_confirm_returns_abstain_to_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A confirmed abstain (node_id was set) whose received link survives — reset must
    # both blanket-clear verdicts AND re-stage the row back to abstain shape.
    _patch_matcher(
        monkeypatch,
        abstains=[Abstention(incoming_index=5, best_baseline_id="b3", confidence=0.4)],
    )
    conn = FakeConn(session=dict(_SESSION), change_links=[{"id": "ch5", "received_node_id": "5"}])

    await svc.reset_session(conn, "c1", "s1")

    _, reclass_args = conn.find("SET node_id = NULL")
    assert reclass_args[0] == "ch5"
    hunk_sql, _ = conn.find("UPDATE counterparty_revision_hunks\nSET verdict")
    assert "verdict = 'pending'" in hunk_sql


# --- guards -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_404_when_session_missing() -> None:
    conn = FakeConn(session=None)
    with pytest.raises(svc.SessionNotFound):
        await svc.reset_session(conn, "c1", "s1")


@pytest.mark.asyncio
async def test_reset_404_when_wrong_contract() -> None:
    conn = FakeConn(session=dict(_SESSION))
    with pytest.raises(svc.SessionNotFound):
        await svc.reset_session(conn, "other-contract", "s1")


@pytest.mark.asyncio
async def test_reset_409_when_already_applied() -> None:
    conn = FakeConn(session={**_SESSION, "status": "completed"})
    with pytest.raises(svc.SessionAlreadyApplied):
        await svc.reset_session(conn, "c1", "s1")
