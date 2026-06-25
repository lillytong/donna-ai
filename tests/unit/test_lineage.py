"""Lifecycle-badge resolver + lineage assembly (F27, DD-75).

`derive_status` is pure (no I/O) — tested directly against the DD-75 table: all five
rules, the post-send-edit edge, no-snapshot→no-version, and the both-pointers label.
The set-based list badge, the ROW_NUMBER version numbering, the lineage timeline
assembly, and the read-only snapshot render adapter run against a fake conn (the
snapshot/mark-sent test convention) so the SQL grouping is exercised without Postgres.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from backend.models.lineage import PointerRow
from backend.models.snapshots import SnapshotNode, StoredSnapshot
from backend.services import lineage, snapshot

_BASE = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _contract(status: str = "drafting") -> Any:
    return SimpleNamespace(status=status)


def _snap(snap_id: str, n: int, origin: str = "export") -> StoredSnapshot:
    return StoredSnapshot(
        id=snap_id,
        contract_id="c1",
        label=None,
        origin=origin,
        created_at=_BASE + timedelta(minutes=n),
        tree=None,
    )


def _ptr(party: str, direction: str, snapshot_id: str) -> PointerRow:
    return PointerRow(party=party, direction=direction, snapshot_id=snapshot_id)


# --- derive_status: the DD-75 table, FIRST MATCH WINS -----------------------


def test_rule1_signed_wins_with_version() -> None:
    snaps = [_snap("s1", 1)]
    ptrs = [_ptr("counterparty", "shared", "s1")]
    badge = lineage.derive_status(_contract("signed"), snaps, ptrs)
    assert badge.label == "Signed"
    assert badge.version == 1
    assert badge.marker is False


def test_rule2_no_snapshot_is_working_copy_no_version() -> None:
    badge = lineage.derive_status(_contract("drafting"), [], [])
    assert badge.label == "Working copy"
    assert badge.version is None
    assert badge.marker is False


def test_rule3_receive_not_engaged_is_your_move() -> None:
    # Phase-2 shape: LBE carries a `received` pointer, no divergence.
    snaps = [_snap("s1", 1, origin="as_received")]
    ptrs = [_ptr("counterparty", "received", "s1")]
    badge = lineage.derive_status(_contract("drafting"), snaps, ptrs, diverged=False)
    assert badge.label == "Your move"
    assert badge.version == 1
    assert badge.party == "counterparty"


def test_rule4_receive_engaged_falls_back_to_working_copy() -> None:
    snaps = [_snap("s1", 1, origin="as_received")]
    ptrs = [_ptr("counterparty", "received", "s1")]
    badge = lineage.derive_status(_contract("drafting"), snaps, ptrs, diverged=True)
    assert badge.label == "Working copy"
    assert badge.version is None  # the working copy is never numbered
    assert badge.based_on == "v1 received from counterparty"


def test_rule5_send_to_counterparty() -> None:
    snaps = [_snap("s1", 1)]
    ptrs = [_ptr("counterparty", "shared", "s1")]
    badge = lineage.derive_status(_contract("under negotiation"), snaps, ptrs)
    assert badge.label == "Sent to counterparty"
    assert badge.version == 1
    assert badge.marker is False
    assert badge.party == "counterparty"


def test_rule5_send_to_legal() -> None:
    snaps = [_snap("s1", 1)]
    ptrs = [_ptr("legal_team", "shared", "s1")]
    badge = lineage.derive_status(_contract(), snaps, ptrs)
    assert badge.label == "Sent to legal"
    assert badge.party == "legal"


def test_rule5_both_pointers_one_snapshot() -> None:
    snaps = [_snap("s1", 1)]
    ptrs = [_ptr("counterparty", "shared", "s1"), _ptr("legal_team", "shared", "s1")]
    badge = lineage.derive_status(_contract(), snaps, ptrs)
    assert badge.label == "Sent to counterparty & legal"
    assert badge.party == "both"
    assert badge.version == 1


def test_post_send_edit_keeps_sent_and_sets_marker() -> None:
    # DD-70 §5: diverged after a send → STAY Sent + raise the marker, never revert.
    snaps = [_snap("s1", 1)]
    ptrs = [_ptr("counterparty", "shared", "s1")]
    badge = lineage.derive_status(_contract(), snaps, ptrs, diverged=True)
    assert badge.label == "Sent to counterparty"
    assert badge.version == 1
    assert badge.marker is True


def test_lbe_is_most_recent_snapshot() -> None:
    # Two sends; the legal send is newer → LBE is the legal one (v2).
    snaps = [_snap("s1", 1), _snap("s2", 2)]
    ptrs = [_ptr("counterparty", "shared", "s1"), _ptr("legal_team", "shared", "s2")]
    badge = lineage.derive_status(_contract(), snaps, ptrs)
    assert badge.label == "Sent to legal"
    assert badge.version == 2  # latest snapshot's position


def test_signed_with_no_snapshot_has_no_version() -> None:
    badge = lineage.derive_status(_contract("signed"), [], [])
    assert badge.label == "Signed"
    assert badge.version is None


# --- fake conn for the I/O paths --------------------------------------------


class _FakeConn:
    """Answers the three lineage reads (set-based badge, numbered timeline,
    pointers) and the snapshot fetch behind the render adapter. Records every
    `fetch` SQL so the no-N+1 property can be asserted."""

    def __init__(
        self,
        *,
        badge_rows: list[dict[str, Any]] | None = None,
        numbered_rows: list[dict[str, Any]] | None = None,
        pointer_rows: list[dict[str, Any]] | None = None,
        snapshots: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.badge_rows = badge_rows or []
        self.numbered_rows = numbered_rows or []
        self.pointer_rows = pointer_rows or []
        self.snapshots = snapshots or {}
        self.fetch_sqls: list[str] = []

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        self.fetch_sqls.append(sql)
        if "WITH latest AS" in sql:
            return self.badge_rows
        if "ROW_NUMBER()" in sql:
            return self.numbered_rows
        if "FROM snapshot_pointers" in sql:
            return self.pointer_rows
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM contract_snapshots\nWHERE id" in sql:
            return self.snapshots.get(args[0])
        return None


# --- set-based list badge: ONE query, no N+1 --------------------------------


async def test_set_based_badge_single_query_no_n_plus_one() -> None:
    conn = _FakeConn(
        badge_rows=[
            {
                "contract_id": "c1",
                "status": "drafting",
                "snapshot_count": 2,
                "lbe_id": "s2",
                "lbe_origin": "export",
                "lbe_pointers": ["counterparty:shared"],
                "diverged": True,
            },
            {
                "contract_id": "c2",
                "status": "drafting",
                "snapshot_count": 0,
                "lbe_id": None,
                "lbe_origin": None,
                "lbe_pointers": [],
                "diverged": False,
            },
        ]
    )

    badges = await lineage.derive_status_for_contracts(conn, ["c1", "c2"])

    # exactly one fetch — the set-based query, not one-per-contract
    assert len(conn.fetch_sqls) == 1
    assert badges["c1"].label == "Sent to counterparty"
    assert badges["c1"].version == 2
    assert badges["c1"].marker is True  # diverged since the send
    assert badges["c2"].label == "Working copy"
    assert badges["c2"].version is None


async def test_set_based_badge_empty_ids_skips_query() -> None:
    conn = _FakeConn()
    assert await lineage.derive_status_for_contracts(conn, []) == {}
    assert conn.fetch_sqls == []  # no query for an empty list


# --- version numbering query (ROW_NUMBER over created_at) --------------------


async def test_list_numbered_snapshots_pairs_version_and_omits_tree() -> None:
    conn = _FakeConn(
        numbered_rows=[
            {
                "id": "s1",
                "contract_id": "c1",
                "label": "v1",
                "origin": "export",
                "created_at": _BASE,
                "version": 1,
            },
            {
                "id": "s2",
                "contract_id": "c1",
                "label": "v2",
                "origin": "export",
                "created_at": _BASE + timedelta(minutes=1),
                "version": 2,
            },
        ]
    )

    numbered = await snapshot.list_numbered_snapshots(conn, "c1")

    assert [(v, s.id) for v, s in numbered] == [(1, "s1"), (2, "s2")]
    assert all(s.tree is None for _v, s in numbered)
    # the rule is encoded in the query: position over created_at
    assert "ROW_NUMBER() OVER (PARTITION BY contract_id ORDER BY created_at)" in conn.fetch_sqls[0]


# --- lineage timeline assembly ----------------------------------------------


async def test_get_lineage_assembles_timeline_baseline_and_reserved() -> None:
    conn = _FakeConn(
        badge_rows=[
            {
                "contract_id": "c1",
                "status": "under negotiation",
                "snapshot_count": 2,
                "lbe_id": "s2",
                "lbe_origin": "export",
                "lbe_pointers": ["legal_team:shared"],
                "diverged": False,
            }
        ],
        numbered_rows=[
            {
                "id": "s1",
                "contract_id": "c1",
                "label": None,
                "origin": "export",
                "created_at": _BASE,
                "version": 1,
            },
            {
                "id": "s2",
                "contract_id": "c1",
                "label": None,
                "origin": "export",
                "created_at": _BASE + timedelta(minutes=1),
                "version": 2,
            },
        ],
        pointer_rows=[
            {"party": "counterparty", "direction": "shared", "snapshot_id": "s1"},
            {"party": "legal_team", "direction": "shared", "snapshot_id": "s2"},
        ],
    )

    view = await lineage.get_lineage(conn, "c1")

    # three reads total: set-based badge + numbered timeline + pointers (no N+1)
    assert len(conn.fetch_sqls) == 3
    assert view.badge.label == "Sent to legal"

    assert [e.version for e in view.timeline] == [1, 2]
    v1, v2 = view.timeline
    assert v1.direction == "sent" and v1.party == "counterparty"
    assert v1.pointer_labels == ["last_shared_with_counterparty"]
    assert v1.is_current_baseline is True  # counterparty `shared` pointer rests here
    assert v2.party == "legal" and v2.is_current_baseline is False

    assert view.working_copy.diverged_since_last_send is False
    assert [(r.party, r.populated) for r in view.reserved] == [
        ("counterparty", False),
        ("legal", False),
    ]


async def test_get_lineage_marker_flows_to_working_copy() -> None:
    conn = _FakeConn(
        badge_rows=[
            {
                "contract_id": "c1",
                "status": "under negotiation",
                "snapshot_count": 1,
                "lbe_id": "s1",
                "lbe_origin": "export",
                "lbe_pointers": ["counterparty:shared"],
                "diverged": True,
            }
        ],
        numbered_rows=[
            {
                "id": "s1",
                "contract_id": "c1",
                "label": None,
                "origin": "export",
                "created_at": _BASE,
                "version": 1,
            }
        ],
        pointer_rows=[{"party": "counterparty", "direction": "shared", "snapshot_id": "s1"}],
    )

    view = await lineage.get_lineage(conn, "c1")

    assert view.badge.marker is True
    assert view.working_copy.diverged_since_last_send is True


# --- read-only snapshot render adapter --------------------------------------


def _stored_snapshot_row(tree: list[SnapshotNode]) -> dict[str, Any]:
    return {
        "id": "s1",
        "contract_id": "c1",
        "label": None,
        "tree": json.dumps([n.model_dump() for n in tree]),
        "origin": "export",
        "created_at": _BASE,
    }


async def test_get_snapshot_tree_nests_and_drops_deleted() -> None:
    tree = [
        SnapshotNode(
            id="n1", parent_id=None, order_index=100, content_type="prose",
            heading="1", body="Root.", is_deleted=False,
        ),
        SnapshotNode(
            id="n2", parent_id="n1", order_index=100, content_type="prose",
            heading="1.1", body="Child.", is_deleted=False,
        ),
        SnapshotNode(
            id="n3", parent_id="n1", order_index=200, content_type="prose",
            heading=None, body="Gone.", is_deleted=True,
        ),
    ]
    conn = _FakeConn(snapshots={"s1": _stored_snapshot_row(tree)})

    rendered = await snapshot.get_snapshot_tree(conn, "c1", "s1")

    assert rendered is not None
    assert rendered.contract_id == "c1"
    assert [n.id for n in rendered.nodes] == ["n1"]  # one root
    root = rendered.nodes[0]
    assert [c.id for c in root.children] == ["n2"]  # deleted n3 dropped
    assert root.children[0].body == "Child."


async def test_get_snapshot_tree_rejects_cross_contract() -> None:
    tree = [
        SnapshotNode(
            id="n1", parent_id=None, order_index=100, content_type="prose",
            heading=None, body="x", is_deleted=False,
        )
    ]
    conn = _FakeConn(snapshots={"s1": _stored_snapshot_row(tree)})
    # snapshot belongs to c1, requested under c2 → None (404 at the route)
    assert await snapshot.get_snapshot_tree(conn, "c2", "s1") is None


async def test_get_snapshot_tree_missing_returns_none() -> None:
    conn = _FakeConn(snapshots={})
    assert await snapshot.get_snapshot_tree(conn, "c1", "nope") is None
