"""Donna's issue-recommendation layer (F11) — the issue-scoped advisory surface (DD-14,
DD-68). Mirrors the Donna v1 Q&A pipeline (single linear shot, no LangGraph — DD-52):

  1. Load the issue + its anchored clause context (REUSE grounding.py / F05b retrieval —
     no embeddings). An anchored issue grounds on its own clause subtree; a free-floating
     issue resolves a clause via the F05b conceptual lookup over the issue text.
  2. Render the versioned prompt (`donna_recommendation_v1.txt`) — recommend ONLY from the
     grounding, framed on the DD-14 reasonableness spectrum + ask/settle/floor ladder.
     Propose vs counter is the same engine, different output field. If a market benchmark is
     needed but absent (no F29/live research in v1), recommend the STRUCTURE and flag the
     gap — never invent a number.
  3. Call Claude at the CAPABLE tier (high/Opus — counter-language is high-consequence,
     DD-35), structured JSON output; validate citations against the real id set
     (hallucinated-id guard, as qa.py) and scrub any leaked id from the prose fields.
  4. Persist the DRAFT (`donna_recommendations`); it never touches `issues.*` until the
     operator confirms (DD-68).

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.issues import StoredIssue
from backend.models.recommendations import (
    RecommendationConfirmRequest,
    RecommendationConfirmResponse,
    RecommendationDraft,
    StoredRecommendation,
)
from backend.prompts.utils import render
from backend.services.clause_search import search_clause
from backend.services.contract_repo import fetch_nodes
from backend.services.donna import recommendation_repo
from backend.services.donna.distillation import fetch_patterns_for_issue
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_focus,
    build_issue_ledger,
    build_label_map,
    build_pattern_grounding,
)
from backend.services.donna.qa import scrub_leaked_ids
from backend.services.issue_repo import get_issue, list_issues
from backend.services.llm import complete

# A high-consequence surface: an unparseable model output is surfaced as an honest
# "could not ground" draft, never fabricated and never raw (§2.4). No drafts, no citations.
_FALLBACK = RecommendationDraft(
    rationale="I couldn't produce a grounded recommendation for this issue from the "
    "material available. Try refreshing, or work the issue manually.",
    draft_recommended_position=None,
    draft_counter_language=None,
    citations=[],
    missing_benchmark=False,
)


class IssueNotFound(Exception):
    """Issue missing or not in the given contract."""


def parse_draft(text: str) -> RecommendationDraft:
    """Tolerate a non-strict JSON recommendation; an unparseable one becomes the honest
    fallback (mirrors qa.parse_answer)."""
    try:
        return RecommendationDraft.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return RecommendationDraft.model_validate_json(text[start : end + 1])
            except ValidationError:
                return _FALLBACK
        return _FALLBACK


def finalize_draft(
    draft: RecommendationDraft, valid_ids: set[str], id_labels: dict[str, str]
) -> RecommendationDraft:
    """Pure post-LLM cleanup: drop hallucinated citations (keep only real node/issue ids)
    and scrub any leaked id out of every prose field, replacing it with its legible label.
    The citations array keeps the real ids."""
    citations = [c for c in draft.citations if c in valid_ids]
    position = draft.draft_recommended_position
    counter = draft.draft_counter_language
    return draft.model_copy(
        update={
            "rationale": scrub_leaked_ids(draft.rationale, id_labels),
            "draft_recommended_position": (
                scrub_leaked_ids(position, id_labels) if position is not None else None
            ),
            "draft_counter_language": (
                scrub_leaked_ids(counter, id_labels) if counter is not None else None
            ),
            "citations": citations,
        }
    )


def _issue_query(issue: StoredIssue) -> str:
    return " ".join(p for p in (issue.title, issue.our_position, issue.their_position) if p)


async def generate_recommendation(contract_id: str, issue_id: str) -> StoredRecommendation:
    """Generate + persist a fresh draft recommendation for the issue (idempotent-ish:
    regenerating replaces the prior draft). DRAFT only — never written to issues.* (DD-68)."""
    async with acquire() as conn:
        issue = await get_issue(conn, issue_id)
        if issue is None or issue.contract_id != contract_id:
            raise IssueNotFound(issue_id)
        nodes = await fetch_nodes(conn, contract_id)
        issues = await list_issues(conn, contract_id)
        # F30 tier-8 retrieval (DD-76): operator-style always, counterparty when same client,
        # deal-type when same contract type. A background RETRIEVAL INPUT — never authoritative,
        # never cited, never exported (§2.4); appended below as a clearly-labelled block.
        patterns = await fetch_patterns_for_issue(conn, contract_id)

    matched_node_id = issue.node_id
    if matched_node_id is None:  # free-floating: resolve a clause via F05b (no embeddings)
        retrieval = await search_clause(contract_id, _issue_query(issue))
        matched_node_id = retrieval.node_id

    labels = build_label_map(nodes)
    settings = get_settings()
    prompt = render(
        "donna_recommendation_v1.txt",
        clauses=build_clause_grounding(nodes, matched_node_id, labels)
        or "(no anchored clause resolved)",
        issue=build_issue_focus(issue, labels),
        ledger=build_issue_ledger([i for i in issues if i.id != issue.id], labels)
        or "(no other issues on record)",
    )

    # Append the learned-pattern block AFTER the rendered prompt (not as a template slot), so
    # the recommendation prompt template and its eval are untouched and patterns stay visibly
    # distinct from the authoritative, citable grounding above (DD-76).
    pattern_block = build_pattern_grounding(patterns)
    if pattern_block:
        prompt = f"{prompt}\n\n{pattern_block}"

    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_recommendation",
        max_tokens=settings.llm.donna_recommendation_max_tokens,
        temperature=settings.llm.donna_recommendation_temperature,
        json_response=True,
    )

    valid_ids = {n.id for n in nodes} | {i.id for i in issues}
    id_labels = {**labels, **{i.id: i.title for i in issues}}
    draft = finalize_draft(parse_draft(result.text), valid_ids, id_labels)

    async with acquire() as conn:
        return await recommendation_repo.upsert_draft(conn, issue_id, draft, settings.models.high)


async def get_recommendation(issue_id: str) -> StoredRecommendation | None:
    async with acquire() as conn:
        return await recommendation_repo.get_by_issue(conn, issue_id)


def _clean(value: str | None) -> str | None:
    """Empty / whitespace-only edited text means "no language", stored as NULL."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def confirm_recommendation(
    issue_id: str, edit: RecommendationConfirmRequest | None = None
) -> RecommendationConfirmResponse | None:
    """[Use Donna's language]: copy the draft into the issue's exported fields (DD-68). When
    the operator edited the language first ([Edit]), `edit` carries the edited values; both
    overwrite the draft before the copy so the export reflects exactly what was confirmed."""
    edited = (
        (_clean(edit.edited_recommended_position), _clean(edit.edited_counter_language))
        if edit is not None
        else None
    )
    async with acquire() as conn:
        confirmed = await recommendation_repo.confirm(conn, issue_id, edited)
    if confirmed is None:
        return None
    return RecommendationConfirmResponse(
        issue_id=issue_id,
        confirmed=confirmed.confirmed,
        recommended_position=confirmed.draft_recommended_position,
        donna_counter_language=confirmed.draft_counter_language,
    )
