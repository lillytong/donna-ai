"""Eval harness for the Mode B Path-B clause matcher (F03b).

Scores the PRODUCTION matcher (`match_revision`) against synthetic before/after
pairs with exact gold maps. Unlike `evals/clause_search`, this matcher is PURELY
LEXICAL + STRUCTURAL (token-set Jaccard + anchor/parent/order priors) — no LLM,
no embeddings (SPIKE #3 proved the Haiku identity-scorer was not needed to clear
the gate). So this eval makes NO model calls and is deterministic; it can run in
CI as-is. It reuses the prod surface (`match_revision` + `ClauseNode`) verbatim —
only the dataset loader, scoring, and report are added here.

Metrics (mirroring SPIKE #3 Layer B):
  * auto-match precision — of auto-accepted matches, the fraction equal to gold
    (THE catastrophic-failure metric; target 1.00, zero silent mismatch);
  * abstention rate — true matches the matcher routed to operator (abstain) or
    failed to auto-match, over all true matches (the usability floor);
  * NEW / DELETED precision + recall — genuine adds/deletes not miscategorised.

Run from the repo root:
    python -m evals.mode_b_matching.run_eval
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.models.revision_match import ClauseNode, RevisionMatchResult
from backend.services.import_.revision_match import match_revision
from pydantic import BaseModel

_DATASET = Path(__file__).parent / "dataset.json"


class BaselineNode(BaseModel):
    id: str
    parent: str | None = None
    order: int
    heading: str = ""
    body: str = ""


class IncomingNode(BaseModel):
    order: int
    parent: int | None = None
    heading: str = ""
    body: str = ""


class Gold(BaseModel):
    matches: dict[str, str]  # incoming order (as str) -> baseline id
    new: list[int]
    deleted: list[str]


class Variant(BaseModel):
    name: str
    incoming: list[IncomingNode]
    gold: Gold


class Dataset(BaseModel):
    contract_label: str
    baseline: list[BaselineNode]
    variants: list[Variant]


class VariantScore(BaseModel):
    name: str
    auto_correct: int
    auto_wrong: int
    n_true_matches: int
    abstained_or_missed: int
    new_tp: int
    new_fp: int
    new_fn: int
    del_tp: int
    del_fp: int
    del_fn: int
    wrong_examples: list[tuple[int, str, str]]


def _to_baseline(nodes: list[BaselineNode]) -> list[ClauseNode]:
    return [
        ClauseNode(id=n.id, parent=n.parent, order=n.order, heading=n.heading, body=n.body)
        for n in nodes
    ]


def _to_incoming(nodes: list[IncomingNode]) -> list[ClauseNode]:
    return [
        ClauseNode(id=None, parent=n.parent, order=n.order, heading=n.heading, body=n.body)
        for n in nodes
    ]


def _score_variant(
    baseline: list[ClauseNode], variant: Variant, res: RevisionMatchResult
) -> VariantScore:
    gold_match = {int(k): v for k, v in variant.gold.matches.items()}
    gold_new = set(variant.gold.new)
    gold_del = set(variant.gold.deleted)

    auto = {m.incoming_index: m.baseline_id for m in res.matches}
    auto_correct = auto_wrong = 0
    wrong: list[tuple[int, str, str]] = []
    for idx, bid in auto.items():
        expected = gold_match.get(idx)
        if expected is not None and expected == bid:
            auto_correct += 1
        else:
            auto_wrong += 1
            wrong.append((idx, expected or "NEW", bid))

    # abstention: true-match incoming nodes the matcher did NOT auto-match correctly
    abstained_or_missed = sum(
        1 for idx in gold_match if auto.get(idx) != gold_match[idx]
    )

    pred_new = set(res.new)
    pred_del = set(res.deleted)
    new_tp = len(pred_new & gold_new)
    del_tp = len(pred_del & gold_del)

    return VariantScore(
        name=variant.name,
        auto_correct=auto_correct,
        auto_wrong=auto_wrong,
        n_true_matches=len(gold_match),
        abstained_or_missed=abstained_or_missed,
        new_tp=new_tp,
        new_fp=len(pred_new - gold_new),
        new_fn=len(gold_new - pred_new),
        del_tp=del_tp,
        del_fp=len(pred_del - gold_del),
        del_fn=len(gold_del - pred_del),
        wrong_examples=wrong,
    )


def _pr(tp: int, fp: int, fn: int) -> tuple[float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


def run() -> int:
    data = Dataset.model_validate(json.loads(_DATASET.read_text(encoding="utf-8")))
    baseline = _to_baseline(data.baseline)

    scores: list[VariantScore] = []
    for variant in data.variants:
        res = match_revision(baseline, _to_incoming(variant.incoming))
        scores.append(_score_variant(baseline, variant, res))

    _report(data, scores)
    return 0


def _report(data: Dataset, scores: list[VariantScore]) -> None:
    print(f"\nMode B clause-matcher eval — {data.contract_label}  (lexical, no LLM)\n")
    print(f"{'variant':<28} {'auto ok/wrong':>14} {'true':>5} {'abst':>5}")
    print("-" * 60)
    for s in scores:
        print(
            f"{s.name:<28} {f'{s.auto_correct}/{s.auto_wrong}':>14} "
            f"{s.n_true_matches:>5} {s.abstained_or_missed:>5}"
        )
        for idx, exp, got in s.wrong_examples:
            print(f"    WRONG incoming#{idx}: expected {exp}, matched {got}")

    ac = sum(s.auto_correct for s in scores)
    aw = sum(s.auto_wrong for s in scores)
    ntm = sum(s.n_true_matches for s in scores)
    abst = sum(s.abstained_or_missed for s in scores)
    new_p, new_r = _pr(
        sum(s.new_tp for s in scores), sum(s.new_fp for s in scores), sum(s.new_fn for s in scores)
    )
    del_p, del_r = _pr(
        sum(s.del_tp for s in scores), sum(s.del_fp for s in scores), sum(s.del_fn for s in scores)
    )

    precision = ac / (ac + aw) if (ac + aw) else 1.0
    abstention = abst / ntm if ntm else 0.0

    print("-" * 60)
    print(f"\nAuto-match precision : {precision:.3f}  ({ac} correct, {aw} wrong)")
    print(f"Abstention rate      : {abstention:.1%}  ({abst} of {ntm} true matches routed/missed)")
    print(f"NEW      P / R       : {new_p:.2f} / {new_r:.2f}")
    print(f"DELETED  P / R       : {del_p:.2f} / {del_r:.2f}")
    print()
    gate_ok = precision >= 0.999 and abstention <= 0.15 and new_r >= 0.999 and del_r >= 0.999
    verdict = "PASS" if gate_ok else "REVIEW"
    print(f"Gate (precision=1.00, abstention<=15%, NEW/DELETED recall=1.00): {verdict}\n")


if __name__ == "__main__":
    raise SystemExit(run())
