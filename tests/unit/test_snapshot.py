"""Snapshot service (F14): cut creates the row + dumps the full tree, stamps the
pending node_versions group (the F15 diff), advances a named pointer (DD-48) only
when asked, and records the audit event. Reads list newest-first and fetch-one
rehydrates the JSONB tree.

No live DB: a fake connection models the `node_versions` stamping and the
`contract_snapshots` round-trip so the grouping semantics are exercised without
Postgres. record_event is stubbed to capture the audit event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.models.snapshots import CutSnapshotRequest, SnapshotPointerTarget
from backend.services import snapshot


def _node(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="n1",
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body="Body.",
        is_deleted=False,
    )
    base.update(kw)
    return base


class _FakeConn:
    def __init__(
        self,
        tree_records: list[dict[str, Any]] | None = None,
        node_versions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tree_records = tree_records if tree_records is not None else [_node()]
        self.node_versions = node_versions if node_versions is not None else []
        self.snapshots: list[dict[str, Any]] = []
        self.pointers: list[tuple[Any, ...]] = []
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self._counter = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM nodes" in sql:
            return self.tree_records
        if "FROM contract_snapshots" in sql:
            rows = [s for s in self.snapshots if s["contract_id"] == args[0]]
            return sorted(rows, key=lambda s: s["created_at"], reverse=True)
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO contract_snapshots" in sql:
            self._counter += 1
            row = {
                "id": f"snap{self._counter}",
                "contract_id": args[0],
                "label": args[1],
                "tree": args[2],  # JSON string, as asyncpg would persist/return
                "origin": args[3],
                "created_at": datetime(2026, 6, 24, 12, self._counter, tzinfo=UTC),
            }
            self.snapshots.append(row)
            return row
        if "FROM contract_snapshots\nWHERE id" in sql:
            return next((s for s in self.snapshots if s["id"] == args[0]), None)
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append((sql, args))
        if "UPDATE node_versions" in sql:
            stamped = 0
            for v in self.node_versions:
                if v["snapshot_id"] is None:
                    v["snapshot_id"] = args[0]
                    stamped += 1
            return f"UPDATE {stamped}"
        if "snapshot_pointers" in sql:
            self.pointers.append(args)
            return "INSERT 0 1"
        return "EXECUTE"


def _capture_record(captured: dict[str, Any]) -> Any:
    async def _record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return StoredAuditEvent(
            id="a1",
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            actor=event.actor,
            payload=event.payload,
            created_at=datetime(2026, 6, 24, tzinfo=UTC),
        )

    return _record


async def test_cut_creates_row_and_stamps_pending_versions(monkeypatch: Any) -> None:
    monkeypatch.setattr(snapshot, "record_event", _capture_record({}))
    versions = [{"node_id": "n1", "snapshot_id": None}, {"node_id": "n2", "snapshot_id": None}]
    conn = _FakeConn(node_versions=versions)

    result = await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest(label="v1"))

    assert result.id == "snap1"
    assert result.contract_id == "c1"
    assert result.label == "v1"
    assert result.origin == "export"  # F14 default
    stamp_sql, stamp_args = next(e for e in conn.executes if "UPDATE node_versions" in e[0])
    assert "snapshot_id IS NULL" in stamp_sql  # only the pending group
    assert stamp_args == ("snap1", "c1")
    assert all(v["snapshot_id"] == "snap1" for v in versions)  # the F15 diff group


async def test_second_snapshot_only_stamps_versions_since_first(monkeypatch: Any) -> None:
    monkeypatch.setattr(snapshot, "record_event", _capture_record({}))
    versions = [{"node_id": "n1", "snapshot_id": None}]
    conn = _FakeConn(node_versions=versions)

    await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest())
    assert versions[0]["snapshot_id"] == "snap1"

    versions.append({"node_id": "n2", "snapshot_id": None})  # edit after the first cut
    await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest())

    assert versions[0]["snapshot_id"] == "snap1"  # untouched by the second cut
    assert versions[1]["snapshot_id"] == "snap2"  # only the new pending row regrouped


async def test_cut_records_snapshot_cut_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(snapshot, "record_event", _capture_record(captured))
    conn = _FakeConn()

    await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest(origin="manual"))

    event = captured["event"]
    assert event.event_type == "snapshot_cut"
    assert event.entity_type == "contract"
    assert event.entity_id == "c1"
    assert event.actor == "operator"  # audit actor = settings.operator_actor
    assert event.payload == {"snapshot_id": "snap1", "origin": "manual"}


async def test_cut_advances_named_pointer_when_requested(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(snapshot, "record_event", _capture_record(captured))
    conn = _FakeConn()
    request = CutSnapshotRequest(
        pointer=SnapshotPointerTarget(party="counterparty", direction="shared")
    )

    await snapshot.cut_snapshot(conn, "c1", request)

    assert conn.pointers == [("c1", "counterparty", "shared", "snap1")]  # send-to-counterparty
    assert captured["event"].payload["pointer"] == {
        "party": "counterparty",
        "direction": "shared",
    }


async def test_cut_sets_no_pointer_by_default(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(snapshot, "record_event", _capture_record(captured))
    conn = _FakeConn()

    await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest())

    assert conn.pointers == []  # copy-only / internal export sets none (DD-48)
    assert "pointer" not in captured["event"].payload


async def test_tree_dump_includes_deleted_nodes(monkeypatch: Any) -> None:
    monkeypatch.setattr(snapshot, "record_event", _capture_record({}))
    records = [_node(id="n1"), _node(id="n2", parent_id="n1", order_index=200, is_deleted=True)]
    conn = _FakeConn(tree_records=records)

    result = await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest())

    assert result.tree is not None
    assert [n.id for n in result.tree] == ["n1", "n2"]
    assert result.tree[1].is_deleted is True  # deletions retained for structural diff


async def test_list_snapshots_most_recent_first(monkeypatch: Any) -> None:
    monkeypatch.setattr(snapshot, "record_event", _capture_record({}))
    conn = _FakeConn()
    await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest(label="v1"))
    await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest(label="v2"))

    listed = await snapshot.list_snapshots(conn, "c1")

    assert [s.label for s in listed] == ["v2", "v1"]
    assert all(s.tree is None for s in listed)  # list omits the heavy tree


async def test_get_snapshot_rehydrates_tree(monkeypatch: Any) -> None:
    monkeypatch.setattr(snapshot, "record_event", _capture_record({}))
    conn = _FakeConn(tree_records=[_node(id="n1", body="Clause text.")])
    cut = await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest())

    fetched = await snapshot.get_snapshot(conn, cut.id)

    assert fetched is not None
    assert fetched.id == "snap1"
    assert fetched.tree is not None
    assert fetched.tree[0].body == "Clause text."  # decoded from the JSONB string


async def test_get_snapshot_missing_returns_none() -> None:
    conn = _FakeConn()
    assert await snapshot.get_snapshot(conn, "nope") is None


@pytest.mark.parametrize("origin", ["export", "as_received", "manual"])
async def test_cut_passes_origin_through(monkeypatch: Any, origin: str) -> None:
    monkeypatch.setattr(snapshot, "record_event", _capture_record({}))
    conn = _FakeConn()

    result = await snapshot.cut_snapshot(conn, "c1", CutSnapshotRequest(origin=origin))  # type: ignore[arg-type]

    assert result.origin == origin
    insert_args = conn.snapshots[0]["origin"]
    assert insert_args == origin
