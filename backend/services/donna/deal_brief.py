"""Donna's deal-brief distillation (F37 / DD-95) — the per-deal global-context tier. Donna
reads the WHOLE contract once at import and distils a grounded brief (parties + roles, each
party's business/interests, the economic spine, key terms + interrelations, purpose) that fills
the {deal_context} grounding slot for her recommendations / chat / brainstorm (Part B wires the
injection). A single linear shot, no LangGraph (DD-52); the whole-contract read is ONE Opus
call, so the cost guard here is structural (skip an empty contract), not per-hunk.

Grounding discipline lives in the prompt (`deal_brief_v1.txt`): cite-or-flag, "not stated in
the contract" over invention, no outside knowledge, inferences marked, and an honest flag for
economics that live in sibling offtake/JV docs this single contract cannot see.

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

import structlog

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.deal_brief import DealBrief
from backend.models.imports import StoredNode
from backend.prompts.utils import render
from backend.services import deal_brief_repo
from backend.services.contract_repo import fetch_nodes
from backend.services.donna.grounding import build_label_map
from backend.services.export.render_docx import _plan
from backend.services.llm import complete

log = structlog.get_logger()


def assemble_contract_text(nodes: list[StoredNode]) -> str:
    """The whole contract as ordered, labelled plain text for a single distillation read.

    Document order (pre-order DFS) via the shared export plan, each node rendered as
    `<label>: <text>` so the model can refer to a clause in plain terms (cite-or-flag) without
    needing raw ids. Tables are flattened row-by-row. Empty nodes are dropped."""
    labels = build_label_map(nodes)
    lines: list[str] = []
    for node, _number in _plan(nodes):
        label = labels.get(node.id, node.role)
        if node.table_data:
            body = "\n".join(" | ".join(cell for cell in row) for row in node.table_data)
        else:
            body = node.body or node.plain_text or ""
        text = " ".join(part for part in (node.heading, body) if part).strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n\n".join(lines)


async def distill_deal_brief(
    conn: object, contract_id: str, *, force: bool = False
) -> DealBrief | None:
    """Read the whole contract, distil the grounded deal brief, and persist it (F37 / DD-95).

    EDITS WIN: `seed_brief` skips a contract the operator has edited unless `force` is set, so
    an automatic re-import re-distil never clobbers an operator edit; a manual Refresh passes
    `force=True`. Returns the stored brief, or None when there is nothing to distil (an empty
    contract) or the upsert was skipped to respect an operator edit."""
    nodes = await fetch_nodes(conn, contract_id)
    if not nodes:  # structural guard: nothing to read, no LLM call
        log.info("deal_brief.skip_empty_contract", contract_id=contract_id)
        return None

    contract_text = assemble_contract_text(nodes)
    if not contract_text.strip():
        log.info("deal_brief.skip_empty_contract", contract_id=contract_id)
        return None

    settings = get_settings()
    prompt = render("deal_brief_v1.txt", contract_text=contract_text)
    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_deal_brief",
        max_tokens=settings.llm.deal_brief_max_tokens,
        temperature=settings.llm.deal_brief_temperature,
        timeout_s=settings.llm.deal_brief_timeout_s,
    )

    brief = await deal_brief_repo.seed_brief(
        conn, contract_id, result.text, settings.models.high, force=force
    )
    if brief is None:
        log.info("deal_brief.seed_skipped_operator_edited", contract_id=contract_id)
    return brief


async def distill_on_import(contract_id: str) -> None:
    """FAILURE-ISOLATED background entry fired post-commit from the import route (F37 auto-seed).
    Distils the deal brief from the freshly committed contract so the {deal_context} grounding
    slot is populated before the operator opens review. Acquires its OWN connection and swallows
    every error (logged) — a brief failure must NEVER fail or roll back the already-committed
    import (mirrors F03c's recommend_on_import / F30's distill_on_issue_close).

    `force=False`: an automatic re-import re-distil respects an operator edit (edits win); the
    manual Refresh route forces a fresh distil."""
    try:
        async with acquire() as conn:
            brief = await distill_deal_brief(conn, contract_id, force=False)
        log.info(
            "deal_brief.auto_done",
            contract_id=contract_id,
            distilled=brief is not None,
        )
    except Exception:
        log.warning("deal_brief.auto_failed", contract_id=contract_id, exc_info=True)
