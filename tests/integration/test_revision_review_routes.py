"""F03c routes: the read payload (two-phase split + ordering + resume state) and
the decision endpoints, with the apply path's F08 reuse + issue-seeding asserted by
monkeypatching node_edit/node_create/node_delete. DB faked through the real service
(no live database)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from backend.api import revision_review as api
from backend.services import node_create, node_delete, node_edit
from backend.services.import_ import revision_review as svc
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(api.router)
client = TestClient(app)

_SESSION = {
    "id": "s1",
    "contract_id": "c1",
    "baseline_snapshot_id": "snap-1",
    "source": "counterparty",
    "source_filename": "v4.docx",
    "parse_path": "clean_diff",
    "status": "reviewing",
    "changes_count": 4,
    "changes_reviewed_count": 0,
    "imported_at": datetime(2026, 6, 25),
}


def _change(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="ch",
        session_id="s1",
        node_id=None,
        proposed_parent_id=None,
        proposed_order_index=None,
        match_confidence=None,
        hunk_count=1,
        hunks_decided=1,
        status="complete",
    )
    base.update(kw)
    return base


def _hunk(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="h",
        change_id="ch",
        hunk_type="replacement",
        significance="substantive",
        position_in_body=0,
        original_text="orig",
        proposed_text="theirs",
        donna_verdict=None,
        donna_counter_text=None,
        verdict="pending",
        final_text=None,
    )
    base.update(kw)
    return base


def _node(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(id="n", parent_id=None, order_index=0, body="orig", heading=None)
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
        self.issues: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM counterparty_revision_sessions" in sql and "contract_id = $1" in sql:
            return [_SESSION]
        if "FROM counterparty_revision_changes" in sql and "session_id = $1" in sql:
            return [c for c in self.changes if c["session_id"] == args[0]]
        if "FROM counterparty_revision_hunks" in sql and "ANY" in sql:
            ids = set(args[0])
            return [h for h in self.hunks if h["change_id"] in ids]
        if "order_index FROM nodes" in sql:
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
        if "SELECT body, heading FROM nodes" in sql or "SELECT parent_id, order_index" in sql:
            return next((n for n in self.nodes if n["id"] == args[0]), None)
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "INSERT INTO issues" in sql:
            self.issues.append(args)
            return f"issue-{len(self.issues)}"
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        return "OK"


class _Settings:
    operator_actor = "operator"


def _install(monkeypatch: pytest.MonkeyPatch, conn: FakeConn) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"edit": [], "create": [], "delete": []}

    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[FakeConn]:
        yield conn

    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    async def _fake_edit(_c: Any, cid: str, nid: str, text: str) -> Any:
        calls["edit"].append((nid, text))
        return None

    async def _fake_create(_c: Any, cid: str, **kw: Any) -> Any:
        calls["create"].append(kw)
        return None

    async def _fake_delete(_c: Any, cid: str, nid: str) -> Any:
        calls["delete"].append(nid)
        return ["x"]

    monkeypatch.setattr(api, "acquire", _fake_acquire)
    monkeypatch.setattr(svc, "record_event", _noop)
    monkeypatch.setattr(svc, "get_settings", lambda: _Settings())
    monkeypatch.setattr(node_edit, "edit_node", _fake_edit)
    monkeypatch.setattr(node_create, "create_node", _fake_create)
    monkeypatch.setattr(node_delete, "delete_node", _fake_delete)
    return calls


# --- read -------------------------------------------------------------------


def test_list_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn()
    _install(monkeypatch, conn)
    resp = client.get("/contracts/c1/revisions/sessions")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "s1"


def test_review_payload_splits_phases_and_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    changes = [
        _change(id="edit1", node_id="b1", match_confidence=0.9),  # edited, doc-order 0
        _change(id="del1", node_id="b2", match_confidence=None),  # deleted, doc-order 1
        _change(id="ab_lo", proposed_parent_id="b3", match_confidence=0.3, status="pending"),
        _change(id="ab_hi", proposed_parent_id="b4", match_confidence=0.6, status="pending"),
    ]
    hunks = [
        _hunk(id="he", change_id="edit1"),
        _hunk(id="hd", change_id="del1", hunk_type="deletion", proposed_text=None),
        _hunk(id="ha1", change_id="ab_lo"),
        _hunk(id="ha2", change_id="ab_hi"),
    ]
    nodes = [_node(id="b1", order_index=0), _node(id="b2", order_index=1)]
    conn = FakeConn(changes=changes, hunks=hunks, nodes=nodes)
    _install(monkeypatch, conn)

    resp = client.get("/revisions/sessions/s1")
    assert resp.status_code == 200
    body = resp.json()
    # Phase 1: abstains ranked by ascending confidence (most-uncertain first).
    assert [a["id"] for a in body["phase1"]["abstains"]] == ["ab_lo", "ab_hi"]
    assert body["phase1"]["tree_anomalies"] == []
    # Phase 2: settled changes in document order (edited b1 then deleted b2).
    assert [c["id"] for c in body["phase2"]] == ["edit1", "del1"]
    assert body["phase2"][0]["change_kind"] == "edited"
    assert body["phase2"][1]["change_kind"] == "deleted"


async def _raise_session_not_found(_c: Any, sid: str) -> Any:
    raise svc.SessionNotFound(sid)


def test_review_payload_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeConn())
    monkeypatch.setattr(svc, "get_review_payload", _raise_session_not_found)
    resp = client.get("/revisions/sessions/missing")
    assert resp.status_code == 404


# --- decisions --------------------------------------------------------------


def test_confirm_match_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        changes=[_change(id="ch", proposed_parent_id="b3", match_confidence=0.4, status="pending")],
        hunks=[_hunk(id="h", change_id="ch")],
        nodes=[_node(id="b3")],
    )
    _install(monkeypatch, conn)
    resp = client.post("/revisions/changes/ch/confirm-match", json={"action": "confirm"})
    assert resp.status_code == 200
    assert any("SET node_id = $2" in sql for sql, _ in conn.executes)


def test_decide_hunk_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        changes=[_change(id="ch", node_id="b1", match_confidence=0.9, status="partial")],
        hunks=[_hunk(id="h", change_id="ch")],
    )
    _install(monkeypatch, conn)
    resp = client.post("/revisions/hunks/h/decide", json={"verdict": "accept"})
    assert resp.status_code == 200
    verdict_update = next(a for sql, a in conn.executes if "SET verdict = $2" in sql)
    assert verdict_update[1] == "accepted" and verdict_update[2] == "theirs"


def test_decide_hunk_counter_without_staged_text_422(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        changes=[_change(id="ch", node_id="b1", match_confidence=0.9)],
        hunks=[_hunk(id="h", change_id="ch", donna_counter_text=None)],
    )
    _install(monkeypatch, conn)
    resp = client.post("/revisions/hunks/h/decide", json={"verdict": "counter"})
    assert resp.status_code == 422


def test_decide_node_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        changes=[_change(id="ch", node_id=None, proposed_order_index=200, status="pending")],
        hunks=[_hunk(id="h", change_id="ch", hunk_type="insertion", original_text=None)],
    )
    _install(monkeypatch, conn)
    resp = client.post("/revisions/changes/ch/decide-node", json={"verdict": "accept"})
    assert resp.status_code == 200


# --- apply ------------------------------------------------------------------


def _apply_fixture() -> FakeConn:
    changes = [
        _change(id="edit_ok", node_id="b1", match_confidence=0.9),
        _change(id="edit_rej", node_id="b5", match_confidence=0.9),
        _change(id="new_ok", node_id=None, proposed_parent_id="p1", proposed_order_index=200),
        _change(id="new_rej", node_id=None, proposed_order_index=300),
        _change(id="del_ok", node_id="b2", match_confidence=None),
    ]
    hunks = [
        _hunk(id="h1", change_id="edit_ok", verdict="accepted", final_text="patched body"),
        _hunk(id="h2", change_id="edit_rej", verdict="rejected", final_text="orig"),
        _hunk(
            id="h3",
            change_id="new_ok",
            hunk_type="insertion",
            original_text=None,
            verdict="accepted",
            final_text="added clause",
        ),
        _hunk(
            id="h4",
            change_id="new_rej",
            hunk_type="insertion",
            original_text=None,
            proposed_text="rejected addition",
            verdict="rejected",
            final_text=None,
        ),
        _hunk(
            id="h5",
            change_id="del_ok",
            hunk_type="deletion",
            proposed_text=None,
            verdict="accepted",
            final_text=None,
        ),
    ]
    nodes = [_node(id="b1", body="orig"), _node(id="b5", body="orig")]
    return FakeConn(changes=changes, hunks=hunks, nodes=nodes)


def test_apply_maps_each_verdict_to_an_f08_path(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _apply_fixture()
    calls = _install(monkeypatch, conn)
    resp = client.post("/revisions/sessions/s1/apply")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["edits_applied"] == 1  # accepted edit patched
    assert body["nodes_inserted"] == 1  # accepted addition created
    assert body["nodes_deleted"] == 1  # accepted deletion soft-deleted
    assert body["issues_created"] == 2  # rejected edit + rejected addition
    # F08 reuse
    assert calls["edit"] == [("b1", "patched body")]
    assert len(calls["create"]) == 1 and calls["create"][0]["text"] == "added clause"
    assert calls["delete"] == ["b2"]
    # session marked completed
    assert any("status = 'completed'" in sql for sql, _ in conn.executes)
    # rejections seeded counterparty_proposed_edit issues
    assert len(conn.issues) == 2


def test_apply_blocks_when_changes_undecided(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        changes=[_change(id="ch", node_id="b1", match_confidence=0.9, status="partial")],
        hunks=[_hunk(id="h", change_id="ch")],
    )
    _install(monkeypatch, conn)
    resp = client.post("/revisions/sessions/s1/apply")
    assert resp.status_code == 409


def test_resume_partially_decided_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # A session mid-review: one hunk decided, one pending — payload reflects state.
    changes = [_change(id="ch", node_id="b1", match_confidence=0.9, hunk_count=2, status="partial")]
    hunks = [
        _hunk(id="h1", change_id="ch", verdict="accepted", final_text="theirs"),
        _hunk(id="h2", change_id="ch", verdict="pending"),
    ]
    nodes = [_node(id="b1", order_index=0)]
    conn = FakeConn(changes=changes, hunks=hunks, nodes=nodes)
    _install(monkeypatch, conn)
    resp = client.get("/revisions/sessions/s1")
    assert resp.status_code == 200
    change = resp.json()["phase2"][0]
    assert change["status"] == "partial"
    verdicts = {h["id"]: h["verdict"] for h in change["hunks"]}
    assert verdicts == {"h1": "accepted", "h2": "pending"}
