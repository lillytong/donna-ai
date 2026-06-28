"""Version-delete service (DD-85 / DD-87): position-typed wipe + latest-delete
rollback + FK-correct cascade, plus the persisted-numbering / badge-max invariants.

No live DB: a stateful fake conn models the snapshot store, the named pointers, the
live node rows, and the pending node_versions, mutating them as the service's SQL
runs — so the cascade ORDER and the rollback restore are exercised without Postgres.
`get_snapshot` reads the same fake store; `record_event` is stubbed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from backend.models.snapshots import SnapshotNode, StoredSnapshot
from backend.services import lineage
from backend.services import version_delete as vd

_BASE = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _node(node_id: str, body: str, *, is_deleted: bool = False) -> SnapshotNode:
    return SnapshotNode(
        id=node_id,
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body=body,
        is_deleted=is_deleted,
    )


class _FakeConn:
    """A stateful fake: snapshots (with frozen trees), pointers, live nodes, pending
    node_versions, baselines, and issue snapshot refs — mutated by the service SQL."""

    def __init__(
        self,
        *,
        snapshots: dict[str, dict[str, Any]],
        pointers: list[dict[str, str]] | None = None,
        nodes: list[dict[str, Any]] | None = None,
        pending_versions: int = 0,
        sessions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.pointers = pointers if pointers is not None else []
        self.nodes = nodes if nodes is not None else []
        self.pending_versions = pending_versions
        self.sessions = sessions if sessions is not None else []
        self.target_versions_deleted = False
        self.issues_opened_nulled = False
        self.issues_resolved_nulled = False
        self.discarded_sessions: list[str] = []
        self.discarded_hunks: list[str] = []
        self.discarded_changes: list[str] = []
        self.discarded_overrides: list[str] = []
        self.issue_session_nulled: list[str] = []
        self.executes: list[str] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "MAX(version_number)" in sql:
            cid = args[0]
            versions = [
                s["version_number"] for s in self.snapshots.values() if s["contract_id"] == cid
            ]
            return max(versions) if versions else None
        return None

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM contract_snapshots\nWHERE id" in sql:  # get_snapshot
            return self.snapshots.get(args[0])
        if "version_number <" in sql:  # predecessor
            cid, target_v = args[0], args[1]
            candidates = [
                s
                for s in self.snapshots.values()
                if s["contract_id"] == cid and s["version_number"] < target_v
            ]
            if not candidates:
                return None
            best = max(candidates, key=lambda s: s["version_number"])
            return {
                "id": best["id"],
                "version_number": best["version_number"],
                "created_at": best["created_at"],
            }
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM snapshot_pointers" in sql:
            sid = args[1]
            return [
                {"party": p["party"], "direction": p["direction"]}
                for p in self.pointers
                if p["snapshot_id"] == sid
            ]
        if "FROM counterparty_revision_sessions" in sql:  # _DEPENDENT_SESSIONS
            cid, target = args[0], args[1]
            return [
                {
                    "id": s["id"],
                    "changes_count": s["changes_count"],
                    "changes_reviewed_count": s["changes_reviewed_count"],
                }
                for s in self.sessions
                if s["contract_id"] == cid
                and s["status"] == "reviewing"
                and target in (s.get("baseline_snapshot_id"), s.get("as_received_snapshot_id"))
            ]
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executes.append(sql)
        if "UPDATE nodes" in sql and "SET parent_id" in sql:
            node_id = args[7]
            for n in self.nodes:
                if n["id"] == node_id:
                    n["body"] = args[4]
                    n["is_deleted"] = args[5]
                    n["updated_at"] = args[6]
            return "UPDATE 1"
        if "SET is_deleted = true" in sql:  # soft-delete added-after nodes
            keep = set(args[2])
            for n in self.nodes:
                if n["id"] not in keep and not n["is_deleted"]:
                    n["is_deleted"] = True
                    n["updated_at"] = args[0]
            return "UPDATE 1"
        if "DELETE FROM node_versions" in sql and "snapshot_id IS NULL" in sql:
            self.pending_versions = 0
            return "DELETE 1"
        if "DELETE FROM node_versions" in sql:
            self.target_versions_deleted = True
            return "DELETE 1"
        if "DELETE FROM snapshot_pointers" in sql:
            sid = args[1]
            self.pointers = [p for p in self.pointers if p["snapshot_id"] != sid]
            return "DELETE 1"
        if "opened_in_snapshot_id = NULL" in sql:
            self.issues_opened_nulled = True
            return "UPDATE 1"
        if "resolved_in_snapshot_id = NULL" in sql:
            self.issues_resolved_nulled = True
            return "UPDATE 1"
        if "DELETE FROM counterparty_revision_hunks" in sql:
            self.discarded_hunks.append(args[0])
            return "DELETE 1"
        if "DELETE FROM counterparty_revision_node_overrides" in sql:
            self.discarded_overrides.append(args[0])
            return "DELETE 1"
        if "counterparty_revision_session_id = NULL" in sql:
            self.issue_session_nulled.append(args[0])
            return "UPDATE 1"
        if "DELETE FROM counterparty_revision_changes" in sql:
            self.discarded_changes.append(args[0])
            return "DELETE 1"
        if "DELETE FROM counterparty_revision_sessions" in sql:
            self.discarded_sessions.append(args[0])
            self.sessions = [s for s in self.sessions if s["id"] != args[0]]
            return "DELETE 1"
        if "DELETE FROM contract_snapshots" in sql:
            self.snapshots.pop(args[0], None)
            return "DELETE 1"
        return "EXECUTE"


def _snap_row(sid: str, version: int, tree: list[SnapshotNode]) -> dict[str, Any]:
    return {
        "id": sid,
        "contract_id": "c1",
        "label": None,
        "origin": "export",
        "created_at": _BASE + timedelta(minutes=version),
        "version_number": version,
        "tree": tree,
    }


@pytest.fixture(autouse=True)
def _stub_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_conn: Any, _event: Any) -> None:
        return None

    monkeypatch.setattr(vd, "record_event", _noop)


# get_snapshot is imported into the service; patch it to read the fake store, but the
# fake also answers the raw _FETCH_SNAPSHOT for completeness. Use the real one.
async def _real_get_snapshot(conn: Any, snapshot_id: str) -> StoredSnapshot | None:
    row = conn.snapshots.get(snapshot_id)
    if row is None:
        return None
    return StoredSnapshot(
        id=row["id"],
        contract_id=row["contract_id"],
        label=row["label"],
        origin=row["origin"],
        created_at=row["created_at"],
        version_number=row["version_number"],
        tree=row["tree"],
    )


@pytest.fixture(autouse=True)
def _patch_get_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vd, "get_snapshot", _real_get_snapshot)


# --- 404 / not-this-contract ------------------------------------------------


async def test_missing_snapshot_returns_none() -> None:
    conn = _FakeConn(snapshots={})
    assert await vd.delete_version(conn, "c1", "nope", confirm=True) is None


async def test_cross_contract_returns_none() -> None:
    row = _snap_row("s1", 1, [_node("n1", "x")])
    row["contract_id"] = "other"
    conn = _FakeConn(snapshots={"s1": row})
    assert await vd.delete_version(conn, "c1", "s1", confirm=True) is None


# --- preview vs execute -----------------------------------------------------


async def test_preview_does_not_mutate() -> None:
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "old")]),
        "s2": _snap_row("s2", 2, [_node("n1", "new")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        pointers=[{"party": "counterparty", "direction": "shared", "snapshot_id": "s2"}],
        nodes=[{"id": "n1", "contract_id": "c1", "body": "new", "is_deleted": False}],
        pending_versions=3,
    )
    res = await vd.delete_version(conn, "c1", "s2", confirm=False)

    assert res is not None
    assert res.deleted is False
    assert res.is_latest is True
    assert res.will_rollback is True
    assert res.rollback_to_version == 1
    assert res.rolled_back is False
    assert res.pointers_removed == []
    assert res.sent_record is not None and res.sent_record.party == "counterparty"
    assert any("Rollback is destructive" in w for w in res.warnings)
    assert any("erases the record of what was sent" in w for w in res.warnings)
    # nothing changed
    assert "s2" in conn.snapshots
    assert conn.pending_versions == 3
    assert conn.executes == []


# --- latest-delete rollback -------------------------------------------------


async def test_latest_delete_rolls_back_content_pointer_and_pending() -> None:
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "old")]),
        "s2": _snap_row("s2", 2, [_node("n1", "new"), _node("n2", "added")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        pointers=[{"party": "counterparty", "direction": "shared", "snapshot_id": "s2"}],
        nodes=[
            {"id": "n1", "contract_id": "c1", "body": "new", "is_deleted": False},
            {"id": "n2", "contract_id": "c1", "body": "added", "is_deleted": False},
        ],
        pending_versions=2,
    )
    res = await vd.delete_version(conn, "c1", "s2", confirm=True)

    assert res is not None
    assert res.deleted is True
    assert res.rolled_back is True
    assert res.pointers_removed == ["counterparty"]
    # content restored to the predecessor (n1 -> "old"); n2 (added after v1) soft-deleted
    n1 = next(n for n in conn.nodes if n["id"] == "n1")
    n2 = next(n for n in conn.nodes if n["id"] == "n2")
    assert n1["body"] == "old" and n1["is_deleted"] is False
    assert n2["is_deleted"] is True
    # restored content stamped with the predecessor's created_at, not now()
    assert n1["updated_at"] == snaps_created("s1")
    # pending edits discarded; the tag is DROPPED (not rolled to s1); target snapshot gone
    assert conn.pending_versions == 0
    assert conn.pointers == []
    assert "s2" not in conn.snapshots
    assert conn.target_versions_deleted is True
    assert conn.issues_opened_nulled and conn.issues_resolved_nulled


def snaps_created(sid: str) -> datetime:
    # predecessor s1 has version 1 → created_at = _BASE + 1 min (mirrors _snap_row)
    return _BASE + timedelta(minutes=1)


# --- middle-delete ----------------------------------------------------------


async def test_middle_delete_leaves_working_copy_and_preserves_gap() -> None:
    # versions 1,2,3 present; delete the middle (v2). working copy untouched, the tag on
    # v2 is DROPPED (not rolled to v1), gap preserved (no renumber — numbering is stored).
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "v1")]),
        "s2": _snap_row("s2", 2, [_node("n1", "v2")]),
        "s3": _snap_row("s3", 3, [_node("n1", "v3")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        pointers=[{"party": "legal_team", "direction": "shared", "snapshot_id": "s2"}],
        nodes=[{"id": "n1", "contract_id": "c1", "body": "current", "is_deleted": False}],
        pending_versions=5,
    )
    res = await vd.delete_version(conn, "c1", "s2", confirm=True)

    assert res is not None
    assert res.deleted is True
    assert res.is_latest is False
    assert res.will_rollback is False
    assert res.rolled_back is False
    assert res.pointers_removed == ["legal"]
    # working copy untouched (no restore ran), pending edits NOT discarded
    assert next(n for n in conn.nodes if n["id"] == "n1")["body"] == "current"
    assert conn.pending_versions == 5
    # the v2 tag is DROPPED (predecessor v1 does NOT inherit it); v2 wiped; v1,v3 remain (gap)
    assert conn.pointers == []
    assert "s2" not in conn.snapshots
    assert set(conn.snapshots) == {"s1", "s3"}


# --- tag-drop: deleting the tagged version REMOVES the tag (DD-87 §4(b) amended) ---


async def test_delete_latest_with_received_tag_drops_it_not_rolled_to_predecessor() -> None:
    # Operator's report: v4 tagged "received from counterparty"; deleting v4 must REMOVE
    # the tag — v3 must NOT become "received from counterparty".
    snaps = {
        "s3": _snap_row("s3", 3, [_node("n1", "v3")]),
        "s4": _snap_row("s4", 4, [_node("n1", "v4")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        pointers=[{"party": "counterparty", "direction": "received", "snapshot_id": "s4"}],
        nodes=[{"id": "n1", "contract_id": "c1", "body": "v4", "is_deleted": False}],
    )
    res = await vd.delete_version(conn, "c1", "s4", confirm=True)

    assert res is not None and res.deleted is True
    assert res.pointers_removed == ["counterparty"]
    # tag gone; the predecessor s3 did NOT inherit it
    assert conn.pointers == []
    assert "s4" not in conn.snapshots


async def test_delete_drops_both_received_and_shared_predecessor_does_not_inherit() -> None:
    # v1,v2,v3; v2 carries BOTH a `received` (counterparty) and a `shared` (legal) tag.
    # Deleting v2 DROPS both — the predecessor v1 must NOT inherit either (no roll-back).
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "v1")]),
        "s2": _snap_row("s2", 2, [_node("n1", "v2")]),
        "s3": _snap_row("s3", 3, [_node("n1", "v3")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        pointers=[
            {"party": "counterparty", "direction": "received", "snapshot_id": "s2"},
            {"party": "legal_team", "direction": "shared", "snapshot_id": "s2"},
        ],
        nodes=[{"id": "n1", "contract_id": "c1", "body": "current", "is_deleted": False}],
    )
    res = await vd.delete_version(conn, "c1", "s2", confirm=True)

    assert res is not None and res.deleted is True
    assert res.pointers_removed == ["counterparty", "legal"]
    # both tags gone; nothing moved to the predecessor s1
    assert conn.pointers == []
    assert set(conn.snapshots) == {"s1", "s3"}


# --- only / last-remaining --------------------------------------------------


async def test_only_delete_removes_snapshot_and_pointer_rows() -> None:
    snaps = {"s1": _snap_row("s1", 1, [_node("n1", "v1")])}
    conn = _FakeConn(
        snapshots=snaps,
        pointers=[{"party": "counterparty", "direction": "shared", "snapshot_id": "s1"}],
        nodes=[{"id": "n1", "contract_id": "c1", "body": "current", "is_deleted": False}],
        pending_versions=1,
    )
    res = await vd.delete_version(conn, "c1", "s1", confirm=True)

    assert res is not None
    assert res.deleted is True
    assert res.is_latest is True
    assert res.will_rollback is False  # no predecessor → no rollback
    assert res.rolled_back is False
    assert res.rollback_to_version is None
    # no snapshots left → pointer rows deleted (not moved); working copy untouched
    assert conn.snapshots == {}
    assert conn.pointers == []
    assert next(n for n in conn.nodes if n["id"] == "n1")["body"] == "current"
    assert conn.pending_versions == 1  # only/middle never discards pending edits


# --- DD-94: cascade-discard the OPEN review the deleted version anchors -------


def _session(
    sid: str, *, baseline: str | None = None, as_received: str | None = None
) -> dict[str, Any]:
    return {
        "id": sid,
        "contract_id": "c1",
        "status": "reviewing",
        "baseline_snapshot_id": baseline,
        "as_received_snapshot_id": as_received,
        "changes_count": 7,
        "changes_reviewed_count": 3,
    }


async def test_delete_baseline_discards_open_review() -> None:
    # v1 is the baseline an OPEN review diffs against; v2 the working copy. Deleting v1
    # (middle, here the only-other) must cascade-discard the session, children-first.
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "v1")]),
        "s2": _snap_row("s2", 2, [_node("n1", "v2")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        nodes=[{"id": "n1", "contract_id": "c1", "body": "v2", "is_deleted": False}],
        sessions=[_session("sess1", baseline="s1")],
    )
    res = await vd.delete_version(conn, "c1", "s1", confirm=True)

    assert res is not None and res.deleted is True
    assert conn.discarded_sessions == ["sess1"]
    assert conn.discarded_hunks == ["sess1"]
    assert conn.discarded_changes == ["sess1"]
    assert conn.discarded_overrides == ["sess1"]
    assert conn.issue_session_nulled == ["sess1"]
    # session row gone BEFORE the snapshot delete (FK order); snapshot wiped.
    assert "s1" not in conn.snapshots
    # children discarded before the parent session (FK-correct order).
    hunk_i = next(
        i for i, s in enumerate(conn.executes) if "DELETE FROM counterparty_revision_hunks" in s
    )
    change_i = next(
        i for i, s in enumerate(conn.executes) if "DELETE FROM counterparty_revision_changes" in s
    )
    sess_i = next(
        i for i, s in enumerate(conn.executes) if "DELETE FROM counterparty_revision_sessions" in s
    )
    assert hunk_i < change_i < sess_i


async def test_delete_as_received_discards_open_review() -> None:
    # The target is the as_received (received) snapshot the review reviews — also discards.
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "v1")]),
        "s2": _snap_row("s2", 2, [_node("n1", "received")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        nodes=[{"id": "n1", "contract_id": "c1", "body": "v1", "is_deleted": False}],
        sessions=[_session("sess1", baseline="s1", as_received="s2")],
    )
    res = await vd.delete_version(conn, "c1", "s2", confirm=True)

    assert res is not None and res.deleted is True
    assert conn.discarded_sessions == ["sess1"]
    assert "s2" not in conn.snapshots


async def test_preview_reports_review_discard_with_counts() -> None:
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "v1")]),
        "s2": _snap_row("s2", 2, [_node("n1", "received")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        nodes=[{"id": "n1", "contract_id": "c1", "body": "v1", "is_deleted": False}],
        sessions=[_session("sess1", baseline="s1", as_received="s2")],
    )
    res = await vd.delete_version(conn, "c1", "s2", confirm=False)

    assert res is not None and res.deleted is False
    assert res.review_discard is not None
    assert res.review_discard.changes_count == 7
    assert res.review_discard.reviewed == 3
    assert any("discards the in-progress revision review" in w for w in res.warnings)
    # preview mutates nothing.
    assert conn.discarded_sessions == [] and conn.executes == []
    assert "s2" in conn.snapshots


async def test_delete_unrelated_to_review_leaves_session_untouched() -> None:
    # An OPEN review on s1/s2; deleting an UNRELATED version s3 must not touch it.
    snaps = {
        "s1": _snap_row("s1", 1, [_node("n1", "v1")]),
        "s2": _snap_row("s2", 2, [_node("n1", "received")]),
        "s3": _snap_row("s3", 3, [_node("n1", "v3")]),
    }
    conn = _FakeConn(
        snapshots=snaps,
        nodes=[{"id": "n1", "contract_id": "c1", "body": "v3", "is_deleted": False}],
        sessions=[_session("sess1", baseline="s1", as_received="s2")],
    )
    res = await vd.delete_version(conn, "c1", "s3", confirm=True)

    assert res is not None and res.deleted is True
    assert res.review_discard is None
    assert conn.discarded_sessions == []
    assert [s["id"] for s in conn.sessions] == ["sess1"]


# --- badge v-number = MAX(version_number), not count, after a delete ---------


def _stored(sid: str, version: int) -> StoredSnapshot:
    return StoredSnapshot(
        id=sid,
        contract_id="c1",
        label=None,
        origin="export",
        created_at=_BASE + timedelta(minutes=version),
        tree=None,
        version_number=version,
    )


def test_badge_version_is_max_version_number_after_gap() -> None:
    # lineage v1,v3,v4 (v2 was deleted) → 3 snapshots but the badge must read v4.
    snaps = [_stored("s1", 1), _stored("s3", 3), _stored("s4", 4)]
    ptrs = [
        SimpleNamespace(party="counterparty", direction="shared", snapshot_id="s4"),
    ]
    badge = lineage.derive_status(SimpleNamespace(status="under negotiation"), snaps, ptrs)
    assert badge.label == "Sent to counterparty"
    assert badge.version == 4  # MAX(version_number), NOT the count (3)


# --- INSERT mints MAX+1 (persisted, never reused) ---------------------------


def test_insert_snapshot_sql_mints_max_plus_one() -> None:
    from backend.services.snapshot import _INSERT_SNAPSHOT

    # the numbering is encoded in the INSERT itself (atomic, per contract).
    assert "COALESCE(MAX(version_number), 0) + 1" in _INSERT_SNAPSHOT
    assert "RETURNING" in _INSERT_SNAPSHOT and "version_number" in _INSERT_SNAPSHOT
