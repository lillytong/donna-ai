"""F03c decision-state transitions (no live DB): the hunk verdict→stored mapping,
the abstain match-confirm reclassifications (confirm / new / rematch), and the
whole-node decision mapping. A fake connection routes SQL by substring and records
executes so the staged mutations are asserted without Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from backend.models.revision_review import (
    ConfirmMatchRequest,
    HunkDecideRequest,
    NodeDecideRequest,
)
from backend.services.import_ import revision_review as svc

_SESSION = {
    "id": "s1",
    "contract_id": "c1",
    "baseline_snapshot_id": "snap-1",
    "source": "counterparty",
    "source_filename": "v4.docx",
    "parse_path": "clean_diff",
    "status": "reviewing",
    "changes_count": 3,
    "changes_reviewed_count": 0,
    "imported_at": __import__("datetime").datetime(2026, 6, 25),
}


def _hunk(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="h1",
        change_id="ch1",
        hunk_type="replacement",
        significance="substantive",
        position_in_body=0,
        original_text="old",
        proposed_text="new",
        donna_verdict=None,
        donna_counter_text=None,
        verdict="pending",
        final_text=None,
    )
    base.update(kw)
    return base


def _change(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="ch1",
        session_id="s1",
        node_id=None,
        proposed_parent_id=None,
        proposed_order_index=None,
        match_confidence=0.5,
        hunk_count=1,
        hunks_decided=0,
        status="pending",
    )
    base.update(kw)
    return base


def _node(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="b3", parent_id="p1", order_index=300, body="abstain baseline body", heading=None
    )
    base.update(kw)
    return base


class FakeConn:
    def __init__(
        self,
        *,
        changes: list[dict[str, Any]] | None = None,
        hunks: list[dict[str, Any]] | None = None,
        nodes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.changes = changes or []
        self.hunks = hunks or []
        self.nodes = nodes or []
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.inserted_hunks: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM counterparty_revision_changes" in sql and "session_id = $1" in sql:
            return [c for c in self.changes if c["session_id"] == args[0]]
        if "FROM counterparty_revision_hunks" in sql and "ANY" in sql:
            ids = set(args[0])
            return [h for h in self.hunks if h["change_id"] in ids]
        if "FROM nodes" in sql and "order_index FROM nodes" in sql:
            return self.nodes
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM counterparty_revision_sessions" in sql:
            return _SESSION
        if "FROM counterparty_revision_changes" in sql and "id = $1" in sql:
            return next((c for c in self.changes if c["id"] == args[0]), None)
        if "FROM counterparty_revision_hunks h" in sql:
            h = next((x for x in self.hunks if x["id"] == args[0]), None)
            if h is None:
                return None
            c = next(c for c in self.changes if c["id"] == h["change_id"])
            return {**h, "session_id": c["session_id"], "node_id": c["node_id"]}
        if "SELECT body, heading FROM nodes" in sql:
            return next((n for n in self.nodes if n["id"] == args[0]), None)
        if "SELECT parent_id, order_index FROM nodes" in sql:
            return next((n for n in self.nodes if n["id"] == args[0]), None)
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        if "INSERT INTO counterparty_revision_hunks" in sql:
            self.inserted_hunks.append(args)
        return "OK"

    def reclassify_args(self) -> tuple[Any, ...]:
        return next(a for sql, a in self.executes if "SET node_id = $2" in sql)


class _Settings:
    operator_actor = "operator"


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    monkeypatch.setattr(svc, "record_event", _noop)
    monkeypatch.setattr(svc, "get_settings", lambda: _Settings())


# --- hunk verdict → stored verdict + final_text -----------------------------


def test_accept_maps_to_proposed_text() -> None:
    verdict, final = svc._map_hunk_verdict(HunkDecideRequest(verdict="accept"), _hunk())
    assert verdict == "accepted" and final == "new"


def test_keep_maps_to_rejected_with_original() -> None:
    verdict, final = svc._map_hunk_verdict(HunkDecideRequest(verdict="keep"), _hunk())
    assert verdict == "rejected" and final == "old"


def test_counter_uses_staged_donna_text() -> None:
    verdict, final = svc._map_hunk_verdict(
        HunkDecideRequest(verdict="counter"), _hunk(donna_counter_text="our counter")
    )
    assert verdict == "modified" and final == "our counter"


def test_counter_without_staged_text_is_rejected() -> None:
    with pytest.raises(svc.BadDecision):
        svc._map_hunk_verdict(HunkDecideRequest(verdict="counter"), _hunk(donna_counter_text=None))


def test_edit_requires_final_text() -> None:
    with pytest.raises(svc.BadDecision):
        svc._map_hunk_verdict(HunkDecideRequest(verdict="edit"), _hunk())
    verdict, final = svc._map_hunk_verdict(
        HunkDecideRequest(verdict="edit", final_text="my text"), _hunk()
    )
    assert verdict == "modified" and final == "my text"


# --- abstain match-confirm reclassifications --------------------------------


@pytest.mark.asyncio
async def test_confirm_sets_node_to_provisional_best() -> None:
    conn = FakeConn(
        changes=[_change(proposed_parent_id="b3", match_confidence=0.5)],
        hunks=[_hunk()],
        nodes=[_node()],
    )
    await svc.confirm_match(conn, "ch1", ConfirmMatchRequest(action="confirm"))
    args = conn.reclassify_args()
    # (id, node_id, proposed_parent_id, proposed_order_index, match_confidence, hunk_count)
    assert args[1] == "b3" and args[2] is None and args[4] == 0.5


@pytest.mark.asyncio
async def test_confirm_without_candidate_is_rejected() -> None:
    conn = FakeConn(changes=[_change(proposed_parent_id=None)], hunks=[_hunk()])
    with pytest.raises(svc.BadDecision):
        await svc.confirm_match(conn, "ch1", ConfirmMatchRequest(action="confirm"))


@pytest.mark.asyncio
async def test_new_collapses_to_single_insertion_hunk() -> None:
    conn = FakeConn(
        changes=[_change(proposed_parent_id="b3")],
        hunks=[_hunk(original_text="abstain baseline body", proposed_text="abstain incoming body")],
        nodes=[_node(id="b3", parent_id="p1", order_index=300)],
    )
    await svc.confirm_match(conn, "ch1", ConfirmMatchRequest(action="new"))
    args = conn.reclassify_args()
    assert args[1] is None  # node_id stays null = new
    assert args[2] == "p1"  # anchored under candidate's parent
    assert args[3] == 300  # order_index set (no longer abstain)
    assert args[5] == 1  # one hunk
    assert len(conn.inserted_hunks) == 1
    # inserted hunk: (change_id, type, significance, pos, original, proposed)
    assert conn.inserted_hunks[0][1] == "insertion"
    assert conn.inserted_hunks[0][5] == "abstain incoming body"


@pytest.mark.asyncio
async def test_rematch_repoints_node_and_regenerates_hunks() -> None:
    conn = FakeConn(
        changes=[_change(proposed_parent_id="b3")],
        hunks=[_hunk(original_text="abstain baseline body", proposed_text="abstain incoming body")],
        nodes=[
            _node(id="b3", body="abstain baseline body"),
            _node(id="bX", body="different baseline clause"),
        ],
    )
    await svc.confirm_match(
        conn, "ch1", ConfirmMatchRequest(action="rematch", baseline_node_id="bX")
    )
    args = conn.reclassify_args()
    assert args[1] == "bX"  # repointed to operator-chosen baseline
    assert len(conn.inserted_hunks) >= 1  # hunks regenerated vs the new baseline


@pytest.mark.asyncio
async def test_rematch_requires_baseline_node_id() -> None:
    conn = FakeConn(changes=[_change(proposed_parent_id="b3")], hunks=[_hunk()], nodes=[_node()])
    with pytest.raises(svc.BadDecision):
        await svc.confirm_match(conn, "ch1", ConfirmMatchRequest(action="rematch"))


@pytest.mark.asyncio
async def test_confirm_match_rejects_non_abstain() -> None:
    conn = FakeConn(changes=[_change(node_id="b1", match_confidence=0.9)], hunks=[_hunk()])
    with pytest.raises(svc.NotAnAbstain):
        await svc.confirm_match(conn, "ch1", ConfirmMatchRequest(action="confirm"))


# --- whole-node decision (new / deleted) ------------------------------------


@pytest.mark.asyncio
async def test_decide_node_accept_records_proposed() -> None:
    conn = FakeConn(
        changes=[_change(node_id=None, proposed_order_index=200, match_confidence=None)],
        hunks=[_hunk(hunk_type="insertion", original_text=None, proposed_text="added clause")],
    )
    await svc.decide_node(conn, "ch1", NodeDecideRequest(verdict="accept"))
    verdict_update = next(a for sql, a in conn.executes if "SET verdict = $2" in sql)
    assert verdict_update[1] == "accepted" and verdict_update[2] == "added clause"


@pytest.mark.asyncio
async def test_decide_node_reject_records_original() -> None:
    conn = FakeConn(
        changes=[_change(node_id="b2", match_confidence=None)],
        hunks=[_hunk(hunk_type="deletion", original_text="removed clause", proposed_text=None)],
    )
    await svc.decide_node(conn, "ch1", NodeDecideRequest(verdict="reject"))
    verdict_update = next(a for sql, a in conn.executes if "SET verdict = $2" in sql)
    assert verdict_update[1] == "rejected" and verdict_update[2] == "removed clause"


@pytest.mark.asyncio
async def test_decide_node_rejects_edited_change() -> None:
    conn = FakeConn(
        changes=[_change(node_id="b1", match_confidence=0.9)], hunks=[_hunk(change_id="ch1")]
    )
    with pytest.raises(svc.WrongChangeKind):
        await svc.decide_node(conn, "ch1", NodeDecideRequest(verdict="accept"))
