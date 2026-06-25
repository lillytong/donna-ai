"""Clean-copy export orchestration (F15b, DD-61).

A clean-copy export always regenerates the current DB state to a clean .docx (the
renderer, DD-43). Whether it also cuts a snapshot depends on the recipient's
intent (DD-61):

- **Send** (`counterparty` / `legal`) — cut an `origin='export'` snapshot, stamp
  the pending edits group under it, and advance that party's DD-48 `shared`
  pointer. The snapshot is a redline baseline and a lineage node.
- **Grab** (`internal` / `copy_only`) — regenerate and download only. No snapshot
  cut, no pointer moved, no edits-group stamped, zero lineage effect (DD-61): the
  operator re-reading their own working copy is not a new version.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.models.export import ExportRecipient
from backend.models.imports import StoredNode
from backend.models.snapshots import CutSnapshotRequest, SnapshotPointerTarget
from backend.services.export.render_docx import render_contract_docx
from backend.services.snapshot import cut_snapshot

_SEND_POINTERS: dict[ExportRecipient, SnapshotPointerTarget] = {
    "counterparty": SnapshotPointerTarget(party="counterparty", direction="shared"),
    "legal": SnapshotPointerTarget(party="legal_team", direction="shared"),
}


async def export_clean_copy(
    conn: Any,
    contract_id: str,
    nodes: list[StoredNode],
    style_config: dict[str, Any],
    recipient: ExportRecipient,
) -> bytes:
    data = await asyncio.to_thread(render_contract_docx, nodes, style_config)
    pointer = _SEND_POINTERS.get(recipient)
    if pointer is not None:
        await cut_snapshot(
            conn,
            contract_id,
            CutSnapshotRequest(origin="export", pointer=pointer),
        )
    return data
