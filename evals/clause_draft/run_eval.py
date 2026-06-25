"""Grounding eval for Donna-assisted clause drafting (F08d) — THE risk gate (§2.4: a
fabricated figure, a coined defined term, or a confidently-wrong clause drafted into the
insert editor is exactly the failure mode the bracketed-placeholder + reuse-the-document's-
terms + honest-empty-body rules exist to prevent). Makes REAL capable-tier (high/Opus) LLM
calls and lives outside `tests/`.

It reuses the production surface, not a re-implementation:
  * `build_clause_grounding` / `build_label_map` — prod's exact id-tagged grounding;
  * `_PLACEMENT` + the versioned prompt `clause_draft_v1.txt` via `render` — the same
    prompt the service renders;
  * `parse_draft` + `finalize_draft` — prod's tolerant parse + citation guard + id scrub;
  * `complete` — the production LiteLLM wrapper (high tier, json_response).

Each case fixes the synthetic contract context so the gate isolates THIS surface's risk:
is the draft grounded and reusing the document's defined terms, does it leave a bracketed
placeholder instead of a fabricated figure, and is it HONEST (empty body) when the
description is too vague to draft anything grounded.

Run from the repo root (so `.env` is found):
    python -m evals.clause_draft.run_eval
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from backend.config.settings import get_settings
from backend.models.clause_draft import ClauseDraft
from backend.models.imports import StoredNode
from backend.prompts.utils import render
from backend.services.donna.drafting import _PLACEMENT, finalize_draft, parse_draft
from backend.services.donna.grounding import build_clause_grounding, build_label_map
from backend.services.llm import complete
from pydantic import BaseModel

_DATASET = Path(__file__).parent / "dataset.json"

_HEX = "[0-9a-fA-F]"
_UUID_RE = re.compile(f"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}")
# A bracketed placeholder the operator must fill in (the prompt example: "[insert notice
# period]"). Any "[...]" span counts as the honest "you supply this value" marker.
_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")
# When the description supplies no figure, ANY digit in the body is the model inventing one
# (a fabricated notice period / amount), so this gate is a bare digit search.
_NUMBER_RE = re.compile(r"\d")


class Expect(BaseModel):
    reuse_term: str | None = None
    placeholder: bool = False
    forbid_numbers: bool = False
    empty_body: bool = False


class EvalCase(BaseModel):
    name: str
    description: str
    anchor_node_id: str | None = None
    mode: str = "below"
    expect: Expect


class CaseOutcome(BaseModel):
    name: str
    passed: bool
    detail: str


def _load() -> tuple[str, list[StoredNode], list[EvalCase]]:
    data = json.loads(_DATASET.read_text(encoding="utf-8"))
    nodes = [
        StoredNode(
            id=n["id"],
            parent_id=None,
            order_index=i,
            content_type="prose",
            heading=n["heading"],
            body=n["body"],
        )
        for i, n in enumerate(data["nodes"])
    ]
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    return str(data["deal_type"]), nodes, cases


async def _draft(deal_type: str, nodes: list[StoredNode], case: EvalCase) -> ClauseDraft:
    settings = get_settings()
    labels = build_label_map(nodes)
    anchor_label = (
        labels.get(case.anchor_node_id, "this clause")
        if case.anchor_node_id is not None
        else "the contract (no specific clause selected)"
    )
    prompt = render(
        "clause_draft_v1.txt",
        deal_type=deal_type,
        placement=_PLACEMENT[case.mode],
        anchor=anchor_label,
        context=build_clause_grounding(nodes, case.anchor_node_id, labels)
        or "(no surrounding clause context)",
        description=case.description,
    )
    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="eval_clause_draft",
        max_tokens=settings.llm.clause_draft_max_tokens,
        temperature=settings.llm.clause_draft_temperature,
        json_response=True,
    )
    valid_ids = {n.id for n in nodes}
    return finalize_draft(parse_draft(result.text), valid_ids, labels)


def _score(case: EvalCase, draft: ClauseDraft, valid_ids: set[str]) -> CaseOutcome:
    e = case.expect
    failures: list[str] = []
    body = draft.body or ""
    prose = " ".join(p for p in (draft.heading, body) if p)

    if e.empty_body and body.strip():
        failures.append(f"expected empty body, drafted: {body[:60]!r}")
    if not e.empty_body and not body.strip():
        failures.append("empty body (failed to draft a grounded clause)")
    if e.reuse_term is not None and e.reuse_term not in body:
        failures.append(f"did not reuse the defined term {e.reuse_term!r}")
    if e.placeholder and not _PLACEHOLDER_RE.search(body):
        failures.append("no bracketed [insert …] placeholder for the missing value")
    if e.forbid_numbers and _NUMBER_RE.search(body):
        failures.append("fabricated a figure instead of a placeholder")
    if _UUID_RE.search(prose):
        failures.append("raw UUID in prose")
    leaked = sorted(i for i in valid_ids if i in prose)
    if leaked:
        failures.append(f"leaked id(s) in prose {leaked}")

    return CaseOutcome(
        name=case.name, passed=not failures, detail="ok" if not failures else "; ".join(failures)
    )


async def run() -> int:
    deal_type, nodes, cases = _load()
    valid_ids = {n.id for n in nodes}
    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcomes.append(_score(case, await _draft(deal_type, nodes, case), valid_ids))
    _report(outcomes)
    return 0 if all(o.passed for o in outcomes) else 1


def _report(outcomes: list[CaseOutcome]) -> None:
    print("\nF08d Donna clause-drafting grounding eval  (high tier, real LLM)\n")
    print(f"{'':2} {'case':<52} detail")
    print("-" * 100)
    for o in outcomes:
        print(f"{'ok' if o.passed else 'XX':2} {o.name[:50]:<52} {o.detail}")
    passed = sum(o.passed for o in outcomes)
    print("-" * 100)
    print(f"\nPassed {passed}/{len(outcomes)} risk cases\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
