"""Eval harness for the F05b conceptual clause-search surface.

Measures TOP-1 accuracy: given a plain-language concept and a fixed candidate
clause list, does the LOW-tier model pick the clause node the human expects
(or null when nothing reasonably matches)?

This is an EVAL, not a test: it makes REAL LOW-tier (Haiku) LLM calls and lives
outside `tests/`. It reuses the production surface wherever possible rather than
reimplementing it:
  * the versioned prompt `backend/prompts/clause_search_v1.txt` via `render`;
  * `build_candidate_block` — the exact `id :: role :: heading :: snippet`
    candidate formatting prod sends;
  * `_parse_match` — prod's tolerant JSON parse;
  * `complete` — the production LiteLLM wrapper (LOW tier, json_response), which
    sources the model id, temperature and api key from `config/settings`.

The only thing rebuilt here is the harness loop and scoring; the candidate block
is built from synthetic `StoredNode`s so prod's formatter produces prod's lines.

Run from the repo root (so `.env` is found):
    python -m evals.clause_search.run_eval
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from backend.config.settings import get_settings
from backend.models.imports import StoredNode
from backend.prompts.utils import render
from backend.services.clause_search import (
    _parse_match,
    build_candidate_block,
)
from backend.services.llm import complete
from pydantic import BaseModel

_DATASET = Path(__file__).parent / "dataset.json"


class Candidate(BaseModel):
    id: str
    role: str
    heading: str
    snippet: str


class EvalCase(BaseModel):
    query: str
    expected_id: str | None = None


class CaseOutcome(BaseModel):
    query: str
    expected_id: str | None
    predicted_id: str | None
    correct: bool
    is_null_case: bool


def _load_dataset() -> tuple[list[Candidate], list[EvalCase]]:
    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    candidates = [Candidate.model_validate(c) for c in data["candidates"]]
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    return candidates, cases


def _to_nodes(candidates: list[Candidate]) -> list[StoredNode]:
    """Build synthetic StoredNodes so prod's `build_candidate_block` emits the exact
    `id :: role :: heading :: snippet` lines: a heading node per candidate plus a
    child body node carrying the snippet (prod draws the snippet from child body)."""
    nodes: list[StoredNode] = []
    for i, c in enumerate(candidates):
        nodes.append(
            StoredNode(
                id=c.id,
                order_index=i,
                content_type="paragraph",
                heading=c.heading,
                role=c.role,
            )
        )
        nodes.append(
            StoredNode(
                id=f"{c.id}-body",
                parent_id=c.id,
                order_index=0,
                content_type="paragraph",
                body=c.snippet,
            )
        )
    return nodes


async def _predict(prompt: str, query: str, valid_ids: set[str]) -> str | None:
    """One real LOW-tier call. Mirrors `search_clause`'s message shape and applies
    prod's hallucinated-id guard: an id not in the candidate set becomes no match."""
    settings = get_settings()
    messages: list[dict[str, Any]] = [
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
        caller="eval_clause_search",
        max_tokens=settings.llm.clause_search_max_tokens,
        temperature=settings.llm.clause_search_temperature,
        json_response=True,
    )
    match = _parse_match(result.text)
    if match.node_id is not None and match.node_id in valid_ids:
        return match.node_id
    return None


async def run() -> int:
    candidates, cases = _load_dataset()
    valid_ids = {c.id for c in candidates}
    prompt = render("clause_search_v1.txt", candidates=build_candidate_block(_to_nodes(candidates)))

    outcomes: list[CaseOutcome] = []
    for case in cases:
        predicted = await _predict(prompt, case.query, valid_ids)
        outcomes.append(
            CaseOutcome(
                query=case.query,
                expected_id=case.expected_id,
                predicted_id=predicted,
                correct=predicted == case.expected_id,
                is_null_case=case.expected_id is None,
            )
        )

    _report(outcomes)
    return 0


def _report(outcomes: list[CaseOutcome]) -> None:
    print("\nF05b clause-search conceptual-match eval  (LOW tier, TOP-1)\n")
    print(f"{'':2} {'query':<48} {'predicted':<16} {'expected':<16}")
    print("-" * 84)
    for o in outcomes:
        mark = "ok" if o.correct else "XX"
        print(
            f"{mark:2} "
            f"{o.query[:46]:<48} "
            f"{str(o.predicted_id):<16} "
            f"{str(o.expected_id):<16}"
        )

    total = len(outcomes)
    passed = sum(o.correct for o in outcomes)
    null_cases = [o for o in outcomes if o.is_null_case]
    null_passed = sum(o.correct for o in null_cases)

    print("-" * 84)
    print(f"\nOverall TOP-1 accuracy: {passed}/{total} = {passed / total:.0%}")
    if null_cases:
        print(
            f"Null-handling ('no reasonable match' -> null): "
            f"{null_passed}/{len(null_cases)} correct"
        )
        for o in null_cases:
            verdict = "correctly returned null" if o.correct else f"returned {o.predicted_id}"
            print(f"  - {o.query!r}: {verdict}")
    print()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
