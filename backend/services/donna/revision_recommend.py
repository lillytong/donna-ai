"""Donna's per-change revision recommendation engine (F03c — the counterparty revision
reviewer; DD-78). For every not-yet-decided change in a Mode B review session, Donna analyzes
each hunk and writes an ADVISORY verdict (accept | counter | keep), a significance
(trivial | substantive), and — when she pushes back — exact counter-language onto the
`counterparty_revision_hunks` row's `donna_verdict` / `donna_counter_text` / `significance`
columns. Mirrors the F11 recommendation pipeline (single linear shot, no LangGraph — DD-52).

  1. Load the session + contract context (deal type), the live nodes, and (cheaply) the
     tier-8 negotiation patterns. Select the changes that are NOT yet decided
     (status != complete) and are content-review kinds (edited / new / deleted); skip
     unresolved abstains (no settled baseline to ground against).
  2. Per hunk, build grounding by REUSING the F11 helpers (grounding.build_clause_grounding /
     build_label_map / build_pattern_grounding): the baseline clause subtree (edited/deleted →
     the matched node; new → the proposed parent's neighbors) plus the specific edit
     (original_text → proposed_text). Render `revision_recommend_v2.txt` and call Claude at the
     CAPABLE tier (high/Opus — counter-language is high-consequence, DD-35), structured JSON.
  3. Finalize each output: scrub any leaked id from the prose, and enforce the schema invariant
     that counter-language exists iff verdict == counter and a trivial hunk never carries it.
  4. Persist ONLY the advisory columns (donna_verdict / donna_counter_text / significance) in
     one transaction. NEVER touches `verdict` / `final_text` — per DD-64 the tracked redline is
     always the deterministic diff; Donna's counter-language is a separate suggestion the
     operator adopts via "Use Donna's", never the silently-applied text.

Idempotent: a decided change (status = complete) is skipped; re-running re-generates the
advisory columns for changes still pending/partial.

PROMPT-INJECTION NOTE: the counterparty's edit text is adversarial input flowing into the
prompt. `revision_recommend_v2.txt` frames all document text as DATA, not instructions. This
remains a residual risk area (open product item on injection stance).

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

import structlog
from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.imports import StoredNode
from backend.models.revision_recommend import (
    RevisionRecommendation,
    RevisionRecommendSummary,
    VerdictTally,
)
from backend.prompts.utils import render
from backend.services import deal_brief_repo
from backend.services.contract_repo import fetch_nodes
from backend.services.cross_references import list_cross_references
from backend.services.defined_terms import list_terms_for_deal
from backend.services.donna.distillation import fetch_patterns_for_issue
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_deal_brief_grounding,
    build_mandate_grounding,
    build_pattern_grounding,
    build_projected_label_map,
    build_reference_grounding,
)
from backend.services.donna.qa import scrub_leaked_ids
from backend.services.firm_profile_repo import get_firm_profile
from backend.services.import_.revision_cluster import cluster_key, normalize_segment
from backend.services.import_.revision_review import projected_clause_numbers
from backend.services.llm import complete
from backend.services.settings_repo import get_contract, get_contract_type

log = structlog.get_logger()

ChangeKind = Literal["edited", "new", "deleted", "abstain"]

# A high-consequence surface: an unparseable model output becomes an honest, conservative
# "keep" (the safe hold — never auto-accept an unreadable change), flagged substantive so the
# operator gives it a full manual read. Never fabricated, never surfaced raw (§2.4).
_FALLBACK = RevisionRecommendation(
    verdict="keep",
    significance="substantive",
    reasoning="I couldn't produce a grounded read of this change — review it manually.",
    counter_language=None,
)


class SessionNotFound(Exception):
    """Revision session missing."""


_SELECT_SESSION = """
SELECT id, contract_id, source, status
FROM counterparty_revision_sessions
WHERE id = $1
"""

# Only the columns the engine grounds on / decides over; status drives the idempotency skip.
_SELECT_CHANGES = """
SELECT id, node_id, proposed_parent_id, proposed_order_index, match_confidence, status
FROM counterparty_revision_changes
WHERE session_id = $1
"""

_SELECT_HUNKS = """
SELECT id, change_id, hunk_type, significance, position_in_body, original_text, proposed_text
FROM counterparty_revision_hunks
WHERE change_id = ANY($1::uuid[])
ORDER BY change_id, position_in_body NULLS FIRST, id
"""

# Writes ONLY the advisory columns (+ Donna's significance call). NEVER verdict / final_text
# (DD-64: the applied text is the deterministic diff, never Donna's suggestion). The rationale
# is Donna's one-line reason for the verdict (her `reasoning` line) — her reasoning, never
# invented clause text.
_UPDATE_HUNK_ADVISORY = """
UPDATE counterparty_revision_hunks
SET donna_verdict = $2, donna_counter_text = $3, significance = $4, donna_rationale = $5
WHERE id = $1
"""


# --- pure helpers (no I/O; unit-testable) ------------------------------------


def derive_kind(node_id: Any, match_confidence: Any, proposed_order_index: Any) -> ChangeKind:
    """The change kind, derived from the staged columns (F03b wrote no kind column; same rule
    as models/revision_review.py): edited = matched node + confidence; deleted = matched node,
    no confidence; new = no node but a sibling position; abstain = neither (unconfirmed match)."""
    if node_id is not None:
        return "edited" if match_confidence is not None else "deleted"
    return "new" if proposed_order_index is not None else "abstain"


def build_change_focus(
    kind: ChangeKind, hunk_type: str, original_text: str | None, proposed_text: str | None
) -> str:
    """The specific edit under review, as a labelled block (DATA, not instructions). For a
    new/deleted whole-node change the single hunk carries the added/removed body; for an edited
    change the hunk is one inline insertion/deletion/replacement within the clause."""
    original = original_text if original_text else "(none)"
    proposed = proposed_text if proposed_text else "(none)"
    if kind == "new":
        return f"Change type: a NEW clause/text the counterparty ADDED.\nAdded text:\n{proposed}"
    if kind == "deleted":
        return (
            "Change type: existing language the counterparty DELETED in full.\n"
            f"Removed text:\n{original}"
        )
    return (
        f"Change type: an edit to our existing language ({hunk_type}).\n"
        f"Our original text:\n{original}\n"
        f"Their proposed text:\n{proposed}"
    )


def parse_recommendation(text: str) -> RevisionRecommendation:
    """Tolerate a non-strict JSON recommendation; an unparseable one becomes the honest
    conservative fallback (mirrors recommendations.parse_draft)."""
    try:
        return RevisionRecommendation.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return RevisionRecommendation.model_validate_json(text[start : end + 1])
            except ValidationError:
                return _FALLBACK
        return _FALLBACK


# --- F35 / DD-92: inline clause-citation anchors ------------------------------
#
# THE ANCHOR CONVENTION (frontend consumes this verbatim): Donna emits an inline
# `[[clause:NODE_ID]]` token in her `reasoning` to reference a clause. The frontend renders each
# token as a clickable inline link labeled with the clause's LIVE projected number (DD-88) that
# scrolls to the clause (reusing the F10/F11 node-id citation + jumpTo pattern). The token is a
# DOUBLE-bracket sentinel chosen so it cannot collide with the single-bracket `[id]` grounding
# convention, with markdown links `[text](url)`, or with ordinary rationale prose, and is
# trivially regex-parseable. NODE_ID is copied verbatim from a bracketed `[id]` in the grounding.
_CLAUSE_ANCHOR_RE = re.compile(r"\[\[clause:([^\]]+?)\]\]")

# Bracketed-id token in a grounding block (`[id] <label> — <text>` / definition / cross-ref
# lines). Single-bracket only — `[^\[\]]` never spans into the `[[clause:...]]` double-bracket.
_GROUNDING_ID_RE = re.compile(r"\[([^\[\]]+)\]")


def extract_clause_anchors(text: str) -> list[str]:
    """Every node_id referenced by an inline `[[clause:NODE_ID]]` anchor, in first-seen order,
    deduped. Pure; the structured read of the anchor convention (never a re-parse downstream)."""
    out: list[str] = []
    for match in _CLAUSE_ANCHOR_RE.finditer(text):
        nid = match.group(1).strip()
        if nid and nid not in out:
            out.append(nid)
    return out


def referenceable_ids(grounding: str, valid: set[str]) -> set[str]:
    """The node_ids a recommendation may anchor to: the bracketed `[id]`s present in this change's
    assembled grounding (focal clause subtree + DD-31 reference bundle), intersected with the real
    node-id set so stray brackets in clause body text can never widen the referenceable set."""
    return {nid for nid in _GROUNDING_ID_RE.findall(grounding)} & valid


def _resolve_anchors(
    text: str, referenceable: set[str], id_labels: dict[str, str]
) -> tuple[str, list[str]]:
    """Validate inline `[[clause:NODE_ID]]` anchors in `text`, returning (text, citations). A VALID
    anchor (id in `referenceable`) is kept VERBATIM so the frontend renders it as a live-numbered
    clickable link, and its id is collected into `citations` (deduped, order-preserved). An anchor
    whose id is unknown/hallucinated or not referenceable degrades to the node's legible label (no
    link) when the id is known, else to a neutral phrase — never a raw id, never a broken link.
    Valid anchors are masked while `scrub_leaked_ids` runs so the bare-id scrub can never rewrite
    the id INSIDE a kept anchor; bare leaked ids elsewhere in the prose are still scrubbed."""
    citations: list[str] = []
    masked: dict[str, str] = {}

    def _repl(match: re.Match[str]) -> str:
        nid = match.group(1).strip()
        if nid in referenceable:
            if nid not in citations:
                citations.append(nid)
            token = f"\x00ANCHOR{len(masked)}\x00"
            masked[token] = f"[[clause:{nid}]]"
            return token
        return id_labels.get(nid, "the referenced clause")

    out = _CLAUSE_ANCHOR_RE.sub(_repl, text)
    out = scrub_leaked_ids(out, id_labels)
    for token, anchor in masked.items():
        out = out.replace(token, anchor)
    return out, citations


def _cluster_key(hunk: Any) -> tuple[str, str] | None:
    """Cross-document clustering key for an asyncpg hunk row (DD-89) — a thin wrapper over the
    SHARED `revision_cluster.cluster_key` so recommend-time and read-time clustering can never
    drift. Returns None for a trivial / whole-node / degenerate hunk (→ singleton bucket)."""
    return cluster_key(hunk["significance"], hunk["original_text"], hunk["proposed_text"])


def _word_spans(text: str) -> list[tuple[str, int, int]]:
    """(word, start_offset, end_offset) for each whitespace-delimited token, so a token-level
    diff can slice the exact source substring back out."""
    spans: list[tuple[str, int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start = i
        while i < n and not text[i].isspace():
            i += 1
        spans.append((text[start:i], start, i))
    return spans


def reconstruct_proposed_clause(
    baseline_body: str, hunks: list[tuple[int, str | None, str | None]]
) -> str:
    """Recover the counterparty's full PROPOSED clause body by replaying every inline hunk
    (position_in_body, original_text, proposed_text) over the baseline node body — the same
    splice the adopt path produces for "accept all". Offsets are BASELINE char offsets, so the
    hunks are applied in descending position to keep earlier offsets valid (mirrors
    revision_review._apply)."""
    out = baseline_body
    for pos, original, proposed in sorted(hunks, key=lambda h: h[0], reverse=True):
        repl = proposed or ""
        if original is not None:
            out = out[:pos] + repl + out[pos + len(original) :]
        else:
            out = out[:pos] + repl + out[pos:]
    return out


def reduce_counter_span(counter: str, proposed_clause: str, proposed_text: str) -> str | None:
    """Reduce an LLM counter that echoed surrounding clause text down to ONLY its changed token
    span, by stripping the prefix/suffix it shares with the counterparty's PROPOSED clause body.

    Word-level `difflib.SequenceMatcher` (proposed clause vs counter) must find EXACTLY ONE
    non-equal region, and that region's proposed side must equal this hunk's `proposed_text` —
    i.e. the counter cleanly changes only the span this hunk owns. The counter side of that
    region is the reduced counter (e.g. "7.5%"). Returns None when the reduction is ambiguous
    (zero or multiple changed regions), does not align to this hunk, or is degenerate (empty
    result) — the caller then keeps the raw counter unreduced rather than guess."""
    prop = _word_spans(proposed_clause)
    cnt = _word_spans(counter)
    matcher = SequenceMatcher(None, [t[0] for t in prop], [t[0] for t in cnt], autojunk=False)
    diffs = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if len(diffs) != 1:
        return None
    _tag, i1, i2, j1, j2 = diffs[0]
    proposed_region = proposed_clause[prop[i1][1] : prop[i2 - 1][2]] if i1 < i2 else ""
    counter_region = counter[cnt[j1][1] : cnt[j2 - 1][2]] if j1 < j2 else ""
    if normalize_segment(proposed_region) != normalize_segment(proposed_text):
        return None
    return counter_region.strip() or None


def finalize_recommendation(
    rec: RevisionRecommendation,
    id_labels: dict[str, str],
    original_text: str | None,
    *,
    referenceable: set[str] | None = None,
    proposed_text: str | None = None,
    proposed_clause: str | None = None,
    model: str | None = None,
    hunk_id: str | None = None,
) -> RevisionRecommendation:
    """Pure post-LLM cleanup + invariant enforcement: resolve the F35/DD-92 inline clause anchors
    (keep valid `[[clause:id]]` anchors verbatim, degrade invalid ones, collect the validated ids
    into `citations`), scrub any leaked bare id out of the prose, and guarantee counter-language
    exists IFF verdict == counter — a trivial hunk never carries it (schema: donna_counter_text is
    null for trivial hunks), and a `counter` with no usable language collapses to the safe `keep`
    (the operator's later "counter" action would otherwise have no staged text to send).

    `referenceable` is the node-id set this change may anchor to (focal clause + DD-31 cross-refs);
    an anchor outside it is treated as not-referenceable. Defaults to none → every anchor degrades
    (the back-compat path for callers without grounding-id context)."""
    reasoning, citations = _resolve_anchors(rec.reasoning, referenceable or set(), id_labels)
    counter = rec.counter_language
    if counter is not None:
        counter = scrub_leaked_ids(counter, id_labels).strip() or None

    verdict = rec.verdict
    if rec.significance == "trivial":
        counter = None

    # DETERMINISTIC SPAN REDUCTION (inline edits only — `original_text` non-empty; a whole-node
    # new/deleted hunk's whole-clause counter is correct and left untouched). The LLM sometimes
    # echoes the whole surrounding sentence into counter_language; on adopt the redline splices
    # that sentence over only the changed span (DD-64) and duplicates text. Reduce the counter
    # to just its changed token span; if the reduction is ambiguous, keep the raw counter and
    # warn — never guess (a wrong splice is worse than an over-long one).
    if (
        verdict == "counter"
        and counter is not None
        and original_text
        and proposed_text
        and proposed_clause
    ):
        reduced = reduce_counter_span(counter, proposed_clause, proposed_text)
        if reduced is None:
            log.warning(
                "revision_recommend.counter_reduction_skipped", model=model, hunk_id=hunk_id
            )
        else:
            counter = reduced

    # A counter whose language merely restores our original span IS a reject: collapse it to
    # `keep` so it applies as a no-op rather than splicing the echoed/whole-clause span as an
    # addition (the redline replaces only the changed span — DD-64).
    if (
        verdict == "counter"
        and counter is not None
        and original_text is not None
        and normalize_segment(counter) == normalize_segment(original_text)
    ):
        verdict = "keep"
        counter = None
    if verdict == "counter":
        if counter is None:
            verdict = "keep"
    else:
        counter = None

    return RevisionRecommendation(
        verdict=verdict,
        significance=rec.significance,
        reasoning=reasoning,
        counter_language=counter,
        citations=citations,
    )


# --- orchestration -----------------------------------------------------------


@dataclass(frozen=True)
class _Member:
    """One target hunk plus the per-change grounding `finalize_recommendation` needs. A cluster's
    members share one judge call (on a representative) but each finalizes against its OWN span
    (DD-89: counter span-reduction is per-hunk — a reduced counter must never be fanned)."""

    hunk: Any
    kind: ChangeKind
    clause: str
    proposed_clause: str
    referenceable: frozenset[str]


async def _judge(
    *,
    kind: ChangeKind,
    hunk: Any,
    deal_context: str,
    clause: str,
    mandate_block: str,
    pattern_block: str,
    max_tokens: int,
    temperature: float,
) -> RevisionRecommendation:
    """One LLM call on a representative hunk → a RAW recommendation (verdict, significance,
    un-reduced counter_language); parse only, NO finalize. Run ONCE per cluster; the caller then
    runs `finalize_recommendation` per member against that member's own span (DD-89). The firm-
    profile mandate (F32/DD-90) and the learned-pattern block are appended AFTER the rendered
    prompt (not template slots) so both stay non-authoritative and the prompt template/eval stay
    untouched (mirrors F11/F36, DD-76). The mandate (operator standing context) precedes the
    patterns (soft heuristics)."""
    prompt = render(
        "revision_recommend_v2.txt",
        deal_context=deal_context,
        clause=clause or "(no baseline clause resolved)",
        change=build_change_focus(
            kind, hunk["hunk_type"], hunk["original_text"], hunk["proposed_text"]
        ),
    )
    if mandate_block:
        prompt = f"{prompt}\n\n{mandate_block}"
    if pattern_block:
        prompt = f"{prompt}\n\n{pattern_block}"

    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="revision_recommend",
        max_tokens=max_tokens,
        temperature=temperature,
        json_response=True,
    )
    return parse_recommendation(result.text)


def _grounding_root(kind: ChangeKind, change: Any) -> str | None:
    """The node whose subtree grounds this change: the matched node for edited/deleted; the
    proposed parent (for its neighbouring clauses) for a new node."""
    if kind in ("edited", "deleted"):
        node_id = change["node_id"]
        return str(node_id) if node_id is not None else None
    parent = change["proposed_parent_id"]
    return str(parent) if parent is not None else None


def _proposed_clause_for(
    kind: ChangeKind, change: Any, hunks: list[Any], nodes_by_id: dict[str, StoredNode]
) -> str:
    """The counterparty's proposed clause body for span-reduction grounding: the matched node's
    baseline body with this change's inline hunks replayed (reconstruct_proposed_clause). Only
    edited changes have a baseline-clause-plus-inline-edits shape; for new/deleted (no inline
    span to reduce against) return empty, which disables reduction for that change."""
    if kind != "edited":
        return ""
    node = nodes_by_id.get(str(change["node_id"])) if change["node_id"] is not None else None
    if node is None:
        return ""
    return reconstruct_proposed_clause(
        node.body or "",
        [(h["position_in_body"] or 0, h["original_text"], h["proposed_text"]) for h in hunks],
    )


async def recommend_session(session_id: str) -> RevisionRecommendSummary:
    """Analyze every not-yet-decided change in the session and write Donna's advisory verdict /
    significance / counter-language onto each hunk. Idempotent (skips decided changes)."""
    async with acquire() as conn:
        session = await conn.fetchrow(_SELECT_SESSION, session_id)
        if session is None:
            raise SessionNotFound(session_id)
        contract_id = str(session["contract_id"])

        contract = await get_contract(conn, contract_id)
        ctype = (
            await get_contract_type(conn, contract.contract_type_id)
            if contract is not None
            else None
        )
        nodes = await fetch_nodes(conn, contract_id)
        # Tier-8 retrieval (DD-76), cheap (one query): operator-style always, counterparty when
        # same client, deal-type when same contract type. Background heuristics, never cited.
        patterns = await fetch_patterns_for_issue(conn, contract_id)
        # F36 / DD-93 reference-graph grounding inputs: the deal's defined-term registry (F16) +
        # this contract's cross-references (F17). Two cheap reads, once per session.
        deal_id = contract.deal_id if contract is not None else None
        defined_terms = await list_terms_for_deal(conn, deal_id) if deal_id is not None else []
        cross_refs = await list_cross_references(conn, contract_id)
        # F32 v1 / DD-90: the global operator-authored firm profile — the firm's standing MANDATE
        # (who we are, our interests, our red-lines). One read per session; injected as a session-
        # level constant grounding block, identical for every change. Empty profile -> no-op.
        firm_profile = await get_firm_profile(conn)
        # F37 / DD-95: the per-deal deal brief — Donna's whole-deal model (parties, economic
        # spine, purpose), distilled once at import. One read per session; composed into the
        # {deal_context} slot as a session-level constant, identical for every change. Empty=no-op.
        deal_brief = await deal_brief_repo.get_brief(conn, contract_id)
        # F35 / DD-92: the DD-88 PROJECTED clause numbers (node_id -> live number) — the same
        # numbers the review pane shows after pending decisions renumber the document. Reuses the
        # canonical projection (not a second numbering path). Donna's clause grounding is labelled
        # with these (not the baseline `_plan` numbers), so a clause anchor resolves to the number
        # the operator currently sees. One read per session; recomputed on each (re-)run.
        projected_numbers = await projected_clause_numbers(conn, contract_id, session_id)

        change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
        targets: list[tuple[Any, ChangeKind]] = []
        for row in change_rows:
            kind = derive_kind(row["node_id"], row["match_confidence"], row["proposed_order_index"])
            if kind != "abstain" and row["status"] != "complete":
                targets.append((row, kind))
        hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r, _ in targets])

    deal_type = ctype.name if ctype is not None else "contract"
    deal_context = f"Contract type: {deal_type}\nRevision source: {session['source']}"
    # F37 / DD-95: compose the per-deal deal brief INTO the {deal_context} slot, alongside the
    # contract-type / source line — the per-deal global context tier (beside the F32 mandate, which
    # is appended below). Built once per session; a missing/blank brief leaves the slot unchanged.
    deal_brief_block = build_deal_brief_grounding(deal_brief)
    if deal_brief_block:
        deal_context = f"{deal_context}\n\n{deal_brief_block}"
    # F35/DD-92: project the label map onto the LIVE (DD-88) numbers, not baseline. Referenceable
    # clauses Donna anchors to are labelled with the number the pane shows.
    labels = build_projected_label_map(nodes, projected_numbers)
    valid_node_ids = set(labels)
    nodes_by_id = {n.id: n for n in nodes}
    pattern_block = build_pattern_grounding(patterns)
    mandate_block = build_mandate_grounding(firm_profile)
    settings = get_settings()
    model = settings.models.high
    max_tokens = settings.llm.revision_recommend_max_tokens
    temperature = settings.llm.revision_recommend_temperature

    # Build every target hunk into a _Member (with its per-change grounding), then cluster across
    # the WHOLE session by `_cluster_key`: identical counterparty edits judged once, fanned to all
    # (DD-89). Non-clusterable hunks (key None — trivial / whole-node / degenerate) each become a
    # singleton bucket, which is exactly the old per-hunk path (judge once, finalize once).
    # F36 / DD-93: the reference bundle is resolved ONCE per grounding-root node and cached, so a
    # multi-member F34 cluster (and any changes sharing a root) resolve once, never per member.
    ref_bundle_cache: dict[str, str] = {}

    def _reference_bundle(root_id: str | None) -> str:
        if root_id is None or root_id not in nodes_by_id:
            return ""
        if root_id not in ref_bundle_cache:
            # Pass the PROJECTED label map (F35/DD-92) so cross-ref target lines carry the live
            # number, matching the focal clause's lines.
            ref_bundle_cache[root_id] = build_reference_grounding(
                nodes_by_id[root_id], nodes_by_id, defined_terms, cross_refs, labels
            )
        return ref_bundle_cache[root_id]

    members: list[_Member] = []
    for change, kind in targets:
        root_id = _grounding_root(kind, change)
        clause = build_clause_grounding(nodes, root_id, labels)
        bundle = _reference_bundle(root_id)
        if bundle:
            clause = f"{clause}\n\n{bundle}" if clause else bundle
        # F35/DD-92: the node_ids this change may anchor to = the bracketed ids in its assembled
        # grounding (focal subtree + reference bundle), gated to real node ids.
        referenceable = frozenset(referenceable_ids(clause, valid_node_ids))
        change_hunks = hunks_by_change.get(str(change["id"]), [])
        proposed_clause = _proposed_clause_for(kind, change, change_hunks, nodes_by_id)
        for hunk in change_hunks:
            members.append(
                _Member(
                    hunk=hunk,
                    kind=kind,
                    clause=clause,
                    proposed_clause=proposed_clause,
                    referenceable=referenceable,
                )
            )

    clusters: dict[tuple[str, str], list[_Member]] = {}
    for member in members:
        key = _cluster_key(member.hunk)
        bucket_key = key if key is not None else ("\x00singleton", str(member.hunk["id"]))
        clusters.setdefault(bucket_key, []).append(member)

    tally = {"accept": 0, "counter": 0, "keep": 0}
    hunks_analyzed = 0
    writes: list[tuple[str, str, str | None, str, str]] = []
    for bucket in clusters.values():
        rep = bucket[0]
        raw = await _judge(
            kind=rep.kind,
            hunk=rep.hunk,
            deal_context=deal_context,
            clause=rep.clause,
            mandate_block=mandate_block,
            pattern_block=pattern_block,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        for member in bucket:
            rec = finalize_recommendation(
                raw,
                labels,
                member.hunk["original_text"],
                referenceable=set(member.referenceable),
                proposed_text=member.hunk["proposed_text"],
                proposed_clause=member.proposed_clause,
                model=model,
                hunk_id=str(member.hunk["id"]),
            )
            writes.append(
                (
                    str(member.hunk["id"]),
                    rec.verdict,
                    rec.counter_language,
                    rec.significance,
                    rec.reasoning,
                )
            )
            tally[rec.verdict] += 1
            hunks_analyzed += 1

    if writes:
        async with acquire() as conn:
            async with conn.transaction():
                for hunk_id, verdict, counter, significance, rationale in writes:
                    await conn.execute(
                        _UPDATE_HUNK_ADVISORY, hunk_id, verdict, counter, significance, rationale
                    )

    return RevisionRecommendSummary(
        session_id=session_id,
        changes_analyzed=len(targets),
        hunks_analyzed=hunks_analyzed,
        by_verdict=VerdictTally(**tally),
    )


async def _hunks_for(conn: Any, change_ids: list[str]) -> dict[str, list[Any]]:
    if not change_ids:
        return {}
    rows = await conn.fetch(_SELECT_HUNKS, change_ids)
    out: dict[str, list[Any]] = {}
    for r in rows:
        out.setdefault(str(r["change_id"]), []).append(r)
    return out


async def recommend_on_import(session_id: str, changes_count: int) -> None:
    """FAILURE-ISOLATED background entry fired post-commit from the Mode B import route (F03c
    auto-run). Pre-analyses the freshly staged changes so the two-pane review opens with Donna's
    advisory verdict / counter-language already populated and "Use Donna's" works without an
    operator round-trip. `recommend_session` acquires its OWN connection and the whole body
    swallows every error (logged) — a recommendation failure must NEVER fail or roll back the
    import, which has already committed (mirrors F30's distill_on_issue_close).

    Cost guard (~1 Opus call per hunk): auto-run is skipped above the configured staged-change
    ceiling. The skip is logged, never silent — the operator can still trigger the recommend
    endpoint manually for an oversized revision."""
    ceiling = get_settings().llm.revision_recommend_auto_max_changes
    if changes_count > ceiling:
        log.info(
            "revision_recommend.auto_skip_oversized",
            session_id=session_id,
            changes_count=changes_count,
            ceiling=ceiling,
        )
        return
    try:
        summary = await recommend_session(session_id)
        log.info(
            "revision_recommend.auto_done",
            session_id=session_id,
            changes_analyzed=summary.changes_analyzed,
            hunks_analyzed=summary.hunks_analyzed,
        )
    except Exception:
        log.warning("revision_recommend.auto_failed", session_id=session_id, exc_info=True)
