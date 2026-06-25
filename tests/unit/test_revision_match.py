"""Unit tests for the Mode B Path-B clause matcher (F03b).

Small hand-built SYNTHETIC clause trees (no real contract content — privacy-safe,
committable, independent of the gitignored spike data). Each test pins one of the
greenlit behaviours the spike proved: anchor lock, reword->match, renumber->match
(number is weak), the duplicate-title depth-disambiguation regression (the
catastrophic swap the spike caught), heavy-reword->abstain, add->new, delete->
deleted, and injectivity.
"""

from __future__ import annotations

from backend.models.revision_match import ClauseNode
from backend.services.import_.revision_match import (
    TAU_HIGH,
    TAU_LOW,
    match_revision,
)


def _b(
    id: str, order: int, *, heading: str = "", body: str = "", parent: str | None = None
) -> ClauseNode:
    """Baseline node (stable id)."""
    return ClauseNode(id=id, parent=parent, order=order, heading=heading, body=body)


def _r(order: int, *, heading: str = "", body: str = "", parent: int | None = None) -> ClauseNode:
    """Incoming node (no id; parent is the parent clause's order)."""
    return ClauseNode(id=None, parent=parent, order=order, heading=heading, body=body)


def _matched(result: object) -> dict[int, str]:
    return {m.incoming_index: m.baseline_id for m in result.matches}  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Anchor lock                                                                   #
# --------------------------------------------------------------------------- #


def test_anchor_lock_identical_text_matches() -> None:
    baseline = [
        _b("p1", 0, heading="Payment"),
        _b("c1", 1, heading="Confidentiality"),
    ]
    incoming = [
        _r(0, heading="Payment"),
        _r(1, heading="Confidentiality"),
    ]
    res = match_revision(baseline, incoming)
    assert _matched(res) == {0: "p1", 1: "c1"}
    assert res.new == [] and res.deleted == [] and res.abstains == []
    # anchor-locked exact matches report full confidence
    assert all(m.confidence == 1.0 for m in res.matches)


# --------------------------------------------------------------------------- #
# Reword -> still matches (Jaccard above the high bar)                          #
# --------------------------------------------------------------------------- #


def test_light_reword_body_matches() -> None:
    baseline = [_b("p1", 0, body="one two three four five six seven eight nine ten")]
    # 9/11 tokens shared -> Jaccard ~0.82 -> well above TAU_HIGH
    incoming = [_r(0, body="one two three four five six seven eight nine eleven")]
    res = match_revision(baseline, incoming)
    assert _matched(res) == {0: "p1"}
    assert res.abstains == [] and res.new == [] and res.deleted == []
    assert res.matches[0].confidence >= TAU_HIGH


# --------------------------------------------------------------------------- #
# Renumber -> still matches (number is a WEAK signal, never a decision)         #
# --------------------------------------------------------------------------- #


def test_renumber_reorder_matches_by_text_not_number() -> None:
    """Swapping reading order (so derived dotted numbers swap) must NOT swap the
    match — identity follows text, not number (the counterparty-renumber stress)."""
    baseline = [
        _b("pay", 0, heading="Payment"),
        _b("conf", 1, heading="Confidentiality"),
    ]
    # incoming presents them in the opposite order -> derived numbers flip
    incoming = [
        _r(0, heading="Confidentiality"),
        _r(1, heading="Payment"),
    ]
    res = match_revision(baseline, incoming)
    assert _matched(res) == {0: "conf", 1: "pay"}
    assert res.new == [] and res.deleted == [] and res.abstains == []


# --------------------------------------------------------------------------- #
# CRITICAL regression: duplicate-title depth disambiguation                     #
# --------------------------------------------------------------------------- #


