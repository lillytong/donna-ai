"""F27 received-version lineage, end-to-end against a LIVE Postgres.

Drives the REAL F03b spine so the lineage view is validated against data the
production import path actually writes (not synthetic-only rows): a Mode-A baseline
import, mark-as-sent (cuts the `last_shared_with_counterparty` snapshot → v1), then a
Mode-B clean-diff revision import (writes the `as_received` snapshot + advances the
`received` pointer → v2). Asserts `get_lineage` now surfaces the received version as a
real numbered, direction-tagged timeline entry (not a greyed `ReservedSlot`), that its
reserved slot is dropped while the still-empty legal slot remains, and that the entry's
snapshot is openable read-only via the same render adapter the frontend uses.

The test body runs inside an OUTER transaction ROLLED BACK in `finally`, so the dev DB
is never mutated (the services' own `conn.transaction()` blocks nest as savepoints).
Skips cleanly when no live Postgres is reachable (CI / fresh clone).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest
from backend.config.settings import get_settings
from backend.models.imports import NodeTreeItem
from backend.models.mark_sent import MarkSentRequest
from backend.models.revision_import import RevisionImportRequest
from backend.services.contract_repo import insert_nodes
from backend.services.import_.docx_reader import read_docx
from backend.services.import_.persist import tree_to_node_rows
from backend.services.import_.revision_import import import_revision
from backend.services.import_.tree_builder import build_tree
from backend.services.lineage import get_lineage
from backend.services.mark_sent import mark_sent
from backend.services.snapshot import get_snapshot_tree
from docx import Document


def _all_bodies(nodes: list[NodeTreeItem]) -> str:
    parts: list[str] = []
    for n in nodes:
        parts.append(n.body or "")
        parts.append(_all_bodies(n.children))
    return " ".join(parts)


_HEADING = "Master Services Terms"
_C1_ORIG = (
    "The vendor shall deliver the fermentation hardware to the licensee within "
    "thirty days of the effective date."
)
_C1_REVISED = (
    "The vendor shall deliver the fermentation hardware to the licensee within "
    "forty five days of the effective date."
)
_C2 = (
    "The licensee agrees to pay a royalty equal to ten percent of net collected "
    "revenue on a quarterly basis."
)
_C3 = (
    "Each party must keep all proprietary biological materials strictly "
    "confidential throughout the entire term of this agreement."
)


async def _connect_or_skip() -> Any:
    try:
        return await asyncpg.connect(get_settings().database_url)
    except (OSError, asyncpg.PostgresError) as exc:  # no live DB in this env
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


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))


async def test_received_version_is_real_lineage_entry(tmp_path: Path) -> None:
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)

        original = tmp_path / "original.docx"
        revised = tmp_path / "revised.docx"
        _write_docx(original, [_HEADING, _C1_ORIG, _C2, _C3])
        _write_docx(revised, [_HEADING, _C1_REVISED, _C2, _C3])

        # Seed the working copy, then mark-as-sent → v1 (shared with counterparty).
        await insert_nodes(conn, contract_id, tree_to_node_rows(build_tree(read_docx(original))))
        sent = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        assert sent.marked

        # Mode-B import → writes the as_received snapshot + received pointer → v2.
        imp = await import_revision(
            conn, contract_id, str(revised), RevisionImportRequest(source="counterparty")
        )
        assert imp.as_received_snapshot_id
        assert imp.received_pointer_party == "counterparty"

        view = await get_lineage(conn, contract_id)

        # The send is v1, the received revision is v2 — a real numbered entry.
        assert [e.version for e in view.timeline] == [1, 2]
        v1, v2 = view.timeline
        assert v1.direction == "sent" and v1.party == "counterparty"
        assert v2.direction == "received" and v2.party == "counterparty"
        assert v2.snapshot_id == imp.as_received_snapshot_id
        assert v2.pointer_labels == ["last_received_from_counterparty"]

        # The counterparty reserved (empty-state) slot is gone; legal still pending.
        assert [(r.party, r.populated) for r in view.reserved] == [("legal", False)]

        # The received version is openable read-only via the shared render adapter,
        # and carries the counterparty's revised text.
        tree = await get_snapshot_tree(conn, contract_id, v2.snapshot_id)
        assert tree is not None
        assert "forty five days" in _all_bodies(tree.nodes)
    finally:
        await tx.rollback()
        await conn.close()
