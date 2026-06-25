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
     (original_text → proposed_text). Render `revision_recommend_v1.txt` and call Claude at the
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
prompt. `revision_recommend_v1.txt` frames all document text as DATA, not instructions. This
remains a residual risk area (open product item on injection stance).

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.revision_recommend import (
    RevisionRecommendation,
    RevisionRecommendSummary,
    VerdictTally,
)
from backend.prompts.utils import render
from backend.services.contract_repo import fetch_nodes
from backend.services.donna.distillation import fetch_patterns_for_issue
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_label_map,
    build_pattern_grounding,
)
from backend.services.donna.qa import scrub_leaked_ids
from backend.services.llm import complete
from backend.services.settings_repo import get_contract, get_contract_type

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
SELECT id, change_id, hunk_type, original_text, proposed_text
FROM counterparty_revision_hunks
WHERE change_id = ANY($1::uuid[])
ORDER BY change_id, position_in_body NULLS FIRST, id
"""

# Writes ONLY the advisory columns (+ Donna's significance call). NEVER verdict / final_text
# (DD-64: the applied text is the deterministic diff, never Donna's suggestion).
_UPDATE_HUNK_ADVISORY = """
UPDATE counterparty_revision_hunks
SET donna_verdict = $2, donna_counter_text = $3, significance = $4
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


def finalize_recommendation(
    rec: RevisionRecommendation, id_labels: dict[str, str]
) -> RevisionRecommendation:
    """Pure post-LLM cleanup + invariant enforcement: scrub any leaked id out of the prose, and
    guarantee counter-language exists IFF verdict == counter — a trivial hunk never carries it
    (schema: donna_counter_text is null for trivial hunks), and a `counter` with no usable
    language collapses to the safe `keep` (the operator's later "counter" action would otherwise
    have no staged text to send)."""
    reasoning = scrub_leaked_ids(rec.reasoning, id_labels)
    counter = rec.counter_language
    if counter is not None:
        counter = scrub_leaked_ids(counter, id_labels).strip() or None

    verdict = rec.verdict
    if rec.significance == "trivial":
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
    )


# --- orchestration -----------------------------------------------------------


async def _analyze_hunk(
    *,
    kind: ChangeKind,
    hunk: Any,
    deal_context: str,
    clause: str,
    pattern_block: str,
    id_labels: dict[str, str],
    max_tokens: int,
    temperature: float,
) -> RevisionRecommendation:
    """One LLM call: ground the hunk, render the prompt, parse + finalize. The learned-pattern
    block is appended AFTER the rendered prompt (not a template slot) so patterns stay visibly
    non-authoritative and the prompt template/eval are untouched (mirrors F11, DD-76)."""
    prompt = render(
        "revision_recommend_v1.txt",
        deal_context=deal_context,
        clause=clause or "(no baseline clause resolved)",
        change=build_change_focus(
            kind, hunk["hunk_type"], hunk["original_text"], hunk["proposed_text"]
        ),
    )
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
    return finalize_recommendation(parse_recommendation(result.text), id_labels)


def _grounding_root(kind: ChangeKind, change: Any) -> str | None:
    """The node whose subtree grounds this change: the matched node for edited/deleted; the
    proposed parent (for its neighbouring clauses) for a new node."""
    if kind in ("edited", "deleted"):
        node_id = change["node_id"]
        return str(node_id) if node_id is not None else None
    parent = change["proposed_parent_id"]
    return str(parent) if parent is not None else None


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

        change_rows = await conn.fetch(_SELECT_CHANGES, session_id)
        targets: list[tuple[Any, ChangeKind]] = []
        for row in change_rows:
            kind = derive_kind(row["node_id"], row["match_confidence"], row["proposed_order_index"])
            if kind != "abstain" and row["status"] != "complete":
                targets.append((row, kind))
        hunks_by_change = await _hunks_for(conn, [str(r["id"]) for r, _ in targets])

    deal_type = ctype.name if ctype is not None else "contract"
    deal_context = f"Contract type: {deal_type}\nRevision source: {session['source']}"
    labels = build_label_map(nodes)
    pattern_block = build_pattern_grounding(patterns)
    settings = get_settings()
    max_tokens = settings.llm.revision_recommend_max_tokens
    temperature = settings.llm.revision_recommend_temperature

    tally = {"accept": 0, "counter": 0, "keep": 0}
    hunks_analyzed = 0
    writes: list[tuple[str, str, str | None, str]] = []
    for change, kind in targets:
        clause = build_clause_grounding(nodes, _grounding_root(kind, change), labels)
        for hunk in hunks_by_change.get(str(change["id"]), []):
            rec = await _analyze_hunk(
                kind=kind,
                hunk=hunk,
                deal_context=deal_context,
                clause=clause,
                pattern_block=pattern_block,
                id_labels=labels,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            writes.append((str(hunk["id"]), rec.verdict, rec.counter_language, rec.significance))
            tally[rec.verdict] += 1
            hunks_analyzed += 1

    if writes:
        async with acquire() as conn:
            async with conn.transaction():
                for hunk_id, verdict, counter, significance in writes:
                    await conn.execute(
                        _UPDATE_HUNK_ADVISORY, hunk_id, verdict, counter, significance
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