def test_duplicate_title_disambiguates_by_depth_not_order() -> None:
    """The catastrophic case the spike caught: a section and an identically-titled
    sub-clause. After an upstream insert renumbers everything, order alone would
    silently swap the section with its sub-clause; depth-first disambiguation keeps
    each mapped to its own baseline. (SPIKE #3 carry-forward (c).)"""
    baseline = [
        _b("grant", 0, heading="Grant of Licence"),
        _b("indem-sec", 1, heading="Indemnity"),  # the SECTION (depth 0)
        _b("indem-sub", 2, heading="Indemnity", parent="indem-sec"),  # sub-clause (depth 1)
    ]
    # counterparty inserts a new top-level section -> downstream orders all shift,
    # but the two "Indemnity" nodes keep their relative depths.
    incoming = [
        _r(0, heading="Grant of Licence"),
        _r(1, heading="Conditions Precedent"),  # NEW upstream insert
        _r(2, heading="Indemnity"),  # section, depth 0
        _r(3, heading="Indemnity", parent=2),  # sub-clause, depth 1
    ]
    res = match_revision(baseline, incoming)
    m = _matched(res)
    assert m[2] == "indem-sec", "section must map to the section, not the sub-clause"
    assert m[3] == "indem-sub", "sub-clause must map to the sub-clause, not the section"
    assert m[0] == "grant"
    assert res.new == [1]
    assert res.deleted == []


# --------------------------------------------------------------------------- #
# Heavy reword -> abstain (between the bars, never silently auto-committed)      #
# --------------------------------------------------------------------------- #


def test_heavy_reword_abstains() -> None:
    # 5/15 tokens shared -> Jaccard ~0.33 -> composite score lands in [TAU_LOW, TAU_HIGH)
    baseline = [_b("x", 0, body="one two three four five six seven eight nine ten")]
    incoming = [_r(0, body="one two three four five aaa bbb ccc ddd eee")]
    res = match_revision(baseline, incoming)
    assert res.matches == []
    assert res.new == [] and res.deleted == []
    assert len(res.abstains) == 1
    ab = res.abstains[0]
    assert ab.incoming_index == 0
    assert ab.best_baseline_id == "x"
    assert TAU_LOW <= ab.confidence < TAU_HIGH


# --------------------------------------------------------------------------- #
# Add -> new ; Delete -> deleted                                                #
# --------------------------------------------------------------------------- #


def test_added_clause_is_new() -> None:
    baseline = [_b("p1", 0, heading="Payment")]
    incoming = [
        _r(0, heading="Payment"),
        _r(1, heading="Insurance"),  # no baseline counterpart
    ]
    res = match_revision(baseline, incoming)
    assert _matched(res) == {0: "p1"}
    assert res.new == [1]
    assert res.deleted == [] and res.abstains == []


def test_deleted_clause_is_deleted() -> None:
    baseline = [
        _b("p1", 0, heading="Payment"),
        _b("gone", 1, heading="Audit Underpayment Interest"),
    ]
    incoming = [_r(0, heading="Payment")]
    res = match_revision(baseline, incoming)
    assert _matched(res) == {0: "p1"}
    assert res.deleted == ["gone"]
    assert res.new == [] and res.abstains == []


# --------------------------------------------------------------------------- #
# Injectivity — no baseline node is ever claimed twice                          #
# --------------------------------------------------------------------------- #


def test_injectivity_no_baseline_claimed_twice() -> None:
    """Two incoming clauses both resemble one baseline clause; only one may win it,
    the other must fall to NEW/abstain — never a double claim."""
    baseline = [_b("p1", 0, body="the buyer shall pay each invoice within thirty days")]
    incoming = [
        _r(0, body="the buyer shall pay each invoice within thirty days"),  # exact -> anchor
        _r(1, body="the buyer shall pay each invoice within thirty days as well"),  # near-dup
    ]
    res = match_revision(baseline, incoming)
    claimed = [m.baseline_id for m in res.matches] + [
        a.best_baseline_id for a in res.abstains if a.best_baseline_id is not None
    ]
    assert len(claimed) == len(set(claimed)), "a baseline node was claimed more than once"
    # exact-text incoming wins the anchor; the near-duplicate cannot also take p1
    assert _matched(res)[0] == "p1"
    assert 1 not in _matched(res)
