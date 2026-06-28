"""Mode B Path-B clause matcher (F03b — counterparty-revision matching).

Ported VERBATIM from the greenlit, Kevin-verified spike
(`spikes/mode_b_matching/matcher.py`, GREENLIGHT 2026-06-25): the algorithm, the
chosen thresholds, the composite weights, and — load-bearing — the duplicate-text
structural disambiguation rule are carried over unchanged from the de-risk run, not
re-derived. See DEV_TODO "SPIKE #3 / F03b build-readiness" for the proof
(auto-match precision 1.000, 0 silent mismatch, changed-region abstention 11.1%,
Layer-A holds on all 3 synthetic variants + self-match).

Lives in `import_/` because Mode B Path B is part of the import spine: the incoming
counterparty draft is parsed (the same `docx_reader -> tree_builder` chain), then
matched against the baseline snapshot before the review flow. The matcher itself is
PURE — it reads neither the DB nor snapshots; F03b's parse path supplies the parsed
incoming tree and the snapshot baseline as plain `ClauseNode` lists.

Lexical + structural only — no embeddings, no LLM. The spike proved the Haiku
identity-scorer (DD-64) was NOT needed to clear the gate; it stays in reserve for a
real-pair residue. The body-similarity metric is **token-set Jaccard**:
`difflib.SequenceMatcher` is FOOLED on real rewrites (its impostor score outranks
true rewrites -> silent mismatch), so it is computed only as a logged secondary,
NEVER as the match decision (SPIKE #3 carry-forward (a)).

Public entry point for F03b:  `match_revision(baseline_nodes, incoming_nodes)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import structlog

from backend.models.revision_match import (
    Abstention,
    ClauseNode,
    LayerAReport,
    MatchedPair,
    RevisionMatchResult,
    SelfMatchReport,
)

log = structlog.get_logger()

# --------------------------------------------------------------------------- #
# GREENLIT thresholds + weights — carried VERBATIM from SPIKE #3.              #
# Calibrated for the token-set Jaccard metric. Do NOT retune without re-running #
# the spike's gate on a real before/after pair (the still-wanted validation).  #
# --------------------------------------------------------------------------- #

TAU_HIGH = 0.40  # auto-match floor: score >= TAU_HIGH AND margin >= DELTA -> match
TAU_LOW = 0.22  # below this -> unmatched (incoming=NEW / baseline=DELETED)
DELTA = 0.05  # runner-up margin: thin margin -> abstain even if score >= TAU_HIGH
# Candidate floor — used for BOTH candidate generation (text-sim gate) and the
# greedy assignment floor. In the spike these were the params `s` and `cand_floor`,
# both 0.30 in the GREENLIT config.
CANDIDATE_FLOOR = 0.30

# Composite-confidence weights (SPIKE #3, unchanged). Text dominates; number is a
# WEAK signal (W_NUM tiny) because counterparty insert/delete renumbers everything
# downstream — number is a generator/tiebreak only, NEVER a decision.
W_TEXT, W_NUM, W_PARENT, W_ORDER = 0.82, 0.06, 0.07, 0.05

# Confidence stamped on a parent rescued by the structural-consistency repair pass
# (`_repair_structural_deletions`). Deliberately BELOW the auto band (TAU_HIGH) so a
# repaired heading-reword surfaces to the operator as a normal low-confidence edit
# rather than masquerading as a high-confidence lexical match. NOT a scoring/threshold
# knob — it never participates in the de-risked match decision; it only labels a pair
# the post-pass moved out of a structurally-impossible deletion.
STRUCTURAL_MATCH_CONFIDENCE = 0.30


# --------------------------------------------------------------------------- #
# Normalisation + similarity                                                   #
# --------------------------------------------------------------------------- #


_PREFIX_RE = re.compile(
    r"^\s*(\(?[0-9]+(\.[0-9]+)*\)?[.)]?|\([a-z]+\)|\([ivxlcdm]+\))\s+", flags=re.I
)


def _norm(s: str | None) -> str:
    """Lowercase, strip a leading enumeration prefix, collapse whitespace.

    Verbatim from the spike: strips "6.", "6.1.2", "(a)", "(iv)" style prefixes so a
    renumbered clause normalises to the same text as its baseline (number is weak).
    """
    s = (s or "").strip()
    s = _PREFIX_RE.sub("", s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _jaccard(a: str, b: str) -> float:
    """Token-set Jaccard — THE match-decision metric (SPIKE #3 carry-forward (a))."""
    ta, tb = set(a.split()), set(b.split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _seqratio(a: str, b: str) -> float:
    """difflib char-diff ratio — logged SECONDARY only; FOOLED on rewrites, never
    used for the decision (SPIKE #3 carry-forward (a))."""
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# Internal prepared node + tree derivation (depth, derived dotted number)       #
# --------------------------------------------------------------------------- #


@dataclass
class _Node:
    """Spike-shaped internal node. `key` is the per-side stable identity (baseline
    id, or incoming order). `parent` is the parent's key on this side."""

    key: str | int
    heading: str
    body: str
    text: str  # canonical content = heading if heading else body
    number: str  # derived dotted number ("6.1.2") — WEAK signal only
    depth: int
    parent: str | int | None
    order: int


def _prepare(nodes: list[ClauseNode], *, baseline: bool) -> list[_Node]:
    """Build internal nodes, deriving depth + dotted number from parent/order.

    Baseline key = node.id; incoming key = node.order (incoming has no id). `parent`
    is already the nearest clause-ancestor's key on this side. depth = count of
    clause ancestors; number = position-based dotted path among clause siblings.
    Both are derived (not inputs) exactly as the spike derived them while flattening.
    """
    keyed: dict[str | int, ClauseNode] = {}
    for n in nodes:
        if baseline:
            if n.id is None:
                raise ValueError("baseline ClauseNode must carry a stable id")
            keyed[n.id] = n
        else:
            keyed[n.order] = n

    # children grouped by parent key, ordered by reading position
    children: dict[str | int | None, list[ClauseNode]] = {}
    for n in nodes:
        children.setdefault(n.parent, []).append(n)
    for sibs in children.values():
        sibs.sort(key=lambda x: x.order)

    depth: dict[str | int, int] = {}
    number: dict[str | int, str] = {}

    def walk(parent_key: str | int | None, prefix: str, d: int) -> None:
        for idx, n in enumerate(children.get(parent_key, []), start=1):
            key = n.id if baseline else n.order
            assert key is not None
            num = f"{prefix}.{idx}" if prefix else str(idx)
            depth[key] = d
            number[key] = num
            walk(key, num, d + 1)

    walk(None, "", 0)

    out: list[_Node] = []
    for n in nodes:
        key = n.id if baseline else n.order
        assert key is not None
        heading = (n.heading or "").strip()
        body = (n.body or "").strip()
        text = heading if heading else body
        out.append(
            _Node(
                key=key,
                heading=heading,
                body=body,
                text=text,
                number=number.get(key, ""),
                depth=depth.get(key, 0),
                parent=n.parent,
                order=n.order,
            )
        )
    out.sort(key=lambda x: x.order)
    return out


# --------------------------------------------------------------------------- #
# Internal matcher result (rich) — mapped to RevisionMatchResult for callers    #
# --------------------------------------------------------------------------- #


@dataclass
class _Pair:
    r_order: int
    b_key: str | int
    score: float
    text_sim: float
    seqratio_secondary: float  # logged-only; never feeds the decision


@dataclass
class _Result:
    # r_order -> ("match" | "abstain" | "new", best_b_key | None, confidence)
    decisions: dict[int, tuple[str, str | int | None, float]] = field(default_factory=dict)
    anchored: set[int] = field(default_factory=set)
    matched_b: dict[str | int, int] = field(default_factory=dict)
    deleted_b: set[str | int] = field(default_factory=set)
    abstain_b: set[str | int] = field(default_factory=set)


def _match_internal(baseline: list[_Node], incoming: list[_Node]) -> _Result:
    """Deterministic anchor pass + fuzzy injective assignment with abstain band.

    Verbatim port of the spike's `match()` with the GREENLIT thresholds + Jaccard.
    """
    res = _Result()

    free_b: dict[str | int, _Node] = {b.key: b for b in baseline}
    bnorm = {b.key: _norm(b.text) for b in baseline}
    rnorm = {r.order: _norm(r.text) for r in incoming}

    n_b = max(1, len(baseline) - 1)
    n_r = max(1, len(incoming) - 1)

    # ---- 1. Anchor pass: byte-identical normalized text -> lock --------------- #
    by_text: dict[str, list[str | int]] = {}
    for b in baseline:
        by_text.setdefault(bnorm[b.key], []).append(b.key)

    for r in incoming:
        t = rnorm[r.order]
        if not t:
            continue
        cands = [k for k in by_text.get(t, []) if k in free_b]
        if not cands:
            continue
        if len(cands) == 1:
            bk = cands[0]
        else:
            # CRITICAL (the catastrophic case the spike caught): duplicate normalized
            # text (e.g. a section header and its identically-titled sub-clause) MUST
            # disambiguate STRUCTURALLY — depth equality first (renumbering shifts
            # order but NOT depth), then parent-already-matched, then order. Order
            # alone crosses them under renumbering (the Variant-A silent swap bug).
            rp = r.order / n_r

            def struct_key(k: str | int, r: _Node = r, rp: float = rp) -> tuple[int, int, float]:
                b = free_b[k]
                depth_diff = abs(b.depth - r.depth)
                par_ok = 0
                if r.parent is not None:
                    pdec = res.decisions.get(int(r.parent)) if isinstance(r.parent, int) else None
                    if pdec and pdec[0] == "match" and pdec[1] == b.parent:
                        par_ok = -1  # reward an already-matched parent
                return (depth_diff, par_ok, abs(b.order / n_b - rp))

            bk = min(cands, key=struct_key)
        res.decisions[r.order] = ("match", bk, 1.0)
        res.anchored.add(r.order)
        res.matched_b[bk] = r.order
        del free_b[bk]
        by_text[t] = [k for k in by_text[t] if k != bk]

    # ---- 2. Candidate generation on the residue ------------------------------ #
    residue_r = [r for r in incoming if r.order not in res.decisions]
    residue_b = list(free_b.values())

    def parent_matched_signal(r: _Node, b: _Node) -> float:
        # r.parent is the parent clause's ORDER (incoming side); if that parent
        # matched a baseline node, reward candidates whose parent is that baseline.
        if r.parent is None or not isinstance(r.parent, int):
            return 0.0
        dec = res.decisions.get(r.parent)
        if not dec or dec[0] != "match":
            return 0.0
        return 1.0 if dec[1] == b.parent else 0.0

    pairs: list[_Pair] = []
    for r in residue_r:
        tr = rnorm[r.order]
        rp = r.order / n_r
        for b in residue_b:
            tb = bnorm[b.key]
            ts = _jaccard(tr, tb)
            num = 1.0 if (r.number and r.number == b.number) else 0.0
            # candidate gate: text-sim >= floor OR same derived number (weak)
            if not (ts >= CANDIDATE_FLOOR or num >= 1.0):
                continue
            pm = parent_matched_signal(r, b)
            op = 1.0 - abs(rp - b.order / n_b)
            score = W_TEXT * ts + W_NUM * num + W_PARENT * pm + W_ORDER * op
            pairs.append(_Pair(r.order, b.key, score, ts, _seqratio(tr, tb)))

    # ---- 3. Greedy best-first injective assignment --------------------------- #
    by_r: dict[int, list[_Pair]] = {}
    for p in pairs:
        by_r.setdefault(p.r_order, []).append(p)
    for v in by_r.values():
        v.sort(key=lambda p: p.score, reverse=True)

    taken_b: set[str | int] = set()
    order_pairs = sorted(pairs, key=lambda p: p.score, reverse=True)
    for p in order_pairs:
        if p.r_order in res.decisions or p.b_key in taken_b:
            continue
        if p.score < CANDIDATE_FLOOR:
            continue
        ranked = [q for q in by_r[p.r_order] if q.b_key not in taken_b]
        if not ranked or ranked[0].b_key != p.b_key:
            continue
        runner = ranked[1].score if len(ranked) > 1 else 0.0
        margin = p.score - runner

        # ---- 4. Accept / reject / ABSTAIN bands ------------------------------ #
        if p.score >= TAU_HIGH and margin >= DELTA:
            res.decisions[p.r_order] = ("match", p.b_key, p.score)
            res.matched_b[p.b_key] = p.r_order
            taken_b.add(p.b_key)
        elif p.score < TAU_LOW:
            continue  # leave incoming for NEW, baseline stays free
        else:
            # abstain band (or thin margin): surface to operator, lock nothing
            res.decisions[p.r_order] = ("abstain", p.b_key, p.score)
            res.abstain_b.add(p.b_key)
            taken_b.add(p.b_key)  # provisionally reserve so two abstains can't share

    # ---- finalize buckets ---------------------------------------------------- #
    for r in incoming:
        if r.order not in res.decisions:
            res.decisions[r.order] = ("new", None, 0.0)

    matched_or_anchor = set(res.matched_b.keys())
    for b in baseline:
        if b.key not in matched_or_anchor and b.key not in res.abstain_b:
            res.deleted_b.add(b.key)

    return res


# --------------------------------------------------------------------------- #
# Structural-consistency repair pass (post-scoring; does NOT touch the de-risked #
# scoring/thresholds — operates only on the finished RevisionMatchResult).       #
# --------------------------------------------------------------------------- #


def _revised_parent_candidates(
    parent: str,
    *,
    children_of: dict[str, list[str]],
    surviving: set[str],
    baseline_to_incoming: dict[str, int],
    incoming_by_order: dict[int, ClauseNode],
) -> tuple[list[str], list[str], set[int | str | None], set[int]]:
    """The revised-parent slot(s) implied by a baseline parent's surviving children.

    Shared by both repair branches: from `parent`'s MATCHED baseline children, look up
    their matched incoming nodes and collect the common incoming-side `parent` (the
    revised parent's structural slot). Returns (surviving_kids, matched_kids,
    candidate_parents, real_parents) where `real_parents` is the subset of candidate
    parents that are concrete incoming nodes (int order present on the incoming side).
    """
    surviving_kids = [c for c in children_of.get(parent, []) if c in surviving]
    matched_kids = [c for c in surviving_kids if c in baseline_to_incoming]
    candidate_parents: set[int | str | None] = set()
    for c in matched_kids:
        inc = incoming_by_order.get(baseline_to_incoming[c])
        if inc is not None:
            candidate_parents.add(inc.parent)
    real_parents = {
        cp for cp in candidate_parents if isinstance(cp, int) and cp in incoming_by_order
    }
    return surviving_kids, matched_kids, candidate_parents, real_parents


def _repair_structural_deletions(
    result: RevisionMatchResult,
    baseline_nodes: list[ClauseNode],
    incoming_nodes: list[ClauseNode],
) -> RevisionMatchResult:
    """Enforce a structural invariant the lexical scorer cannot see.

    INVARIANT: a baseline parent that still has >=1 baseline CHILD which SURVIVES
    (matched or abstained) is NOT really deleted, and — when the children confirm the
    pairing — it is NOT a low-confidence guess either. The lexical scorer cannot see
    that a section's children all matched confidently, so a reworded section HEADING
    lands either in `deleted` (heading fell below the candidate floor; new heading ->
    `new`) or in `abstains` (heading scored in the [TAU_LOW, TAU_HIGH) band against its
    own baseline parent). Both are heading-reword EDITs of a surviving parent and must
    never reach the operator as a phantom deletion or a "maps to nothing" abstain.

    Runs AFTER the de-risked scoring (`_match_internal`) and re-classifies ONLY the
    offending baseline parents; every other result is preserved byte-for-byte. Pure
    and deterministic, O(n) over the node sets (child/parent index maps built once).
    Does NOT touch any scoring threshold.

    Two branches, both keyed on the SAME surviving-children signal:

    (A) ABSTAIN parent -> confident match. An abstention whose `best_baseline_id` is a
        baseline parent with surviving MATCHED children, where those children's common
        incoming parent is exactly this abstention's `incoming_index` (one clean real
        parent that IS the abstain's own slot), is CONFIRMED: the children prove the
        pairing the scorer was unsure of. PROMOTE it out of `abstains` into `matches`
        at structural confidence. We only confirm an existing pairing — never invent.

    (B) DELETED parent. Per offending deleted parent P:
      1. From P's MATCHED children take the common incoming-side `parent` R.
      2. Clean R (exactly one real incoming parent, still unclaimed in `new`): MOVE P
         from `deleted` into `matches` as MatchedPair(R, P, structural confidence) and
         REMOVE R from `new` — the spurious delete+new becomes one heading-reword edit.
      3. No clean R (children's incoming parents disagree, R is None/top-level, R is
         already claimed, or only abstained children survive): DEMOTE P to an ABSTAIN
         (operator-confirm), anchored to a safe surviving incoming node — never leave a
         structurally-invalid deletion. Logged for observability.
    """
    # --- index maps (built once; O(n)) ------------------------------------- #
    incoming_by_order: dict[int, ClauseNode] = {n.order: n for n in incoming_nodes}
    children_of: dict[str, list[str]] = {}
    for n in baseline_nodes:
        if n.parent is not None and n.id is not None:
            children_of.setdefault(str(n.parent), []).append(n.id)

    baseline_to_incoming: dict[str, int] = {m.baseline_id: m.incoming_index for m in result.matches}
    abstain_incoming_by_baseline: dict[str, int] = {
        a.best_baseline_id: a.incoming_index
        for a in result.abstains
        if a.best_baseline_id is not None
    }
    surviving: set[str] = set(baseline_to_incoming) | set(abstain_incoming_by_baseline)

    # mutable working buckets
    out_matches: list[MatchedPair] = list(result.matches)
    out_abstains: list[Abstention] = list(result.abstains)
    out_new: set[int] = set(result.new)
    out_deleted: set[str] = set(result.deleted)

    changed = False

    # --- (A) promote abstain parents whose children confirm the pairing ----- #
    promoted_abstain_baselines: set[str] = set()
    for ab in result.abstains:
        bid = ab.best_baseline_id
        if bid is None or bid not in children_of:
            continue  # not a parent heading -> ordinary low-confidence pair, leave it
        _kids, matched_kids, candidate_parents, real_parents = _revised_parent_candidates(
            bid,
            children_of=children_of,
            surviving=surviving,
            baseline_to_incoming=baseline_to_incoming,
            incoming_by_order=incoming_by_order,
        )
        if not matched_kids:
            continue  # no confident child to confirm the slot -> leave for operator
        if (
            len(candidate_parents) == 1
            and len(real_parents) == 1
            and next(iter(real_parents)) == ab.incoming_index
        ):
            promoted_abstain_baselines.add(bid)
            baseline_to_incoming[bid] = ab.incoming_index  # keep maps coherent for (B)
            out_matches.append(
                MatchedPair(
                    incoming_index=ab.incoming_index,
                    baseline_id=bid,
                    confidence=STRUCTURAL_MATCH_CONFIDENCE,
                )
            )
            changed = True
            log.info(
                "revision_match.structural_repair.abstain_heading_reword",
                baseline_parent=bid,
                revised_parent_index=ab.incoming_index,
                surviving_children=_kids,
            )
    if promoted_abstain_baselines:
        out_abstains = [
            a for a in out_abstains if a.best_baseline_id not in promoted_abstain_baselines
        ]

    # --- (B) rescue structurally-impossible deleted parents ----------------- #
    for p in sorted(out_deleted):  # deterministic order
        surviving_kids, matched_kids, candidate_parents, real_parents = _revised_parent_candidates(
            p,
            children_of=children_of,
            surviving=surviving,
            baseline_to_incoming=baseline_to_incoming,
            incoming_by_order=incoming_by_order,
        )
        if not surviving_kids:
            continue  # genuine deletion — leave untouched

        clean_r: int | None = None
        if len(candidate_parents) == 1 and len(real_parents) == 1:
            r = next(iter(real_parents))
            if r in out_new:  # still unclaimed -> the reworded parent's slot
                clean_r = r

        if clean_r is not None:
            out_deleted.discard(p)
            out_new.discard(clean_r)
            out_matches.append(
                MatchedPair(
                    incoming_index=clean_r,
                    baseline_id=p,
                    confidence=STRUCTURAL_MATCH_CONFIDENCE,
                )
            )
            # keep maps coherent so a later P can't re-claim the same incoming node
            baseline_to_incoming[p] = clean_r
            changed = True
            log.info(
                "revision_match.structural_repair.heading_reword",
                baseline_parent=p,
                revised_parent_index=clean_r,
                surviving_children=surviving_kids,
            )
        else:
            # ambiguous -> abstain; anchor on a safe incoming node NOT in `new`
            safe_real = sorted(cp for cp in real_parents if cp not in out_new)
            if safe_real:
                anchor = safe_real[0]
            else:
                child_anchors = [baseline_to_incoming[c] for c in matched_kids]
                child_anchors += [
                    abstain_incoming_by_baseline[c]
                    for c in surviving_kids
                    if c in abstain_incoming_by_baseline
                ]
                anchor = min(child_anchors)
            out_deleted.discard(p)
            out_abstains.append(
                Abstention(
                    incoming_index=anchor,
                    best_baseline_id=p,
                    confidence=STRUCTURAL_MATCH_CONFIDENCE,
                )
            )
            changed = True
            log.info(
                "revision_match.structural_repair.ambiguous_demote",
                baseline_parent=p,
                anchor_incoming_index=anchor,
                candidate_parents=sorted(str(cp) for cp in candidate_parents),
                surviving_children=surviving_kids,
            )

    if not changed:
        return result
    return RevisionMatchResult(
        matches=out_matches,
        new=sorted(out_new),
        deleted=sorted(out_deleted),
        abstains=out_abstains,
    )


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #


def match_revision(
    baseline_nodes: list[ClauseNode], incoming_nodes: list[ClauseNode]
) -> RevisionMatchResult:
    """Match an incoming counterparty revision against the baseline clause tree.

    Returns the injective partial map as four buckets:
      - `matches`  : auto-accepted incoming -> baseline pairs (+ confidence),
      - `new`      : incoming indices with no baseline counterpart,
      - `deleted`  : baseline ids absent from the incoming draft,
      - `abstains` : low-confidence / thin-margin pairs for operator-confirm.

    Pure: reads neither the DB nor snapshots. F03b supplies the parsed incoming tree
    and the `last_shared_with_counterparty` snapshot baseline as `ClauseNode` lists.
    Indices in `matches`/`new`/`abstains` are incoming `order`; ids in `deleted` and
    `baseline_id` are baseline `ClauseNode.id`.
    """
    baseline = _prepare(baseline_nodes, baseline=True)
    incoming = _prepare(incoming_nodes, baseline=False)
    res = _match_internal(baseline, incoming)

    matches: list[MatchedPair] = []
    new: list[int] = []
    abstains: list[Abstention] = []
    for r_order, (kind, b_key, conf) in sorted(res.decisions.items()):
        if kind == "match":
            assert b_key is not None
            matches.append(
                MatchedPair(incoming_index=r_order, baseline_id=str(b_key), confidence=conf)
            )
        elif kind == "abstain":
            abstains.append(
                Abstention(
                    incoming_index=r_order,
                    best_baseline_id=None if b_key is None else str(b_key),
                    confidence=conf,
                )
            )
        else:
            new.append(r_order)

    deleted = sorted(str(k) for k in res.deleted_b)
    result = RevisionMatchResult(matches=matches, new=new, deleted=deleted, abstains=abstains)
    # Post-pass: repair structurally-impossible deleted-parent-with-surviving-child
    # cases (heading rewords the lexical scorer can't see). Does NOT touch scoring.
    return _repair_structural_deletions(result, baseline_nodes, incoming_nodes)


# --------------------------------------------------------------------------- #
# Layer-A mechanical oracle (no ground truth; must hold unconditionally)        #
# --------------------------------------------------------------------------- #


def layer_a_invariants(
    baseline_nodes: list[ClauseNode],
    incoming_nodes: list[ClauseNode],
    result: RevisionMatchResult,
) -> LayerAReport:
    """Mechanical invariants on the RESOLVED map (abstains -> best candidate).

    No ground truth needed — these must hold for ANY input. F03b can call this as a
    runtime safety net on the abstain->operator-confirm path. Mirrors the spike's
    `layer_a` (SPIKE #3 Layer A): injectivity, partition completeness on both sides,
    and the reconstruction round-trip (every incoming reconstructs; every baseline
    is matched or deleted).
    """
    baseline_keys = {n.id for n in baseline_nodes if n.id is not None}
    incoming_orders = {n.order for n in incoming_nodes}

    # resolved: matches as-is, abstains resolved to their best candidate
    claimed: list[str] = [m.baseline_id for m in result.matches]
    resolved_matched_incoming: set[int] = {m.incoming_index for m in result.matches}
    for ab in result.abstains:
        if ab.best_baseline_id is not None:
            claimed.append(ab.best_baseline_id)
            resolved_matched_incoming.add(ab.incoming_index)

    # (1) injectivity: no baseline claimed twice
    injectivity = len(claimed) == len(set(claimed))

    # (2) partition completeness (resolved): |incoming| = matched + new,
    #     |baseline| = matched + deleted
    matched_b_keys = set(claimed)
    deleted_b = set(result.deleted)
    new_incoming = {i for i in result.new if i in incoming_orders}
    partition_incoming = (len(resolved_matched_incoming) + len(new_incoming)) == len(
        incoming_orders
    )
    partition_baseline = (len(matched_b_keys) + len(deleted_b)) == len(baseline_keys)

    # (3) reconstruction round-trip: every incoming node is accounted for (matched
    #     or new -> incoming text wins) AND every baseline is matched or deleted.
    accounted_incoming = resolved_matched_incoming | new_incoming
    bucket_ok = (matched_b_keys | deleted_b) == baseline_keys
    roundtrip = accounted_incoming == incoming_orders and bucket_ok

    return LayerAReport(
        injectivity=injectivity,
        partition_incoming=partition_incoming,
        partition_baseline=partition_baseline,
        roundtrip=roundtrip,
    )


def self_match_noop(baseline_nodes: list[ClauseNode]) -> SelfMatchReport:
    """Self-match: match a clause set against itself -> zero NEW/DELETED/ABSTAIN.

    Catches threshold + normalisation bugs (SPIKE #3 Layer A (4)). The incoming side
    is the baseline re-keyed by order (ids stripped, as a real parse would produce).
    """
    by_id_to_order: dict[str | None, int] = {n.id: n.order for n in baseline_nodes}
    incoming = [
        ClauseNode(
            id=None,
            # remap baseline parent_id -> the parent's order (incoming parent semantics)
            parent=None if n.parent is None else by_id_to_order.get(str(n.parent)),
            order=n.order,
            heading=n.heading,
            body=n.body,
            role=n.role,
        )
        for n in baseline_nodes
    ]
    res = match_revision(baseline_nodes, incoming)
    return SelfMatchReport(new=len(res.new), deleted=len(res.deleted), abstain=len(res.abstains))
