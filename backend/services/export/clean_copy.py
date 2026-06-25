"""Clean-copy export orchestration (F15b, DD-43, DD-71).

A clean-copy export regenerates the current DB state to a clean .docx via the
deterministic renderer (DD-43). **DD-71: export is a pure grab** — it cuts no
snapshot, advances no pointer, stamps no `node_versions` group, and has zero
lineage effect. The boundary event (snapshot + pointer + version mint) is the
separate Mark-as-sent action (`services/mark_sent.py`), which the operator
triggers after sending the file manually — the app can't actually send.

This service is renderer-only; `render_docx.py` is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.models.imports import StoredNode
from backend.services.export.render_docx import render_contract_docx


async def export_clean_copy(
    nodes: list[StoredNode],
    style_config: dict[str, Any],
) -> bytes:
    return await asyncio.to_thread(render_contract_docx, nodes, style_config)
