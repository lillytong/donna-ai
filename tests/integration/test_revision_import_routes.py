"""F03b route: clean-diff revision import end-to-end with parsing + matcher +
snapshot mocked. Asserts the HTTP contract, that all four matcher buckets stage
change+hunk rows, the as_received snapshot + received pointer, and the guard 409/422
paths. DB faked through the real service (no live database)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api import revision_import as api
from backend.models.contract_tree import ParsedTree, TreeNode
from backend.models.revision_match import Abstention, MatchedPair, RevisionMatchResult
from backend.models.snapshots import SnapshotNode, StoredSnapshot
from backend.services.import_ import revision_import as svc
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(api.router)
client = TestClient(app)

_NOW = datetime(2026, 6, 25, tzinfo=UTC)
_DOCX = b"PK\x03\x04clean-stub"


def _tn(index: int, order: int, text: str) -> TreeNode:
    return TreeNode(
        index=index, parent_index=None, depth=0, order_index=order, kind="prose", text=text
    )


def _sn(node_id: str, order: int, body: str) -> SnapshotNode:
    return SnapshotNode(
        id=node_id,
        parent_id=None,
        order_index=order,
        content_type="prose",
        heading=None,
        body=body,
        is_deleted=False,
    )


_INCOMING = ParsedTree(
    nodes=[
        _tn(0, 100, "the price is 20 dollars"),  # edited match -> b1
        _tn(1, 200, "brand new clause"),  # new
        _tn(2, 300, "abstain incoming body"),  # abstain -> b3
    ]
)

_BASELINE_TREE = [
    _sn("b1", 100, "the price is 10 dollars"),
    _sn("b2", 200, "deleted clause text"),
    _sn("b3", 300, "abstain baseline body"),
]

_MATCH = RevisionMatchResult(
    matches=[MatchedPair(incoming_index=0, baseline_id="b1", confidence=0.91)],
    new=[1],
    deleted=["b2"],
    abstains=[Abstention(incoming_index=2, best_baseline_id="b3", confidence=0.5)],
)


class _FakeConn:
    def __init__(self, *, open_session: bool, has_baseline: bool) -> None:
        self._open_session = open_session
        self._has_baseline = has_baseline
        self.changes: list[tuple[Any, ...]] = []
        self.hunks: list[tuple[Any, ...]] = []
        self.session_update: int | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "status = 'reviewing'" in sql:
            return 1 if self._open_session else None
        if "direction = 'shared'" in sql:
            return "snap-baseline" if self._has_baseline else None
        if "count(*) FROM contract_snapshots" in sql:
            return 2
        if "INSERT INTO counterparty_revision_sessions" in sql:
            return "sess-1"
        if "INSERT INTO counterparty_revision_changes" in sql:
            self.changes.append(args)
            return f"chg-{len(self.changes)}"
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        if "INSERT INTO counterparty_revision_hunks" in sql:
            self.hunks.append(args)
        if "UPDATE counterparty_revision_sessions SET changes_count" in sql:
            self.session_update = args[0]
        return "OK"


def _install(
    monkeypatch: pytest.MonkeyPatch, *, open_session: bool = False, has_baseline: bool = True
) -> _FakeConn:
    conn = _FakeConn(open_session=open_session, has_baseline=has_baseline)
    pointer_set: dict[str, Any] = {}

    @asynccontextmanager
    async def _fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    async def _fake_get_snapshot(_conn: Any, _sid: str) -> StoredSnapshot:
        return StoredSnapshot(
            id="snap-baseline",
            contract_id="c1",
            label=None,
            origin="export",
            created_at=_NOW,
            tree=_BASELINE_TREE,
        )

    async def _fake_snapshot_tree(_conn: Any, _cid: str, _tree: Any, **kw: Any) -> StoredSnapshot:
        pointer_set["pointer"] = kw.get("pointer")
        pointer_set["origin"] = kw.get("origin")
        return StoredSnapshot(
            id="snap-received",
            contract_id="c1",
            label=None,
            origin="as_received",
            created_at=_NOW,
            tree=None,
        )

    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    # F03c auto-run fires recommend_on_import as a FastAPI BackgroundTask, which TestClient
    # executes after the response — stub it to a sync recorder so the test never hits the real
    # recommend engine / DB. The recorder captures the scheduled (session_id, changes_count).
    auto_runs: list[tuple[str, int]] = []

    def _fake_recommend_on_import(session_id: str, changes_count: int) -> None:
        auto_runs.append((session_id, changes_count))

    monkeypatch.setattr(api, "acquire", _fake_acquire)
    monkeypatch.setattr(api, "recommend_on_import", _fake_recommend_on_import)
    monkeypatch.setattr(svc, "count_tracked_changes", lambda _p: (0, 0))
    monkeypatch.setattr(svc, "read_docx", lambda _p: object())
    monkeypatch.setattr(svc, "build_tree", lambda _doc: _INCOMING)
    monkeypatch.setattr(svc, "match_revision", lambda _b, _i: _MATCH)
    monkeypatch.setattr(svc, "get_snapshot", _fake_get_snapshot)
    monkeypatch.setattr(svc, "snapshot_tree", _fake_snapshot_tree)
    monkeypatch.setattr(svc, "record_event", _noop)
    conn.pointer_set = pointer_set  # type: ignore[attr-defined]
    conn.auto_runs = auto_runs  # type: ignore[attr-defined]
    return conn


def test_clean_import_stages_all_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _install(monkeypatch)

    resp = client.post(
        "/contracts/c1/revisions/import?source=counterparty&filename=v4.docx", content=_DOCX
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["parse_path"] == "clean_diff"
    assert body["baseline_snapshot_id"] == "snap-baseline"
    assert body["as_received_snapshot_id"] == "snap-received"
    assert body["received_pointer_party"] == "counterparty"
    assert body["version"] == 3  # snapshot_count(2) + 1
    assert body["edited_matches"] == 1
    assert body["unchanged_matches"] == 0
    assert body["new"] == 1
    assert body["deleted"] == 1
    assert body["abstains"] == 1
    assert body["changes_count"] == 4
    assert body["hunk_count"] >= 4

    # 4 change rows: one per bucket. (node_id, proposed_parent_id, proposed_order_index,
    # match_confidence) sit at args[1..4].
    assert len(conn.changes) == 4
    edited, new, deleted, abstain = conn.changes
    assert edited[1] == "b1" and edited[4] == 0.91  # matched node id + confidence
    assert new[1] is None and new[3] == 200  # new: null node, order_index carried
    assert deleted[1] == "b2"  # deletion anchored to baseline id
    assert abstain[1] is None and abstain[2] == "b3" and abstain[4] == 0.5  # provisional + conf
    assert conn.session_update == 4

    # as_received snapshot advanced the received pointer for the party.
    pointer = conn.pointer_set["pointer"]  # type: ignore[attr-defined]
    assert pointer.party == "counterparty" and pointer.direction == "received"
    assert conn.pointer_set["origin"] == "as_received"  # type: ignore[attr-defined]

    # F03c auto-run: exactly one recommend_on_import scheduled post-commit, carrying the new
    # session id + staged-change count (the cost guard / recommend engine live in the task).
    assert conn.auto_runs == [(body["session_id"], 4)]  # type: ignore[attr-defined]


def test_legal_source_maps_to_legal_team(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _install(monkeypatch)
    resp = client.post("/contracts/c1/revisions/import?source=legal", content=_DOCX)
    assert resp.status_code == 200
    assert resp.json()["received_pointer_party"] == "legal_team"
    assert resp.json()["source"] == "legal_team"
    pointer = conn.pointer_set["pointer"]  # type: ignore[attr-defined]
    assert pointer.party == "legal_team"


def test_second_import_blocked_by_open_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, open_session=True)
    resp = client.post("/contracts/c1/revisions/import?source=counterparty", content=_DOCX)
    assert resp.status_code == 409


def test_no_baseline_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, has_baseline=False)
    resp = client.post("/contracts/c1/revisions/import?source=counterparty", content=_DOCX)
    assert resp.status_code == 409


def test_tracked_changes_upload_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    monkeypatch.setattr(svc, "count_tracked_changes", lambda _p: (5, 2))
    resp = client.post("/contracts/c1/revisions/import?source=counterparty", content=_DOCX)
    assert resp.status_code == 422


def test_non_docx_body_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    resp = client.post("/contracts/c1/revisions/import?source=counterparty", content=b"not docx")
    assert resp.status_code == 400
