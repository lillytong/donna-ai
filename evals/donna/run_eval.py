"""Grounding eval for Donna single-contract Q&A (F10) — the RISK gate (§2.4: a wrong or
ungrounded answer costs credibility). Makes REAL capable-tier (medium/Sonnet) LLM calls
and lives outside `tests/`.

It reuses the production surface wherever possible rather than reimplementing it:
  * `build_clause_grounding` / `build_issue_ledger` — prod's exact id-tagged grounding;
  * the versioned prompt `donna_qa_v3.txt` via `render`;
  * `parse_answer` — prod's tolerant structured-output parse;
  * `complete` — the production LiteLLM wrapper (medium tier, json_response).

Retrieval itself (the F05b clause lookup) is evaluated separately in evals/clause_search;
here the matched clause is pre-resolved per case so the gate isolates THIS surface's risk:
does Donna answer ONLY from the grounding, cite the right id, deflect advice, and admit a
miss honestly.

Four case types: (a) in-contract -> cites the correct clause; (b) out-of-contract ->
honest not_found, no fabricated citations; (c) advice/position -> deflected, never advises;
(d) status-briefing -> grounded in the issue ledger.

Run from the repo root (so `.env` is found):
    python -m evals.donna.run_eval
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from backend.config.settings import get_settings
from backend.models.donna import DonnaStructuredAnswer
from backend.models.imports import StoredNode
from backend.models.issues import StoredIssue
from backend.prompts.utils import render
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_ledger,
    build_label_map,
)
from backend.services.donna.qa import parse_answer, scrub_leaked_ids
from backend.services.llm import complete
from pydantic import BaseModel

_DATASET = Path(__file__).parent / "dataset.json"

# Raw UUID shape (the live bug: "referencing clause 6161c90b-04bd-..."). The scrubbed
# answer must never contain one, nor any of the grounding's known node/issue ids.
_HEX = "[0-9a-fA-F]"
_UUID_RE = re.compile(f"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}")


class EvalCase(BaseModel):
    name: str
    question: str
    matched_node_id: str | None = None
    expected_kind: str
    must_cite: str | None = None
    forbid_citations: bool = False


class CaseOutcome(BaseModel):
    name: str
    expected_kind: str
    got_kind: str
    citations: list[str]
    passed: bool
    detail: str


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
    issues = [
        StoredIssue(
            id=s["id"],
            contract_id="c-eval",
            node_id=s["node_id"],
            title=s["title"],
            status=s["status"],
            our_position=s["our_position"],
            their_position=s["their_position"],
            initiator="operator",
            authority="within-operator-authority",
            needs_legal_review=False,
            category="commercial",
            created_at=datetime(2026, 1, 1),
        )
        for s in data["issues"]
    ]
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    return nodes, issues, cases


async def _answer(
    nodes: list[StoredNode], issues: list[StoredIssue], case: EvalCase
) -> DonnaStructuredAnswer:
    settings = get_settings()
    labels = build_label_map(nodes)
    prompt = render(
        "donna_qa_v3.txt",
        clauses=build_clause_grounding(nodes, case.matched_node_id, labels)
        or "(no matching clause found)",
        issues=build_issue_ledger(issues, labels) or "(no issues on record)",
        summary="(none)",
        history="(no earlier conversation)",
        question=case.question,
    )
    result = await complete(
        tier="medium",
        messages=[{"role": "user", "content": prompt}],
        caller="eval_donna_qa",
        max_tokens=settings.llm.donna_qa_max_tokens,
        temperature=settings.llm.donna_qa_temperature,
        json_response=True,
    )
    return parse_answer(result.text)


def _score(
    case: EvalCase,
    answer: DonnaStructuredAnswer,
    valid_ids: set[str],
    id_labels: dict[str, str],
) -> CaseOutcome:
    citations = [c for c in answer.citations if c in valid_ids]
    failures: list[str] = []
    if answer.kind != case.expected_kind:
        failures.append(f"kind={answer.kind} (want {case.expected_kind})")
    if case.must_cite is not None and case.must_cite not in citations:
        failures.append(f"missing citation {case.must_cite}")
    if case.forbid_citations and citations:
        failures.append(f"fabricated/extra citations {citations}")
    # The shipped prose (after the qa scrub) must carry no raw id in any form.
    prose = scrub_leaked_ids(answer.answer, id_labels)
    if _UUID_RE.search(prose):
        failures.append("raw UUID in prose")
    leaked = sorted(i for i in valid_ids if i in prose)
    if leaked:
        failures.append(f"leaked id(s) in prose {leaked}")
    return CaseOutcome(
        name=case.name,
        expected_kind=case.expected_kind,
        got_kind=answer.kind,
        citations=citations,
        passed=not failures,
        detail="ok" if not failures else "; ".join(failures),
    )


async def run() -> int:
    nodes, issues, cases = _load()
    valid_ids = {n.id for n in nodes} | {i.id for i in issues}
    id_labels = {**build_label_map(nodes), **{i.id: i.title for i in issues}}
    outcomes = [_score(c, await _answer(nodes, issues, c), valid_ids, id_labels) for c in cases]
    _report(outcomes)
    return 0 if all(o.passed for o in outcomes) else 1


def _report(outcomes: list[CaseOutcome]) -> None:
    print("\nF10 Donna Q&A grounding eval  (medium tier, real LLM)\n")
    print(f"{'':2} {'case':<48} {'kind':<12} {'citations':<18} detail")
    print("-" * 100)
    for o in outcomes:
        mark = "ok" if o.passed else "XX"
        print(f"{mark:2} {o.name[:46]:<48} {o.got_kind:<12} {str(o.citations):<18} {o.detail}")
    passed = sum(o.passed for o in outcomes)
    print("-" * 100)
    print(f"\nPassed {passed}/{len(outcomes)} risk cases\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
