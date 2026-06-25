"""Distillation-quality eval for Donna's brainstorm close-summary (F10b, DD-73/DD-77).

On close, Donna distils a (client-held) brainstorm transcript into ONE compact
`BrainstormSummary {question, conclusion, fallbacks}` at the MEDIUM tier, or honestly
returns None when the brainstorm was dismissed without substantive exploration (never
fabricate, §2.4). This eval gates THAT distillation quality.

It makes REAL MEDIUM-tier (Sonnet) LLM calls and lives outside `tests/`. It reuses the
production surface rather than reimplementing it — exactly the path
`brainstorm.distill_brainstorm_summary` runs once its issue + clause grounding are
resolved:
  * grounding builders `build_label_map` / `build_issue_focus` / `build_clause_grounding`;
  * `render_transcript` — prod's `You: … / Donna: …` transcript renderer;
  * the versioned prompt `distill_brainstorm_v1.txt` via `render`;
  * `complete` — the production LiteLLM wrapper (MEDIUM tier, json_response), which
    sources model id / temperature / api key from `config/settings`;
  * `parse_summary` — prod's tolerant parse + honest-empty (None) contract.

Only the harness loop, the synthetic dataset, and scoring are local. The issue + clause
context are pre-resolved from `dataset.json` (no DB touched), so the gate isolates the
distillation step. Scoring is structural/keyword (mirrors the recommendation + advise
evals): does the summary capture the question explored, the position landed, and the
fallbacks weighed — or correctly distil to None.

Run from the repo root (so `.env` is found):
    python -m evals.brainstorm_distill.run_eval
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from backend.config.settings import get_settings
from backend.models.brainstorm import BrainstormSummary
from backend.models.donna import DonnaTurn
from backend.models.imports import StoredNode
from backend.models.issues import StoredIssue
from backend.prompts.utils import render
from backend.services.donna.brainstorm import parse_summary, render_transcript
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_focus,
    build_label_map,
)
from backend.services.llm import complete
from pydantic import BaseModel

_DATASET = Path(__file__).parent / "dataset.json"

_HEX = "[0-9a-fA-F]"
_UUID_RE = re.compile(f"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}")


class Expect(BaseModel):
    produces_summary: bool = True
    question_terms: list[str] = []
    conclusion_terms: list[str] = []
    require_fallback: bool = False
    fallbacks_terms: list[str] = []


class EvalCase(BaseModel):
    name: str
    issue: dict[str, object]
    matched_node_id: str | None = None
    transcript: list[DonnaTurn] = []
    expect: Expect


class CaseOutcome(BaseModel):
    name: str
    produced: bool
    passed: bool
    detail: str


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _to_issue(raw: dict[str, object]) -> StoredIssue:
    return StoredIssue(
        id=str(raw["id"]),
        contract_id="c-eval",
        node_id=_opt_str(raw.get("node_id")),
        title=str(raw["title"]),
        status=str(raw["status"]),
        initiator=str(raw["initiator"]),
        our_position=_opt_str(raw.get("our_position")),
        their_position=_opt_str(raw.get("their_position")),
        options_on_table=_opt_str(raw.get("options_on_table")),
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        created_at=datetime(2026, 1, 1),
    )


def _load() -> tuple[list[StoredNode], list[EvalCase]]:
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
    return nodes, cases


async def _distill(nodes: list[StoredNode], case: EvalCase) -> BrainstormSummary | None:
    """Mirrors brainstorm.distill_brainstorm_summary once issue + grounding are resolved."""
    settings = get_settings()
    issue = _to_issue(case.issue)
    labels = build_label_map(nodes)
    prompt = render(
        "distill_brainstorm_v1.txt",
        issue=build_issue_focus(issue, labels),
        clauses=build_clause_grounding(nodes, case.matched_node_id, labels)
        or "(no anchored clause — free-floating issue)",
        transcript=render_transcript(case.transcript),
    )
    result = await complete(
        tier="medium",
        messages=[{"role": "user", "content": prompt}],
        caller="eval_brainstorm_distill",
        max_tokens=settings.llm.brainstorm_distill_max_tokens,
        temperature=settings.llm.brainstorm_distill_temperature,
        json_response=True,
    )
    return parse_summary(result.text)


def _hits(terms: list[str], text: str) -> bool:
    low = text.lower()
    return any(t.lower() in low for t in terms)


def _score(case: EvalCase, summary: BrainstormSummary | None) -> CaseOutcome:
    e = case.expect

    if not e.produces_summary:
        passed = summary is None
        detail = "ok (honest None)" if passed else "manufactured a summary on a dismissed turn"
        return CaseOutcome(
            name=case.name, produced=summary is not None, passed=passed, detail=detail
        )

    failures: list[str] = []
    if summary is None:
        return CaseOutcome(
            name=case.name,
            produced=False,
            passed=False,
            detail="distilled to None on a substantive brainstorm",
        )

    prose = " ".join((summary.question, summary.conclusion, summary.fallbacks))
    if e.question_terms and not _hits(e.question_terms, summary.question):
        failures.append(f"question missed {e.question_terms}")
    if e.conclusion_terms and not _hits(e.conclusion_terms, summary.conclusion):
        failures.append(f"conclusion missed the landing {e.conclusion_terms}")
    if e.require_fallback and not summary.fallbacks.strip():
        failures.append("no fallback recorded")
    if e.fallbacks_terms and not _hits(e.fallbacks_terms, summary.fallbacks):
        failures.append(f"fallbacks missed the rejected option {e.fallbacks_terms}")
    if _UUID_RE.search(prose):
        failures.append("raw UUID in summary")

    return CaseOutcome(
        name=case.name,
        produced=True,
        passed=not failures,
        detail="ok" if not failures else "; ".join(failures),
    )


async def run() -> int:
    nodes, cases = _load()
    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcomes.append(_score(case, await _distill(nodes, case)))
    _report(outcomes)
    return 0 if all(o.passed for o in outcomes) else 1


def _report(outcomes: list[CaseOutcome]) -> None:
    print("\nF10b brainstorm close-summary distillation eval  (medium tier, real LLM)\n")
    print(f"{'':2} {'case':<48} {'summary?':<9} detail")
    print("-" * 100)
    for o in outcomes:
        produced = "yes" if o.produced else "none"
        print(f"{'ok' if o.passed else 'XX':2} {o.name[:46]:<48} {produced:<9} {o.detail}")
    passed = sum(o.passed for o in outcomes)
    print("-" * 100)
    print(f"\nPassed {passed}/{len(outcomes)} distillation cases\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
