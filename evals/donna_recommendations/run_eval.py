"""Grounding eval for Donna's issue-recommendation layer (F11) — THE risk gate (§2.4 /
DD-68: a wrong, ungrounded, or fabricated recommendation costs credibility and can leak
into an export). Makes REAL capable-tier (high/Opus) LLM calls and lives outside `tests/`.

It reuses the production surface, not a re-implementation:
  * `build_issue_focus` / `build_clause_grounding` / `build_issue_ledger` — prod's exact
    id-tagged grounding;
  * the versioned prompt `donna_recommendation_v1.txt` via `render`;
  * `parse_draft` + `finalize_draft` — prod's tolerant parse + citation guard + id scrub;
  * `complete` — the production LiteLLM wrapper (high tier, json_response).

Retrieval (the F05b clause lookup) is evaluated separately; here the clause is pre-resolved
per case so the gate isolates THIS surface's risk: is the recommendation grounded and cited,
propose-vs-counter correct, framed on the reasonableness spectrum, and HONEST about a missing
market benchmark — never a fabricated number.

Run from the repo root (so `.env` is found):
    python -m evals.donna_recommendations.run_eval
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from backend.config.settings import get_settings
from backend.models.imports import StoredNode
from backend.models.issues import StoredIssue
from backend.models.recommendations import RecommendationDraft
from backend.prompts.utils import render
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_focus,
    build_issue_ledger,
    build_label_map,
)
from backend.services.donna.recommendations import finalize_draft, parse_draft
from backend.services.llm import complete
from pydantic import BaseModel

_DATASET = Path(__file__).parent / "dataset.json"

_HEX = "[0-9a-fA-F]"
_UUID_RE = re.compile(f"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}")
# A fabricated market figure: a number bound to a rate/percentage/currency marker. The
# clause prose ("twelve months", "thirty days") is spelled out, so a digit+% / $ in the
# recommendation is the model inventing a benchmark it was never given.
_FABRICATED_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*%|[$€£]\s*\d|\bbasis points\b", re.IGNORECASE)
_SPECTRUM_TERMS = (
    "favorable",
    "fair",
    "aggressive",
    "deal-breaking",
    "walk",
    "ask",
    "settle",
    "floor",
    "reasonable",
)


class Expect(BaseModel):
    must_cite: str | None = None
    counter: bool = False
    position: bool = False
    missing_benchmark: bool = False
    forbid_numbers: bool = False
    spectrum: bool = False


class EvalCase(BaseModel):
    name: str
    issue: dict[str, object]
    matched_node_id: str | None = None
    expect: Expect


class CaseOutcome(BaseModel):
    name: str
    passed: bool
    detail: str


def _to_issue(raw: dict[str, object]) -> StoredIssue:
    return StoredIssue(
        id=str(raw["id"]),
        contract_id="c-eval",
        node_id=raw.get("node_id"),  # type: ignore[arg-type]
        title=str(raw["title"]),
        status=str(raw["status"]),
        initiator=str(raw["initiator"]),
        our_position=raw.get("our_position"),  # type: ignore[arg-type]
        their_position=raw.get("their_position"),  # type: ignore[arg-type]
        options_on_table=raw.get("options_on_table"),  # type: ignore[arg-type]
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        created_at=datetime(2026, 1, 1),
    )


def _load() -> tuple[list[StoredNode], list[StoredIssue], list[EvalCase]]:
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
    ledger = [_to_issue(s) for s in data["ledger_issues"]]
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    return nodes, ledger, cases


async def _recommend(
    nodes: list[StoredNode], ledger: list[StoredIssue], case: EvalCase
) -> RecommendationDraft:
    settings = get_settings()
    issue = _to_issue(case.issue)
    all_issues = [*ledger, issue]
    labels = build_label_map(nodes)
    prompt = render(
        "donna_recommendation_v1.txt",
        clauses=build_clause_grounding(nodes, case.matched_node_id, labels)
        or "(no anchored clause resolved)",
        issue=build_issue_focus(issue, labels),
        ledger=build_issue_ledger(ledger, labels) or "(no other issues on record)",
    )
    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="eval_donna_recommendation",
        max_tokens=settings.llm.donna_recommendation_max_tokens,
        temperature=settings.llm.donna_recommendation_temperature,
        json_response=True,
    )
    valid_ids = {n.id for n in nodes} | {i.id for i in all_issues}
    id_labels = {**labels, **{i.id: i.title for i in all_issues}}
    return finalize_draft(parse_draft(result.text), valid_ids, id_labels)


def _score(case: EvalCase, draft: RecommendationDraft, valid_ids: set[str]) -> CaseOutcome:
    e = case.expect
    failures: list[str] = []
    prose = " ".join(
        p
        for p in (draft.rationale, draft.draft_recommended_position, draft.draft_counter_language)
        if p
    )

    if e.must_cite is not None and e.must_cite not in draft.citations:
        failures.append(f"missing citation {e.must_cite} (got {draft.citations})")
    if e.counter and not (draft.draft_counter_language or "").strip():
        failures.append("no draft_counter_language")
    if e.position and not (draft.draft_recommended_position or "").strip():
        failures.append("no draft_recommended_position")
    if e.missing_benchmark and not draft.missing_benchmark:
        failures.append("did not flag missing_benchmark")
    if e.forbid_numbers and _FABRICATED_NUMBER_RE.search(prose):
        failures.append("fabricated market number")
    if e.spectrum and not any(t in draft.rationale.lower() for t in _SPECTRUM_TERMS):
        failures.append("no reasonableness-spectrum framing")
    if _UUID_RE.search(prose):
        failures.append("raw UUID in prose")
    leaked = sorted(i for i in valid_ids if i in prose)
    if leaked:
        failures.append(f"leaked id(s) in prose {leaked}")

    return CaseOutcome(
        name=case.name, passed=not failures, detail="ok" if not failures else "; ".join(failures)
    )


async def run() -> int:
    nodes, ledger, cases = _load()
    outcomes: list[CaseOutcome] = []
    for case in cases:
        valid_ids = {n.id for n in nodes} | {i.id for i in ledger} | {str(case.issue["id"])}
        outcomes.append(_score(case, await _recommend(nodes, ledger, case), valid_ids))
    _report(outcomes)
    return 0 if all(o.passed for o in outcomes) else 1


def _report(outcomes: list[CaseOutcome]) -> None:
    print("\nF11 Donna recommendation grounding eval  (high tier, real LLM)\n")
    print(f"{'':2} {'case':<52} detail")
    print("-" * 100)
    for o in outcomes:
        print(f"{'ok' if o.passed else 'XX':2} {o.name[:50]:<52} {o.detail}")
    passed = sum(o.passed for o in outcomes)
    print("-" * 100)
    print(f"\nPassed {passed}/{len(outcomes)} risk cases\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
