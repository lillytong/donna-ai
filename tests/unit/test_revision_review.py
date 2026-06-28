"""F03c decision-state transitions (no live DB): the hunk verdict→stored mapping,
the abstain match-confirm reclassifications (confirm / new / rematch), and the
whole-node decision mapping. A fake connection routes SQL by substring and records
executes so the staged mutations are asserted without Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from backend.models.imports import ContractTreeResponse, StoredNode
from backend.models.revision_match import MatchedPair, RevisionMatchResult
from backend.models.revision_review import (
    ClusterDecideRequest,
    ConfirmMatchRequest,
    DocumentNode,
    HunkDecideRequest,
    NodeDecideRequest,
    ReviewChange,
    ReviewHunk,
)
from backend.services.import_ import revision_review as svc
from backend.services.import_.revision_cluster import cluster_id as _cluster_id
from backend.services.import_.revision_cluster import cluster_key as _cluster_key

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
    "pending_changes": 3,
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
        donna_rationale=None,
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
        received_node_id=None,
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
        session_status: str = "reviewing",
    ) -> None:
        self.changes = changes or []
        self.hunks = hunks or []
        self.nodes = nodes or []
        self.session_status = session_status
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
            return {**_SESSION, "status": self.session_status}
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


# --- guard: no decisions on an already-applied (completed) session ----------


@pytest.mark.asyncio
async def test_decide_hunk_rejects_completed_session() -> None:
    conn = FakeConn(changes=[_change()], hunks=[_hunk()], session_status="completed")
    with pytest.raises(svc.SessionAlreadyApplied) as exc:
        await svc.decide_hunk(conn, "h1", HunkDecideRequest(verdict="accept"))
    assert exc.value.status_code == 409
    assert not any("SET verdict = $2" in sql for sql, _ in conn.executes)


@pytest.mark.asyncio
async def test_decide_node_rejects_completed_session() -> None:
    conn = FakeConn(
        changes=[_change(node_id=None, proposed_order_index=200, match_confidence=None)],
        hunks=[_hunk(hunk_type="insertion", original_text=None, proposed_text="added clause")],
        session_status="completed",
    )
    with pytest.raises(svc.SessionAlreadyApplied) as exc:
        await svc.decide_node(conn, "ch1", NodeDecideRequest(verdict="accept"))
    assert exc.value.status_code == 409
    assert not any("SET verdict = $2" in sql for sql, _ in conn.executes)


@pytest.mark.asyncio
async def test_confirm_match_rejects_completed_session() -> None:
    conn = FakeConn(
        changes=[_change(proposed_parent_id="b3", match_confidence=0.5)],
        hunks=[_hunk()],
        nodes=[_node()],
        session_status="completed",
    )
    with pytest.raises(svc.SessionAlreadyApplied) as exc:
        await svc.confirm_match(conn, "ch1", ConfirmMatchRequest(action="confirm"))
    assert exc.value.status_code == 409
    assert not any("SET node_id = $2" in sql for sql, _ in conn.executes)


# --- cross-document clustering (DD-89 / F34) --------------------------------


def test_stamp_clusters_groups_recurring_edits_and_skips_singleton_and_abstain() -> None:
    # Two edited clauses carry the SAME counterparty edit (modulo case/whitespace/edge punct);
    # they must share one cluster id (size 2). A unique edit stays a singleton; an abstain whose
    # text happens to match is excluded (recommend never judged it) and does not bump the count.
    a = svc._to_change(
        _change(id="chA", node_id="bA", match_confidence=0.9),
        [
            svc._to_hunk(
                _hunk(id="hA", change_id="chA", original_text="Buyer", proposed_text="Purchaser")
            )
        ],
    )
    b = svc._to_change(
        _change(id="chB", node_id="bB", match_confidence=0.9),
        [
            svc._to_hunk(
                _hunk(
                    id="hB", change_id="chB", original_text="(Buyer", proposed_text="  purchaser  "
                )
            )
        ],
    )
    singleton = svc._to_change(
        _change(id="chC", node_id="bC", match_confidence=0.9),
        [svc._to_hunk(_hunk(id="hC", change_id="chC", original_text="foo", proposed_text="bar"))],
    )
    abstain = svc._to_change(
        _change(id="chD"),
        [
            svc._to_hunk(
                _hunk(id="hD", change_id="chD", original_text="Buyer", proposed_text="Purchaser")
            )
        ],
    )
    svc._stamp_clusters([a, b, singleton, abstain])

    ha, hb = a.hunks[0], b.hunks[0]
    # Same id as the SHARED helper Step-1 recommend-time clustering uses (no drift).
    expected = _cluster_id(_cluster_key("substantive", "Buyer", "Purchaser") or ("", ""))
    assert ha.cluster_id == expected and hb.cluster_id == expected
    assert ha.cluster_size == 2 and hb.cluster_size == 2
    assert singleton.hunks[0].cluster_id is None and singleton.hunks[0].cluster_size == 1
    assert abstain.hunks[0].cluster_id is None and abstain.hunks[0].cluster_size == 1


@pytest.mark.asyncio
async def test_decide_cluster_propagates_to_all_member_change_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes = [
        _change(id="chA", node_id="bA", match_confidence=0.9, hunk_count=1),
        _change(id="chB", node_id="bB", match_confidence=0.9, hunk_count=1),
    ]
    hunks = [
        _hunk(id="hA", change_id="chA", original_text="Buyer", proposed_text="Purchaser"),
        _hunk(id="hB", change_id="chB", original_text="Buyer", proposed_text="Purchaser"),
    ]
    conn = FakeConn(changes=changes, hunks=hunks)
    sentinel = object()

    async def _stub_payload(_conn: Any, _sid: str) -> Any:
        return sentinel

    monkeypatch.setattr(svc, "get_review_payload", _stub_payload)
    cid = _cluster_id(_cluster_key("substantive", "Buyer", "Purchaser") or ("", ""))
    result = await svc.decide_cluster(conn, "s1", cid, ClusterDecideRequest(verdict="accept"))

    assert result is sentinel
    verdict_updates = [a for sql, a in conn.executes if "SET verdict = $2" in sql]
    assert {a[0] for a in verdict_updates} == {"hA", "hB"}
    assert all(a[1] == "accepted" and a[2] == "Purchaser" for a in verdict_updates)
    # Progress refreshed for EACH distinct affected change row (members span >1 change).
    progress = [a for sql, a in conn.executes if "SET hunks_decided = sub.decided" in sql]
    assert {a[0] for a in progress} == {"chA", "chB"}


@pytest.mark.asyncio
async def test_decide_cluster_unknown_cluster_is_404() -> None:
    conn = FakeConn(
        changes=[_change(id="chA", node_id="bA", match_confidence=0.9)],
        hunks=[_hunk(id="hA", change_id="chA")],
    )
    with pytest.raises(svc.ClusterNotFound) as exc:
        await svc.decide_cluster(conn, "s1", "cl_missing", ClusterDecideRequest(verdict="accept"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_peel_off_decide_hunk_leaves_cluster_sibling_untouched() -> None:
    # A member overridden via the per-hunk decide must not touch its cluster siblings.
    changes = [
        _change(id="chA", node_id="bA", match_confidence=0.9, hunk_count=1),
        _change(id="chB", node_id="bB", match_confidence=0.9, hunk_count=1),
    ]
    hunks = [
        _hunk(id="hA", change_id="chA", original_text="Buyer", proposed_text="Purchaser"),
        _hunk(id="hB", change_id="chB", original_text="Buyer", proposed_text="Purchaser"),
    ]
    conn = FakeConn(changes=changes, hunks=hunks)
    await svc.decide_hunk(conn, "hA", HunkDecideRequest(verdict="keep"))

    verdict_updates = [a for sql, a in conn.executes if "SET verdict = $2" in sql]
    assert len(verdict_updates) == 1
    assert verdict_updates[0][0] == "hA" and verdict_updates[0][1] == "rejected"


# --- change structural context (F03c UX enrichment, every change kind) ------


def _sn(
    nid: str,
    parent: str | None,
    order: int,
    *,
    heading: str | None = None,
    body: str | None = None,
) -> StoredNode:
    return StoredNode(
        id=nid,
        parent_id=parent,
        order_index=order,
        content_type="prose",
        heading=heading,
        body=body,
    )


def _baseline_tree() -> ContractTreeResponse:
    # Services › Performance › [Process Optimisation] -> {Uptime targets, Response times}
    return ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("svc", None, 0, heading="Services"),
            _sn("perf", "svc", 0, heading="Performance"),
            _sn("cand", "perf", 0, heading="Process Optimisation"),
            _sn("c1", "cand", 0, heading="Uptime targets"),
            _sn("c2", "cand", 1, heading="Response times"),
        ],
    )


def _received_tree() -> ContractTreeResponse:
    # Master Terms › Performance › [body=Process Optimization] -> {System uptime, Latency}
    return ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("r-root", None, 0, heading="Master Terms"),
            _sn("r-perf", "r-root", 0, heading="Performance"),
            _sn("r-cand", "r-perf", 0, body="Process Optimization"),
            _sn("rc1", "r-cand", 0, body="System uptime"),
            _sn("rc2", "r-cand", 1, body="Latency"),
        ],
    )


def _ctx_hunk(original: str | None, proposed: str | None) -> ReviewHunk:
    return ReviewHunk(
        id="h1",
        change_id="ab1",
        hunk_type="replacement" if original is not None else "insertion",
        significance="substantive",
        position_in_body=0,
        original_text=original,
        proposed_text=proposed,
        donna_verdict=None,
        donna_counter_text=None,
        donna_rationale=None,
        verdict="pending",
        final_text=None,
    )


def _make_change(
    kind: str,
    hunks: list[ReviewHunk],
    *,
    node_id: str | None = None,
    parent: str | None = None,
    order: int | None = None,
    conf: float | None = None,
) -> ReviewChange:
    return ReviewChange(
        id="ch1",
        session_id="s1",
        change_kind=kind,  # type: ignore[arg-type]
        node_id=node_id,
        proposed_parent_id=parent,
        proposed_order_index=order,
        match_confidence=conf,
        hunk_count=len(hunks),
        hunks_decided=0,
        status="pending",
        hunks=hunks,
    )


def _edited_baseline_tree() -> ContractTreeResponse:
    # Services › Payment › [pterm body] , sibling "plate" so a next-neighbour exists.
    return ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("svc", None, 0, heading="Services"),
            _sn("pay", "svc", 0, heading="Payment"),
            _sn("pterm", "pay", 0, body="The licensee shall pay within thirty days of invoice."),
            _sn("plate", "pay", 1, body="Late payments accrue interest at one percent monthly."),
        ],
    )


def test_locate_derives_number_breadcrumb_and_neighbours() -> None:
    located = svc._locate(_edited_baseline_tree().nodes, "pterm")
    assert located is not None
    assert located.number == "1.1.1"
    assert located.breadcrumb == ["Services", "Payment"]
    assert located.item.id == "pterm"
    assert located.prev_label is None
    assert located.next_label == "Late payments accrue interest at one percent monthly."


def test_build_side_context_no_target_degrades() -> None:
    ctx = svc._build_side_context("baseline", _baseline_tree(), None)
    assert ctx.found is False
    assert ctx.number is None and ctx.breadcrumb == [] and ctx.children_preview == []
    assert ctx.body is None


def test_find_incoming_id_flags_ambiguity() -> None:
    tree = ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("a", None, 0, body="Performance Standards"),
            _sn("b", None, 1, body="Performance Standards"),
        ],
    )
    node_id, ambiguous = svc._find_incoming_id(tree.nodes, "Performance Standards")
    assert node_id == "a" and ambiguous is True


@pytest.mark.asyncio
async def test_edited_change_resolves_exactly_with_in_context_body() -> None:
    # EDITED: node_id is the baseline node directly -> exact resolution, full body
    # returned with offsets that index INTO it (so the diff renders in place).
    body = "The licensee shall pay within thirty days of invoice."
    pos = body.index("thirty")
    hunk = _ctx_hunk("thirty", "forty five")
    hunk.position_in_body = pos
    change = _make_change("edited", [hunk], node_id="pterm", conf=0.55)

    ctx = await svc._change_context(
        FakeConn(), "c1", change, _edited_baseline_tree(), _received_tree()
    )

    assert ctx.baseline.found is True
    assert ctx.baseline.number == "1.1.1"
    assert ctx.baseline.heading is None  # body-only clause
    assert ctx.baseline.breadcrumb == ["Services", "Payment"]
    assert ctx.baseline.body == body
    # The hunk offset indexes into the returned body -> in-place rendering is valid.
    assert ctx.baseline.body[pos : pos + len("thirty")] == "thirty"
    assert ctx.baseline.next_label == "Late payments accrue interest at one percent monthly."
    # The their side does not apply to an edited change.
    assert ctx.their.found is False


@pytest.mark.asyncio
async def test_deleted_change_resolves_baseline_by_node_id() -> None:
    change = _make_change(
        "deleted", [_ctx_hunk("Process Optimisation", None)], node_id="cand", conf=None
    )
    ctx = await svc._change_context(FakeConn(), "c1", change, _baseline_tree(), _received_tree())
    assert ctx.baseline.found is True
    assert ctx.baseline.number == "1.1.1"
    assert ctx.baseline.breadcrumb == ["Services", "Performance"]
    assert ctx.their.found is False


@pytest.mark.asyncio
async def test_new_change_resolves_their_side_from_received_tree() -> None:
    # NEW: node_id NULL; the added clause is body-matched in the as_received tree.
    change = _make_change("new", [_ctx_hunk(None, "Process Optimization")], parent="cand", order=2)
    ctx = await svc._change_context(FakeConn(), "c1", change, _baseline_tree(), _received_tree())
    assert ctx.their.found is True
    assert ctx.their.breadcrumb == ["Master Terms", "Performance"]
    assert ctx.their.children_preview == ["System uptime", "Latency"]
    assert ctx.baseline.found is False


@pytest.mark.asyncio
async def test_abstain_context_populates_both_sides() -> None:
    # Candidate abstain: a heading match the operator can't judge from text alone.
    conn = FakeConn(nodes=[{"id": "cand", "body": None, "heading": "Process Optimisation"}])
    change = _make_change(
        "abstain",
        [_ctx_hunk("Process Optimisation", "Process Optimization")],
        parent="cand",
        conf=0.42,
    )

    pair = await svc._change_context(conn, "c1", change, _baseline_tree(), _received_tree())

    assert pair.baseline.found is True
    assert pair.baseline.number == "1.1.1"
    assert pair.baseline.breadcrumb == ["Services", "Performance"]
    assert pair.baseline.children_preview == ["Uptime targets", "Response times"]

    assert pair.their.found is True
    assert pair.their.breadcrumb == ["Master Terms", "Performance"]
    assert pair.their.children_preview == ["System uptime", "Latency"]


@pytest.mark.asyncio
async def test_abstain_context_no_candidate_degrades_gracefully() -> None:
    # No baseline candidate: the "(no candidate)" side returns empty, never errors;
    # the incoming side still resolves from the as_received tree.
    conn = FakeConn()
    change = _make_change("abstain", [_ctx_hunk(None, "Process Optimization")], parent=None)

    pair = await svc._change_context(conn, "c1", change, _baseline_tree(), _received_tree())

    assert pair.baseline.found is False
    assert pair.baseline.breadcrumb == [] and pair.baseline.children_preview == []
    assert pair.their.found is True
    assert pair.their.children_preview == ["System uptime", "Latency"]


# --- two-pane document view: node-level kind derivation (pure) ---------------


def test_derive_kinds_new_is_added() -> None:
    change = _make_change("new", [_ctx_hunk(None, "added clause")], parent="b1", order=2)
    assert svc.derive_document_change_kinds(change) == ["added"]


def test_derive_kinds_deleted_is_deleted() -> None:
    change = _make_change("deleted", [_ctx_hunk("removed clause", None)], node_id="b2")
    assert svc.derive_document_change_kinds(change) == ["deleted"]


def test_derive_kinds_edited_with_hunks_is_modified() -> None:
    # An intra-clause insertion/replacement/deletion -> node-level "modified", never a
    # node add/delete (the hunk types describe text WITHIN the clause).
    change = _make_change("edited", [_ctx_hunk("thirty", "forty five")], node_id="b1", conf=0.6)
    assert svc.derive_document_change_kinds(change) == ["modified"]


def test_derive_kinds_edited_without_hunks_is_empty() -> None:
    change = _make_change("edited", [], node_id="b1", conf=0.6)
    assert svc.derive_document_change_kinds(change) == []


def test_derive_kinds_never_emits_shifted_for_any_kind() -> None:
    # "shifted" is in the legend but not derivable from staged data — assert no kind
    # path produces it (the load-bearing gap guard).
    kinds = [
        *svc.derive_document_change_kinds(_make_change("new", [_ctx_hunk(None, "x")], order=1)),
        *svc.derive_document_change_kinds(
            _make_change("deleted", [_ctx_hunk("x", None)], node_id="b")
        ),
        *svc.derive_document_change_kinds(
            _make_change("edited", [_ctx_hunk("a", "b")], node_id="b", conf=0.6)
        ),
        *svc.derive_document_change_kinds(
            _make_change("abstain", [_ctx_hunk("a", "b")], parent="b")
        ),
    ]
    assert "shifted" not in kinds


def test_derive_kinds_abstain_is_empty() -> None:
    # Abstains are carried in `abstain_matches[]`, not the change overlay.
    change = _make_change("abstain", [_ctx_hunk("a", "b")], parent="b1", conf=0.4)
    assert svc.derive_document_change_kinds(change) == []


# --- two-pane document view: tree flattening (number / depth / order) --------


def test_flatten_document_reading_order_then_role_aware_numbering() -> None:
    flat = svc._flatten_document(_edited_baseline_tree())
    # _flatten_document gives reading-order + depth + text only; it no longer derives
    # clause numbers (DD-43): numbering is role-aware and assigned AFTER role resolution
    # by _assign_clause_numbers, so front/back-matter can never be numbered as clauses.
    assert [(n.node_id, n.clause_number, n.depth) for n in flat] == [
        ("svc", None, 0),
        ("pay", None, 1),
        ("pterm", None, 2),
        ("plate", None, 2),
    ]
    # Body-only clause text is returned verbatim (offsets index into it downstream).
    assert flat[2].text == "The licensee shall pay within thirty days of invoice."
    # The separate role-aware pass numbers the clause-role nodes via the canonical scheme.
    svc._assign_clause_numbers(flat)
    assert [(n.node_id, n.clause_number) for n in flat] == [
        ("svc", "1"),
        ("pay", "1.1"),
        ("pterm", "1.1.1"),
        ("plate", "1.1.2"),
    ]


def test_flatten_document_none_tree_is_empty() -> None:
    assert svc._flatten_document(None) == []


def test_flatten_marks_heading_only_node_and_keeps_heading_text() -> None:
    # Mirrors import's `typeLabel === "Heading"` (heading set, no body) so the review
    # pane can bold headings. A heading-only node -> is_heading True AND text carries the
    # heading (renders, never empty); a body-bearing clause -> is_heading False.
    tree = ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("h", None, 0, heading="Payment Terms"),
            _sn("c", "h", 0, body="The licensee shall pay within thirty days."),
        ],
    )
    flat = {n.node_id: n for n in svc._flatten_document(tree)}
    assert flat["h"].is_heading is True
    assert flat["h"].text == "Payment Terms"
    assert flat["c"].is_heading is False
    assert flat["c"].text == "The licensee shall pay within thirty days."


# --- BUG R5: context number is role-aware, never `_locate`'s inflated count, and new
# clauses are placed in document order (not floated to the top) --------------------


def test_build_side_context_prefers_role_aware_number_over_locate() -> None:
    # `_locate` numbers "pterm" positionally as "1.1.1". The canonical role-aware map
    # (the number derive_numbers/the two-pane view assign) wins when supplied — this is
    # what stops a matched clause showing `_locate`'s inflated path (the 13.3 -> 31.3.1
    # / 18.1 -> 36.1 live failures, where front-matter/recitals had bumped the count).
    tree = _edited_baseline_tree()
    role_aware = svc._build_side_context("baseline", tree, "pterm", {"pterm": "13.3.1"})
    assert role_aware.number == "13.3.1"
    # Breadcrumb / body still come from the positional walk (only the NUMBER is overridden).
    assert role_aware.breadcrumb == ["Services", "Payment"]
    assert role_aware.body == "The licensee shall pay within thirty days of invoice."
    # No map (or id absent) -> falls back to the positional path (unit-test/legacy shape).
    fallback = svc._build_side_context("baseline", tree, "pterm", None)
    assert fallback.number == "1.1.1"
    assert svc._build_side_context("baseline", tree, "pterm", {"other": "9.9"}).number == "1.1.1"


def test_assign_clause_numbers_excludes_nonclause_no_inflated_number() -> None:
    # Reproduces the >20 inflation at its root: three front-matter siblings precede the
    # clauses. A naive positional count would make the first clause "4" and a child "4.1";
    # the role-aware scheme skips non-clause nodes so they stay "1" / "1.1" — i.e. a clause
    # number can never exceed the clause count, the property the live 31.3.1 / 36.1 violated.
    def dn(node_id: str, role: str, depth: int) -> DocumentNode:
        return DocumentNode(
            node_id=node_id, clause_number=None, role=role, depth=depth, text=node_id
        )

    nodes = [
        dn("title", "title", 0),
        dn("recital-a", "recital", 0),
        dn("recital-b", "recital", 0),
        dn("c1", "clause", 0),
        dn("c1-1", "clause", 1),
        dn("c2", "clause", 0),
    ]
    svc._assign_clause_numbers(nodes)
    numbered = {n.node_id: n.clause_number for n in nodes}
    assert numbered["title"] is None and numbered["recital-a"] is None
    assert numbered["c1"] == "1" and numbered["c1-1"] == "1.1" and numbered["c2"] == "2"


def _doc_node(node_id: str, depth: int = 0) -> DocumentNode:
    return DocumentNode(
        node_id=node_id, clause_number=None, role="clause", depth=depth, text=node_id
    )


def _resolved_for_order() -> svc._ResolvedDocument:
    # Revised reading order (flat snapshot index == node_id): b1=0, b2=1, NEW=2, b3=3.
    # Baseline order has a DELETED node `bdel` between b2 and b3 (absent from revised).
    revised = [_doc_node("0"), _doc_node("1"), _doc_node("2"), _doc_node("3")]
    baseline = [_doc_node("b1"), _doc_node("b2"), _doc_node("bdel"), _doc_node("b3")]
    match = RevisionMatchResult(
        matches=[
            MatchedPair(incoming_index=0, baseline_id="b1", confidence=1.0),
            MatchedPair(incoming_index=1, baseline_id="b2", confidence=1.0),
            MatchedPair(incoming_index=3, baseline_id="b3", confidence=1.0),
        ],
        new=[2],
        deleted=["bdel"],
        abstains=[],
    )
    return svc._ResolvedDocument(
        baseline=baseline,
        revised=revised,
        baseline_tree=None,
        revised_tree=None,
        match=match,
        received_snapshot_id="snap",
    )


def test_document_order_new_clause_not_floated_to_top() -> None:
    # A genuinely-new clause whose F03b `proposed_parent_id` is NULL must land at its REAL
    # revised position (after b2, index 2), never at the top — the live "31.3.1/36.1 sort
    # to the top" failure. Edited/deleted interleave by document position.
    key = svc._document_order_key(_resolved_for_order())
    edited_b1 = _make_change("edited", [_ctx_hunk("a", "b")], node_id="b1", conf=0.6)
    edited_b2 = _make_change("edited", [_ctx_hunk("a", "b")], node_id="b2", conf=0.6)
    edited_b3 = _make_change("edited", [_ctx_hunk("a", "b")], node_id="b3", conf=0.6)
    deleted = _make_change("deleted", [_ctx_hunk("x", None)], node_id="bdel")
    new = _make_change("new", [_ctx_hunk(None, "added")], parent=None, order=100)
    new.received_node_id = "2"

    ordered = sorted([new, edited_b3, deleted, edited_b1, edited_b2], key=key)
    assert [c.node_id or "NEW" for c in ordered] == ["b1", "b2", "bdel", "NEW", "b3"]
    # The new clause is NOT first (the regression guard).
    assert ordered[0] is not new


def test_document_order_new_with_null_received_falls_back_to_parent_anchor() -> None:
    # Defensive: a pre-migration-0011 new row (received_node_id NULL) anchors just after
    # its proposed_parent's revised position instead of floating to the top.
    key = svc._document_order_key(_resolved_for_order())
    new = _make_change("new", [_ctx_hunk(None, "added")], parent="b1", order=100)
    edited_b3 = _make_change("edited", [_ctx_hunk("a", "b")], node_id="b3", conf=0.6)
    ordered = sorted([edited_b3, new], key=key)
    assert ordered[0] is new  # anchored at b1 (pos 0)+0.5, before b3 (pos 3)


# --- projected reading order: verdict-aware numbering + real placement -------------


def _resolved_for_projection() -> svc._ResolvedDocument:
    # Baseline two top-level clauses b1, b2 -> "1", "2". Counterparty inserts a NEW clause
    # between them (revised order: 0=match b1, 1=NEW, 2=match b2). Mirrors the section-18
    # insert pushing the next section down.
    revised = [_doc_node("0"), _doc_node("1"), _doc_node("2")]
    baseline = [_doc_node("b1"), _doc_node("b2")]
    match = RevisionMatchResult(
        matches=[
            MatchedPair(incoming_index=0, baseline_id="b1", confidence=1.0),
            MatchedPair(incoming_index=2, baseline_id="b2", confidence=1.0),
        ],
        new=[1],
        deleted=[],
        abstains=[],
    )
    return svc._ResolvedDocument(
        baseline=baseline,
        revised=revised,
        baseline_tree=None,
        revised_tree=None,
        match=match,
        received_snapshot_id="snap",
    )


def _added_change(verdict: str = "pending") -> ReviewChange:
    hunk = _ctx_hunk(None, "Limitation of Liability")
    hunk.verdict = verdict  # type: ignore[assignment]
    c = _make_change("new", [hunk], parent=None, order=100)
    c.received_node_id = "1"  # its real revised position (between b1 and b2)
    return c


def test_projected_numbering_bumps_baseline_clause_down_for_pending_addition() -> None:
    # (a) A non-rejected (pending) addition before b2 pushes b2 from "2" to "3".
    projected = svc._build_projected(_resolved_for_projection(), [_added_change("pending")])
    by_id = {p.node_id: p for p in projected}
    assert by_id["b1"].clause_number == "1"
    assert by_id["1"].clause_number == "2" and by_id["1"].change_kind == "added"
    assert by_id["b2"].clause_number == "3"  # bumped down by the insert


def test_projected_numbering_reverts_when_addition_rejected() -> None:
    # (b) Rejecting that addition EMITS it as a struck trace (in place, unnumbered) and
    # excludes it from the projected numbering tree; b2 renumbers back to "2".
    projected = svc._build_projected(_resolved_for_projection(), [_added_change("rejected")])
    by_id = {p.node_id: p for p in projected}
    # The rejected addition is still emitted at its real revised position (between b1, b2)...
    assert "1" in by_id
    rejected_add = by_id["1"]
    assert rejected_add.change_kind == "added"
    assert rejected_add.numbered is False and rejected_add.clause_number is None
    assert [p.node_id for p in projected] == ["b1", "1", "b2"]
    # ...but it consumes no number, so the surrounding clauses number as if it were absent.
    assert by_id["b1"].clause_number == "1"
    assert by_id["b2"].clause_number == "2"  # back to the baseline number


def test_projected_added_clause_placed_at_real_position_not_top() -> None:
    # (c) The added clause lands at its real revised position (between b1 and b2), never
    # floated to the top — the live "added clauses pile at the top" failure.
    projected = svc._build_projected(_resolved_for_projection(), [_added_change("pending")])
    assert [p.node_id for p in projected] == ["b1", "1", "b2"]
    assert projected[0].node_id == "b1"  # top is the real first baseline clause, not the add


def test_projected_deletion_accepted_unnumbered_rejected_kept_numbered() -> None:
    # A deleted baseline node `bdel` (absent from the revised side): an ACCEPTED deletion is
    # shown in place but excluded from numbering; a REJECTED deletion survives and numbers.
    resolved = _resolved_for_order()  # baseline b1,b2,bdel,b3 ; bdel matcher-deleted
    accepted = _make_change("deleted", [_ctx_hunk("x", None)], node_id="bdel")
    accepted.hunks[0].verdict = "accepted"  # type: ignore[assignment]
    proj_acc = {p.node_id: p for p in svc._build_projected(resolved, [accepted])}
    assert proj_acc["bdel"].change_kind == "deleted" and proj_acc["bdel"].numbered is False
    assert proj_acc["bdel"].clause_number is None
    # Survivors number contiguously around the removed clause: b1,b2,b3 -> 1,2,3.
    assert [proj_acc[i].clause_number for i in ("b1", "b2", "b3")] == ["1", "2", "3"]

    rejected = _make_change("deleted", [_ctx_hunk("x", None)], node_id="bdel")
    rejected.hunks[0].verdict = "rejected"  # type: ignore[assignment]
    proj_rej = {p.node_id: p for p in svc._build_projected(resolved, [rejected])}
    assert proj_rej["bdel"].numbered is True and proj_rej["bdel"].clause_number == "3"
    assert proj_rej["b3"].clause_number == "4"  # the kept deletion pushes b3 down


def test_projected_rejected_addition_emitted_struck_unnumbered() -> None:
    # (a) A REJECTED addition is EMITTED at its real revised position as a struck trace —
    # numbered=False, clause_number=None, change_kind "added" — and consumes no number, so
    # the clause below it keeps the number it would have if the addition were absent.
    projected = svc._build_projected(_resolved_for_projection(), [_added_change("rejected")])
    by_id = {p.node_id: p for p in projected}
    added = by_id["1"]
    assert added.change_kind == "added"
    assert added.numbered is False and added.clause_number is None
    assert added.change_id == "ch1"
    # Emitted in place (between b1 and b2), not omitted and not floated.
    assert [p.node_id for p in projected] == ["b1", "1", "b2"]
    # b2 keeps the number it would have with the addition absent (baseline "2").
    assert by_id["b1"].clause_number == "1"
    assert by_id["b2"].clause_number == "2"


# --- cascade: reject of an added parent rejects its added descendants -------------


def test_descendant_received_ids_collects_subtree_only() -> None:
    tree = ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("r17", None, 0, heading="17"),
            _sn("r18", None, 1, heading="18"),
            _sn("r18-1", "r18", 0, body="18.1"),
            _sn("r18-2", "r18", 1, body="18.2"),
            _sn("r18-2-a", "r18-2", 0, body="18.2.a"),
            _sn("r19", None, 2, heading="19"),
        ],
    )
    # Every proper descendant of r18 (incl. the grandchild), and nothing outside the subtree.
    assert svc._descendant_received_ids(tree, "r18") == {"r18-1", "r18-2", "r18-2-a"}
    assert svc._descendant_received_ids(tree, "missing") == set()
    assert svc._descendant_received_ids(None, "r18") == set()


def _new_change(change_id: str, received_node_id: str) -> dict[str, Any]:
    return _change(
        id=change_id,
        node_id=None,
        proposed_order_index=180,
        match_confidence=None,
        received_node_id=received_node_id,
    )


def _cascade_conn_and_tree() -> tuple[FakeConn, ContractTreeResponse]:
    # Counterparty added section "18" (r18) with three sub-clauses 18.1/18.2/18.3 — each a
    # NEW change row keyed to its as_received node id.
    changes = [
        _new_change("p18", "r18"),
        _new_change("c1", "r18-1"),
        _new_change("c2", "r18-2"),
        _new_change("c3", "r18-3"),
    ]
    hunks = [
        _hunk(
            id="h-p18",
            change_id="p18",
            hunk_type="insertion",
            original_text=None,
            proposed_text="Section 18",
        ),
        _hunk(
            id="h-c1",
            change_id="c1",
            hunk_type="insertion",
            original_text=None,
            proposed_text="18.1",
        ),
        _hunk(
            id="h-c2",
            change_id="c2",
            hunk_type="insertion",
            original_text=None,
            proposed_text="18.2",
        ),
        _hunk(
            id="h-c3",
            change_id="c3",
            hunk_type="insertion",
            original_text=None,
            proposed_text="18.3",
        ),
    ]
    conn = FakeConn(changes=changes, hunks=hunks)
    tree = ContractTreeResponse.from_rows(
        "c1",
        [
            _sn("r18", None, 0, heading="18"),
            _sn("r18-1", "r18", 0, body="18.1"),
            _sn("r18-2", "r18", 1, body="18.2"),
            _sn("r18-3", "r18", 2, body="18.3"),
        ],
    )
    return conn, tree


@pytest.mark.asyncio
async def test_decide_node_reject_added_parent_cascades_to_added_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (b) Rejecting added "18" auto-rejects 18.1/18.2/18.3 in the SAME call.
    conn, tree = _cascade_conn_and_tree()

    async def _fake_resolve(_conn: Any, _session: Any) -> svc._ResolvedDocument:
        return svc._ResolvedDocument([], [], None, tree, None, None)

    monkeypatch.setattr(svc, "_resolve_document", _fake_resolve)
    await svc.decide_node(conn, "p18", NodeDecideRequest(verdict="reject"))

    verdicts = {a[0]: a[1] for sql, a in conn.executes if "SET verdict = $2" in sql}
    assert verdicts == {
        "h-p18": "rejected",
        "h-c1": "rejected",
        "h-c2": "rejected",
        "h-c3": "rejected",
    }


@pytest.mark.asyncio
async def test_decide_node_accept_added_parent_does_not_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (c) ASYMMETRIC: accepting added "18" leaves its children PENDING (no cascade, the
    # document-resolution path is never even entered on accept).
    conn, tree = _cascade_conn_and_tree()

    async def _must_not_resolve(_conn: Any, _session: Any) -> svc._ResolvedDocument:
        raise AssertionError("accept must not resolve the document or cascade")

    monkeypatch.setattr(svc, "_resolve_document", _must_not_resolve)
    await svc.decide_node(conn, "p18", NodeDecideRequest(verdict="accept"))

    touched = {a[0] for sql, a in conn.executes if "SET verdict = $2" in sql}
    assert touched == {"h-p18"}  # only the parent; the three children are untouched
