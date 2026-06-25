"""Donna-assisted clause drafting (F08d) — the "Draft with Donna" path in the cockpit
⋮ insert menu. Mirrors the F11 recommendation pipeline (single linear shot, no LangGraph —
DD-52):

  1. Load the contract (for deal type) + its nodes; resolve the anchor's surrounding-clause
     context (REUSE grounding.py — no embeddings).
  2. Render the versioned prompt (`clause_draft_v1.txt`): draft ONE clause at the insert
     level, grounded in deal type + surrounding clauses, never inventing a fact/figure
     (bracketed placeholder where the operator must supply a value).
  3. Call Claude at the CAPABLE tier (high/Opus — drafted language is high-consequence,
     DD-35), structured JSON; validate citations against the real id set (hallucinated-id
     guard, as qa.py) and scrub any leaked id from the heading/body.

The draft is TRANSIENT: it is returned to pre-fill the insert editor and the operator
commits it through the normal F08b create path — `draft_clause` never writes a node (§2.4:
Donna's output is an operator-reviewed draft, never authoritative).

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.clause_draft import ClauseDraft, ClauseDraftRequest
from backend.prompts.utils import render
from backend.services.contract_repo import fetch_nodes
from backend.services.donna.grounding import build_clause_grounding, build_label_map
from backend.services.donna.qa import scrub_leaked_ids
from backend.services.llm import complete
from backend.services.settings_repo import get_contract, get_contract_type

# Empty body = the honest "couldn't draft" signal the cockpit renders as a retry, never a
# fabricated generic clause (§2.4).
_FALLBACK = ClauseDraft(heading=None, body="", citations=[])

_PLACEMENT = {
    "below": "as a new clause immediately after",
    "sub": "as a sub-clause under",
    "above": "as a new clause immediately before",
}


class ContractNotFound(Exception):
    """Contract missing."""


def parse_draft(text: str) -> ClauseDraft:
    """Tolerate a non-strict JSON draft; an unparseable one becomes the empty fallback
    (mirrors recommendations.parse_draft)."""
    try:
        return ClauseDraft.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return ClauseDraft.model_validate_json(text[start : end + 1])
            except ValidationError:
                return _FALLBACK
        return _FALLBACK


def finalize_draft(
    draft: ClauseDraft, valid_ids: set[str], id_labels: dict[str, str]
) -> ClauseDraft:
    """Pure post-LLM cleanup: drop hallucinated citations (keep only real node ids) and scrub
    any leaked id out of the heading/body, replacing it with its legible label."""
    citations = [c for c in draft.citations if c in valid_ids]
    heading = scrub_leaked_ids(draft.heading, id_labels) if draft.heading is not None else None
    return draft.model_copy(
        update={
            "heading": heading,
            "body": scrub_leaked_ids(draft.body, id_labels),
            "citations": citations,
        }
    )


async def draft_clause(contract_id: str, req: ClauseDraftRequest) -> ClauseDraft:
    """Draft one clause for the insert described by `req`. Transient — never persisted."""
    async with acquire() as conn:
        contract = await get_contract(conn, contract_id)
        if contract is None:
            raise ContractNotFound(contract_id)
        ctype = await get_contract_type(conn, contract.contract_type_id)
        nodes = await fetch_nodes(conn, contract_id)

    deal_type = ctype.name if ctype is not None else "contract"
    labels = build_label_map(nodes)
    anchor_label = (
        labels.get(req.anchor_node_id, "this clause")
        if req.anchor_node_id is not None
        else "the contract (no specific clause selected)"
    )
    settings = get_settings()
    prompt = render(
        "clause_draft_v1.txt",
        deal_type=deal_type,
        placement=_PLACEMENT[req.mode],
        anchor=anchor_label,
        context=build_clause_grounding(nodes, req.anchor_node_id, labels)
        or "(no surrounding clause context)",
        description=req.description,
    )

    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="clause_draft",
        max_tokens=settings.llm.clause_draft_max_tokens,
        temperature=settings.llm.clause_draft_temperature,
        json_response=True,
    )

    valid_ids = {n.id for n in nodes}
    return finalize_draft(parse_draft(result.text), valid_ids, labels)
