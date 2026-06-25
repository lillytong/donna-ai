"""Extraction-quality eval for Donna's negotiation-pattern distillation (F30, DD-76).

On ISSUE-CLOSE, Donna distils 0-N compact, transferable negotiation PATTERNS from the
committed issue ledger at the MEDIUM tier. An empty list is the honest, expected output
when a closed issue holds no durable pattern — never manufacture one (DD-76). The
`subject_ref` is DERIVED from the issue's contract context by the service, never from the
model (the LLM only proposes subject_type + insight). This eval gates THAT extraction.

It makes REAL MEDIUM-tier (Sonnet) LLM calls and lives outside `tests/`. It reuses the
production extract/parse path rather than reimplementing it — exactly what
`distillation._distill` runs once the issue + grounding + existing patterns are resolved:
  * grounding builders `build_label_map` / `build_issue_focus` / `build_clause_grounding`
    and `build_existing_block` — prod's exact prompt grounding;
  * the versioned prompt `distill_v1.txt` via `render`;
  * `complete` — the production LiteLLM wrapper (MEDIUM tier, json_response), sourcing
    model id / temperature / api key from `config/settings.distillation`;
  * `parse_distillation` — prod's tolerant parse + honest-empty (empty list) contract;
  * `subject_ref_for` — prod's DETERMINISTIC subject_ref derivation, exercised here to
    confirm the ref is service-derived (never model-supplied) and keys correctly.

Only the harness loop, the synthetic dataset, and scoring are local. The issue, clause
grounding, and contract context (client_id / contract_type_id) are pre-resolved from
`dataset.json` (no DB touched), so the gate isolates the extraction step.

Run from the repo root (so `.env` is found):
    python -m evals.negotiation_patterns.run_eval
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from backend.config.settings import get_settings
from backend.models.imports import StoredNode
from backend.models.insights import CandidatePattern, DistillationResult
from backend.models.issues import StoredIssue
from backend.prompts.utils import render
from backend.services.donna.distillation import (
    build_existing_block,
    parse_distillation,
    subject_ref_for,
)
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

_OPERATOR_GLOBAL = {"operator_style", "legal_team_tendency"}


class Expect(BaseModel):
    expect_patterns: bool = True
    subject_types: list[str] = []


class EvalCase(BaseModel):
    name: str
    client_id: str | None = None
    contract_type_id: str | None = None
    issue: dict[str, object]
    matched_node_id: str | None = None
    expect: Expect


class CaseOutcome(BaseModel):
    name: str
    n_patterns: int
    subject_types: list[str]
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


async def _distill(nodes: list[StoredNode], case: EvalCase) -> DistillationResult:
    """Mirrors distillation._distill's extract/parse once issue + grounding are resolved
    (existing-pattern set left empty — this gate measures extraction, not merge)."""
    settings = get_settings()
    issue = _to_issue(case.issue)
    labels = build_label_map(nodes)
    prompt = render(
        "distill_v1.txt",
        issue=build_issue_focus(issue, labels),
        clauses=build_clause_grounding(nodes, case.matched_node_id, labels)
        or "(no anchored clause — free-floating issue)",
        existing=build_existing_block([]),
    )
    result = await complete(
        tier="medium",
        messages=[{"role": "user", "content": prompt}],
        caller="eval_negotiation_patterns",
        max_tokens=settings.distillation.max_tokens,
        temperature=settings.distillation.temperature,
        json_response=True,
    )
    return parse_distillation(result.text)


def _ref_failure(case: EvalCase, cand: CandidatePattern) -> str | None:
    """The service-derived subject_ref must key by subject_type: operator/legal -> null,
    counterparty -> client_id, deal_type_norm -> contract_type_id. Exercises subject_ref_for
    so the eval confirms the ref is derived by the service, never proposed by the model."""
    derived = subject_ref_for(cand.subject_type, case.client_id, case.contract_type_id)
    if cand.subject_type in _OPERATOR_GLOBAL and derived is not None:
        return f"{cand.subject_type} got a non-null ref {derived!r}"
    if cand.subject_type == "counterparty_behavior" and derived != case.client_id:
        return f"counterparty ref {derived!r} != client_id {case.client_id!r}"
    if cand.subject_type == "deal_type_norm" and derived != case.contract_type_id:
        return f"deal_type_norm ref {derived!r} != contract_type_id {case.contract_type_id!r}"
    return None


def _score(case: EvalCase, result: DistillationResult) -> CaseOutcome:
    e = case.expect
    patterns = result.patterns
    got_types = sorted({p.subject_type for p in patterns})
    failures: list[str] = []

    if not e.expect_patterns:
        if patterns:
            failures.append(f"manufactured {len(patterns)} pattern(s) from a one-off issue")
    else:
        if not patterns:
            failures.append("no pattern extracted from a signal-bearing issue")
        for st in e.subject_types:
            if st not in got_types:
                failures.append(f"missing expected subject_type {st} (got {got_types})")

    for cand in patterns:
        if _UUID_RE.search(cand.insight):
            failures.append("raw UUID in insight")
        if (rf := _ref_failure(case, cand)) is not None:
            failures.append(rf)

    return CaseOutcome(
        name=case.name,
        n_patterns=len(patterns),
        subject_types=got_types,
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
    print("\nF30 negotiation-pattern distillation extraction eval  (medium tier, real LLM)\n")
    print(f"{'':2} {'case':<46} {'n':<3} {'subject_types':<24} detail")
    print("-" * 110)
    for o in outcomes:
        print(
            f"{'ok' if o.passed else 'XX':2} {o.name[:44]:<46} {o.n_patterns:<3} "
            f"{','.join(o.subject_types)[:22]:<24} {o.detail}"
        )
    passed = sum(o.passed for o in outcomes)
    print("-" * 110)
    print(f"\nPassed {passed}/{len(outcomes)} extraction cases\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
