"""F-version-delete (DD-85 / DD-87) end-to-end against a LIVE Postgres.

Two gates, both driving the real snapshot/mark-sent spine:

  1. The INVERSE-PAIR ORACLE (DD-87 §3): after a LATEST-delete rollback, the live
     working tree must equal the predecessor snapshot's frozen tree — i.e.
     `get_snapshot_tree(predecessor)` == the live `get_contract_tree`, structurally
     (topology + content_type + heading + body, the fields the snapshot preserves and
     the rollback restores). This is the free inverse of cut→restore.

  2. GAP PRESERVATION + no v-number reuse (DD-85): mark-sent v1,v2,v3, delete the
     MIDDLE v2, then mark-sent again — the new version is v4 (MAX+1, never v2), and the
     lineage reads v1,v3,v4. Proves the persisted-numbering swap (DD-87 §1).

The body runs in an OUTER transaction ROLLED BACK in `finally`, so the dev DB is never
mutated (the services' own `conn.transaction()` blocks nest as savepoints). Skips
cleanly when no live Postgres is reachable (CI / fresh clone).
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from backend.config.settings import get_settings
from backend.models.imports import ContractTreeResponse, NodeTreeItem
from backend.models.mark_sent import MarkSentRequest
from backend.services.contract_repo import fetch_nodes, insert_nodes
from backend.services.import_.docx_reader import read_docx
from backend.services.import_.persist import tree_to_node_rows
from backend.services.import_.tree_builder import build_tree
from backend.services.lineage import get_lineage
from backend.services.mark_sent import mark_sent
from backend.services.snapshot import get_snapshot_tree, list_pointers
from backend.services.version_delete import delete_version
from docx import Document

_HEADING = "Master Services Terms"
_C1_ORIG = "The vendor shall deliver the hardware within thirty days of the effective date."
_C1_EDIT = "The vendor shall deliver the hardware within ninety days of the effective date."
_C2 = "The licensee shall pay a royalty of ten percent of net collected revenue quarterly."


async def _connect_or_skip() -> Any:
    try:
        return await asyncpg.connect(get_settings().database_url)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no live Postgres reachable: {exc}")


async def _make_contract(conn: Any) -> str:
    client_id = await conn.fetchval(
        "INSERT INTO clients (name) VALUES ($1) RETURNING id", "Test Client"
    )
    deal_id = await conn.fetchval(
        "INSERT INTO deals (client_id, name, position) VALUES ($1, $2, 'licensor') RETURNING id",
        client_id,
        "Test Deal",
    )
    ct_id = await conn.fetchval(
        "INSERT INTO contract_types (name) VALUES ($1) RETURNING id", "Test Type"
    )
    contract_id = await conn.fetchval(
        """INSERT INTO contracts (client_id, deal_id, contract_type_id, name, status, origin)
           VALUES ($1, $2, $3, $4, 'drafting', 'us') RETURNING id""",
        client_id,
        deal_id,
        ct_id,
        "Test Contract",
    )
    return str(contract_id)


def _write_docx(path: Any, paragraphs: list[str]) -> None:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))


def _project(nodes: list[NodeTreeItem]) -> list[Any]:
    """The snapshot-preserved projection: topology + content_type + heading + body.
    Role / plain_text / table_data are NOT frozen in a snapshot (DD-87 §3 note), so the
    inverse-pair equality is on exactly what cut→restore round-trips."""
    return [
        (n.order_index, n.content_type, n.heading, n.body, _project(n.children)) for n in nodes
    ]


async def _live_tree(conn: Any, contract_id: str) -> ContractTreeResponse:
    rows = await fetch_nodes(conn, contract_id)
    return ContractTreeResponse.from_rows(contract_id, rows)


async def test_latest_delete_rollback_inverse_pair_oracle(tmp_path: Any) -> None:
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)
        original = tmp_path / "original.docx"
        _write_docx(original, [_HEADING, _C1_ORIG, _C2])
        await insert_nodes(conn, contract_id, tree_to_node_rows(build_tree(read_docx(original))))

        # v1 = the original working copy, sent to counterparty.
        v1 = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        assert v1.marked and v1.snapshot_id is not None

        # Edit a clause body in place, then v2 captures the edited copy.
        await conn.execute(
            "UPDATE nodes SET body = $1, updated_at = now() "
            "WHERE contract_id = $2::uuid AND body = $3",
            _C1_EDIT,
            contract_id,
            _C1_ORIG,
        )
        v2 = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        assert v2.marked and v2.snapshot_id is not None
        assert v2.version == 2

        # The live tree currently carries the edit.
        live_before = await _live_tree(conn, contract_id)
        assert "ninety days" in str(_project(live_before.nodes))

        # Delete the LATEST (v2) → rolls the working copy back to v1.
        res = await delete_version(conn, contract_id, v2.snapshot_id, confirm=True)
        assert res is not None
        assert res.deleted and res.rolled_back
        assert res.rollback_to_version == 1
        assert res.pointers_rolled_back == ["counterparty"]

        # ORACLE: live working tree == v1 snapshot tree (structurally).
        live_after = await _live_tree(conn, contract_id)
        v1_tree = await get_snapshot_tree(conn, contract_id, v1.snapshot_id)
        assert v1_tree is not None
        assert _project(live_after.nodes) == _project(v1_tree.nodes)
        assert "thirty days" in str(_project(live_after.nodes))
        assert "ninety days" not in str(_project(live_after.nodes))

        # The redline baseline pointer rolled back to v1; v2 is wiped.
        pointers = await list_pointers(conn, contract_id)
        baseline = next(
            p for p in pointers if (p.party, p.direction) == ("counterparty", "shared")
        )
        assert baseline.snapshot_id == v1.snapshot_id
        view = await get_lineage(conn, contract_id)
        assert [e.version for e in view.timeline] == [1]
    finally:
        await tx.rollback()
        await conn.close()


async def test_middle_delete_preserves_gap_and_no_reuse(tmp_path: Any) -> None:
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)
        original = tmp_path / "original.docx"
        _write_docx(original, [_HEADING, _C1_ORIG, _C2])
        await insert_nodes(conn, contract_id, tree_to_node_rows(build_tree(read_docx(original))))

        v1 = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        v2 = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        v3 = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        assert (v1.version, v2.version, v3.version) == (1, 2, 3)
        assert v2.snapshot_id is not None

        # Delete the MIDDLE (v2): working copy untouched, gap preserved.
        res = await delete_version(conn, contract_id, v2.snapshot_id, confirm=True)
        assert res is not None
        assert res.deleted and res.is_latest is False and res.rolled_back is False

        view = await get_lineage(conn, contract_id)
        assert [e.version for e in view.timeline] == [1, 3]

        # Next mark-sent mints v4 (MAX+1) — NEVER reuses v2.
        v4 = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        assert v4.version == 4
        view2 = await get_lineage(conn, contract_id)
        assert [e.version for e in view2.timeline] == [1, 3, 4]
    finally:
        await tx.rollback()
        await conn.close()
