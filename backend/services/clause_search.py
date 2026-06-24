"""Conceptual clause search (Donna's first live-LLM surface).

Loads the contract's live nodes and offers only the *heading* nodes — the clause
titles and section/appendix headings conceptual search actually matches against
(validated on real data) — to a low-tier (Haiku) model as a token-capped candidate
list. Body paragraphs are excluded: sending all nodes blew the org rate limit. The
candidate block is the same for every query in a session, so it is sent as its own
cache_control prefix block (Anthropic prompt caching) with the volatile query last.
The model's answer is validated against the actual node ids — a hallucinated id is
treated as no match. No LangGraph: a single linear shot (DD-52)."""

from __future__ import annotations

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.clause_search import ClauseMatch, ClauseSearchResult
from backend.models.imports import StoredNode
from backend.prompts.utils import render
from backend.services.contract_repo import fetch_nodes
from backend.services.llm import complete

# Per-candidate snippet cap. A prompt-construction constant (not a model token
# limit, so not a DD-35 config value): keeps each candidate line lean.
_SNIPPET_CHARS = 120


def _is_candidate(node: StoredNode) -> bool:
    # Heading nodes only (clause titles, section/appendix headings) — not body
    # paragraphs. Mutually exclusive with `body` at persist time (persist.py).
    return node.heading is not None


def _child_snippets(nodes: list[StoredNode]) -> dict[str, str]:
    """Map each parent node id -> a snippet drawn from its child body text, so a
    heading candidate carries a preview of the clause's content."""
    by_parent: dict[str, list[str]] = {}
    for n in nodes:
        if n.parent_id is not None and n.body:
            by_parent.setdefault(n.parent_id, []).append(n.body)
    return {pid: " ".join(bodies) for pid, bodies in by_parent.items()}


def _cap(text: str) -> str:
    return " ".join(text.split())[:_SNIPPET_CHARS]


def build_candidate_block(nodes: list[StoredNode]) -> str:
    """Render heading nodes as `id :: role :: heading :: snippet` lines."""
    child_text = _child_snippets(nodes)
    lines = [
        f"{n.id} :: {n.role} :: {n.heading} :: {_cap(child_text.get(n.id, ''))}"
        for n in nodes
        if _is_candidate(n)
    ]
    return "\n".join(lines)


def _node_text(node: StoredNode) -> str | None:
    return node.body or node.plain_text or node.heading


def _parse_match(text: str) -> ClauseMatch:
    """Tolerate a non-strict JSON answer; an unparseable one is no match."""
    try:
        return ClauseMatch.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return ClauseMatch.model_validate_json(text[start : end + 1])
            except ValidationError:
                return ClauseMatch(node_id=None)
        return ClauseMatch(node_id=None)


async def search_clause(contract_id: str, query: str) -> ClauseSearchResult:
    async with acquire() as conn:
        nodes = await fetch_nodes(conn, contract_id)

    candidates = [n for n in nodes if _is_candidate(n)]
    if not candidates:
        return ClauseSearchResult(node_id=None, matched_text=None)

    settings = get_settings()
    prompt = render("clause_search_v1.txt", candidates=build_candidate_block(nodes))
    # Cache the stable candidate block (prefix), keep the per-query text volatile
    # and last — Anthropic caching is a prefix match.
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": query},
            ],
        }
    ]
    result = await complete(
        tier="low",
        messages=messages,
        caller="clause_search",
        max_tokens=settings.llm.clause_search_max_tokens,
        temperature=settings.llm.clause_search_temperature,
        json_response=True,
    )

    match = _parse_match(result.text)
    by_id = {n.id: n for n in candidates}
    node = by_id.get(match.node_id) if match.node_id is not None else None
    if node is None:
        return ClauseSearchResult(node_id=None, matched_text=None)
    return ClauseSearchResult(node_id=node.id, matched_text=_node_text(node))
