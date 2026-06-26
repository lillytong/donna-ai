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
from backend.models.revision_review import (
    ConfirmMatchRequest,
    HunkDecideRequest,
    NodeDecideRequest,
    ReviewChange,
    ReviewHunk,
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


def test_flatten_document_derives_number_depth_and_reading_order() -> None:
    flat = svc._flatten_document(_edited_baseline_tree())
    assert [(n.node_id, n.clause_number, n.depth) for n in flat] == [
        ("svc", "1", 0),
        ("pay", "1.1", 1),
        ("pterm", "1.1.1", 2),
        ("plate", "1.1.2", 2),
    ]
    # Body-only clause text is returned verbatim (offsets index into it downstream).
    assert flat[2].text == "The licensee shall pay within thirty days of invoice."


def test_flatten_document_none_tree_is_empty() -> None:
    assert svc._flatten_document(None) == []
