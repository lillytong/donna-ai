"""Layer-A mechanical oracle for the Mode B matcher — permanent regression gate.

Mirrors `tests/system/test_export_roundtrip.py`: an always-runs SYNTHETIC fixture is
the gate; the gitignored real spike pair runs only when present (skipif). Layer A
needs NO ground truth — these invariants must hold for ANY input, so they catch
dropped/duplicated content, threshold drift, and normalisation bugs without a
labelled key (accuracy-vs-gold is the separate `evals/mode_b_matching` job).

The four invariants (SPIKE #3 Layer A, ported):
  (1) injectivity        — no baseline node claimed by two incoming nodes;
  (2) partition          — |incoming| = matched + new, |baseline| = matched + deleted;
  (3) reconstruction     — applying the implied diff to the baseline yields incoming;
  (4) self-match no-op    — matching a tree against itself -> 0 new/deleted/abstain.

Invariants are computed INDEPENDENTLY here (not via the service's own oracle), then
cross-checked against `layer_a_invariants` so a matcher bug and an oracle bug can't
agree silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from backend.models.revision_match import ClauseNode, RevisionMatchResult
from backend.services.import_.revision_match import (
    _norm,
    layer_a_invariants,
    match_revision,
    self_match_noop,
)

_SPIKE = Path(__file__).resolve().parents[2] / "spikes" / "mode_b_matching"


# --------------------------------------------------------------------------- #
# Always-runs synthetic fixture (privacy-safe, committable)                     #
# --------------------------------------------------------------------------- #

_ROYALTY_BASE = "royalty of ten percent of net revenue payable annually"
_AUDIT_INTEREST_BASE = "underpayment accrues interest at one point five percent per month"
_CONF_BASE = (
    "each party keeps the other party confidential information secret and "
    "uses it only to perform this agreement"
)
_ROYALTY_REWORDED = "royalty of ten percent of net collected revenue payable annually in arrears"
_CONF_HEAVY_REWORD = (
    "neither party shall reveal proprietary data save where compelled "
    "by applicable law or court order"
)


def _baseline_fixture() -> list[ClauseNode]:
    """A small but adversarial baseline: a duplicate-title section/sub-clause pair,
    body-only and heading clauses, three depth levels."""
    return [
        ClauseNode(id="grant", parent=None, order=0, heading="Grant of Licence"),
        ClauseNode(id="consid", parent=None, order=1, heading="Consideration"),
        ClauseNode(id="royalty", parent="consid", order=2, body=_ROYALTY_BASE),
        ClauseNode(id="audit", parent=None, order=3, heading="Audit Rights"),
        ClauseNode(id="audit-interest", parent="audit", order=4, body=_AUDIT_INTEREST_BASE),
        ClauseNode(id="indem-sec", parent=None, order=5, heading="Indemnity"),
        ClauseNode(id="indem-sub", parent="indem-sec", order=6, heading="Indemnity"),
        ClauseNode(id="conf", parent=None, order=7, body=_CONF_BASE),
    ]


def _incoming_fixture() -> list[ClauseNode]:
    """Counterparty revision: upstream insert (renumber), a light reword, a heavy
    reword, a deletion, an addition, and the duplicate-title pair preserved."""
    return [
        ClauseNode(id=None, parent=None, order=0, heading="Grant of Licence"),  # anchor
        ClauseNode(id=None, parent=None, order=1, heading="Conditions Precedent"),  # NEW upstream
        ClauseNode(id=None, parent=None, order=2, heading="Consideration"),  # anchor (renumbered)
        # order 3: light reword of the royalty body (still matches)
        ClauseNode(id=None, parent=2, order=3, body=_ROYALTY_REWORDED),
        ClauseNode(id=None, parent=None, order=4, heading="Audit Rights"),  # anchor
        # audit-interest DELETED
        ClauseNode(id=None, parent=None, order=5, heading="Indemnity"),  # section
        ClauseNode(id=None, parent=5, order=6, heading="Indemnity"),  # sub-clause
        # order 7: heavy reword of the confidentiality body
        ClauseNode(id=None, parent=None, order=7, body=_CONF_HEAVY_REWORD),
        ClauseNode(id=None, parent=None, order=8, heading="Insurance"),  # NEW
    ]


# --------------------------------------------------------------------------- #
# Independent invariant computation                                             #
# --------------------------------------------------------------------------- #


def _resolved_pairs(res: RevisionMatchResult) -> list[tuple[int, str]]:
    """matches + abstains (resolved to best candidate) as (incoming_idx, baseline_id)."""
    pairs = [(m.incoming_index, m.baseline_id) for m in res.matches]
    pairs += [
        (a.incoming_index, a.best_baseline_id)
        for a in res.abstains
        if a.best_baseline_id is not None
    ]
    return pairs


def _check_injectivity(res: RevisionMatchResult) -> bool:
    claimed = [b for _, b in _resolved_pairs(res)]
    return len(claimed) == len(set(claimed))


def _check_partition(
    baseline: list[ClauseNode], incoming: list[ClauseNode], res: RevisionMatchResult
) -> tuple[bool, bool]:
    matched_incoming = {i for i, _ in _resolved_pairs(res)}
    new_set = set(res.new)
    incoming_keys = {n.order for n in incoming}
    part_in = (
        matched_incoming.isdisjoint(new_set)
        and (matched_incoming | new_set) == incoming_keys
        and (len(matched_incoming) + len(new_set)) == len(incoming_keys)
    )

    matched_baseline = {b for _, b in _resolved_pairs(res)}
    deleted = set(res.deleted)
    baseline_keys = {n.id for n in baseline if n.id is not None}
    part_b = (
        matched_baseline.isdisjoint(deleted)
        and (matched_baseline | deleted) == baseline_keys
        and (len(matched_baseline) + len(deleted)) == len(baseline_keys)
    )
    return part_in, part_b


def _check_reconstruction(
    baseline: list[ClauseNode], incoming: list[ClauseNode], res: RevisionMatchResult
) -> bool:
    """Apply the implied diff to the baseline and require it to equal the incoming
    document (by normalized text): every matched baseline takes its incoming partner's
    text, new nodes insert, deleted nodes drop — so the reconstruction is exactly the
    incoming reading sequence, and every baseline is accounted as matched-or-deleted."""
    ordered = sorted(incoming, key=lambda x: x.order)
    recon = [_norm(n.heading or n.body) for n in ordered]
    target = [_norm(n.heading or n.body) for n in ordered]

    matched_baseline = {b for _, b in _resolved_pairs(res)}
    deleted = set(res.deleted)
    baseline_keys = {n.id for n in baseline if n.id is not None}
    bucket_ok = (matched_baseline | deleted) == baseline_keys
    return recon == target and bucket_ok and len(recon) == len(incoming)


def _assert_layer_a(baseline: list[ClauseNode], incoming: list[ClauseNode]) -> None:
    res = match_revision(baseline, incoming)

    inj = _check_injectivity(res)
    part_in, part_b = _check_partition(baseline, incoming, res)
    recon = _check_reconstruction(baseline, incoming, res)

    assert inj, "Layer-A (1) injectivity FAILED — a baseline node claimed twice"
    assert part_in, "Layer-A (2) incoming partition FAILED — matched + new != |incoming|"
    assert part_b, "Layer-A (2) baseline partition FAILED — matched + deleted != |baseline|"
    assert recon, "Layer-A (3) reconstruction round-trip FAILED"

    # cross-check against the service's own oracle — they must agree
    report = layer_a_invariants(baseline, incoming, res)
    assert report.passed
    assert report.injectivity == inj
    assert report.partition_incoming == part_in
    assert report.partition_baseline == part_b
    assert report.roundtrip == recon


# --------------------------------------------------------------------------- #
# Tests                                                                         #
# --------------------------------------------------------------------------- #


def test_layer_a_holds_on_synthetic_fixture() -> None:
    _assert_layer_a(_baseline_fixture(), _incoming_fixture())


def test_self_match_is_a_noop() -> None:
    """Layer-A (4): a document matched against itself yields zero new/deleted/abstain."""
    baseline = _baseline_fixture()
    report = self_match_noop(baseline)
    assert report.passed, f"self-match was not a no-op: {report.model_dump()}"

    # and independently: re-key the baseline as an incoming draft (ids stripped) and
    # confirm the matcher reproduces it exactly.
    id_to_order = {n.id: n.order for n in baseline}
    incoming = [
        ClauseNode(
            id=None,
            parent=None if n.parent is None else id_to_order[str(n.parent)],
            order=n.order,
            heading=n.heading,
            body=n.body,
        )
        for n in baseline
    ]
    res = match_revision(baseline, incoming)
    assert res.new == [] and res.deleted == [] and res.abstains == []
    assert len(res.matches) == len(baseline)
    _assert_layer_a(baseline, incoming)


# --------------------------------------------------------------------------- #
# Optional: the gitignored real spike pair (offline cached preview), if present #
# --------------------------------------------------------------------------- #


def _spike_real_pair() -> tuple[list[ClauseNode], list[ClauseNode]] | None:
    """Reuse the spike's gitignored artifacts IF present: `baseline_tree.json` (the
    BEFORE tree) + a cached importer preview `_preview_<v>.json` (the parsed AFTER).
    These are produced offline by the spike runner; absent in a clean checkout."""
    bt_path = _SPIKE / "baseline_tree.json"
    if not bt_path.exists():
        return None
    previews = sorted(_SPIKE.glob("_preview_*.json"))
    if not previews:
        return None

    bt = json.loads(bt_path.read_text(encoding="utf-8"))
    baseline: list[ClauseNode] = []
    order = [0]

    def walk(nodes: list[dict[str, Any]], parent_id: str | None) -> None:
        for n in nodes:
            if n.get("role") == "clause":
                baseline.append(
                    ClauseNode(
                        id=n["id"],
                        parent=parent_id,
                        order=order[0],
                        heading=(n.get("heading") or "").strip(),
                        body=(n.get("body") or "").strip(),
                    )
                )
                order[0] += 1
                walk(n.get("children") or [], n["id"])
            else:
                walk(n.get("children") or [], parent_id)

    walk(bt["nodes"], None)

    payload = json.loads(previews[0].read_text(encoding="utf-8"))
    flat: list[dict[str, Any]] = []

    def rec(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            flat.append(n)
            rec(n.get("children") or [])

    rec(payload["nodes"])
    by_index: dict[int, dict[str, Any]] = {n["index"]: n for n in flat}
    clause_order: dict[int, int] = {}
    o = 0
    for n in flat:
        if n.get("role") == "clause":
            clause_order[n["index"]] = o
            o += 1

    def parent_clause_order(n: dict[str, Any]) -> int | None:
        p = n.get("parent_index")
        while p is not None and p in by_index:
            pn = by_index[p]
            if pn.get("role") == "clause":
                return clause_order.get(pn["index"])
            p = pn.get("parent_index")
        return None

    incoming: list[ClauseNode] = []
    for n in flat:
        if n.get("role") != "clause":
            continue
        incoming.append(
            ClauseNode(
                id=None,
                parent=parent_clause_order(n),
                order=clause_order[n["index"]],
                heading=(n.get("heading") or "").strip(),
                body=(n.get("body") or "").strip(),
            )
        )
    return baseline, incoming


@pytest.mark.skipif(
    _spike_real_pair() is None, reason="gitignored spike real pair / cached preview absent"
)
def test_layer_a_holds_on_real_spike_pair() -> None:
    pair = _spike_real_pair()
    assert pair is not None
    baseline, incoming = pair
    _assert_layer_a(baseline, incoming)
