"""Negotiation insight distillation (F30, DD-76; amends DD-55/DD-73) — Donna's cross-deal
negotiation memory.

TRIGGER = ISSUE-CLOSE (not brainstorm-close). When an issue's status flips to `closed`,
this runs an LLM pass over the COMMITTED issue ledger (title, our/their position, options,
decision, status, anchored clause text) and distils 0-N compact, transferable negotiation
PATTERNS into `negotiation_patterns`. It never reads a raw brainstorm transcript — the
committed ledger is grounding-safe by construction (DD-76 reconciles DD-55 ↔ DD-73).

Pipeline (single linear shot, no LangGraph — DD-52), at the MEDIUM tier (Sonnet — judgment,
but internal and never counterparty-facing, DD-35):
  1. Load the closed issue + its contract context (deal/client/contract-type ids) + the
     anchored clause grounding (REUSE grounding.py).
  2. Fetch the small set of existing patterns for the subjects relevant to this issue
     (operator-global + this client + this contract type) — the merge-first candidate set,
     folded into the SAME extraction call.
  3. Render `distill_v1.txt`, call Claude (structured JSON). Honest: an empty list is the
     expected output when there is no durable pattern — never manufacture one.
  4. Merge-first persist: a candidate tagged `reinforces_id` (a real existing id) increments
     evidence + refines wording + bumps confidence; a `contradicts_id` surfaces a stored
     contradiction flag (never a silent overwrite); anything else inserts a new record.
  5. Consolidation: once >= N new patterns accumulate, prune unreinforced low-evidence
     patterns past TTL and collapse exact duplicates — so the store converges to ~100-200.

`subject_ref` is DERIVED from the issue's contract context, never from the model (the LLM
only proposes subject_type + insight), so a hallucinated id can never reach the table.

RETRIEVAL (F30 tier 8): `fetch_patterns_for_issue` returns the patterns Donna injects when
she opens an issue (recommendations.py). Patterns are a retrieval INPUT — never authoritative,
never cited, never exported (§2.4).

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.insights import (
    CandidatePattern,
    DistillationResult,
    StoredPattern,
)
from backend.models.issues import StoredIssue
from backend.prompts.utils import render
from backend.services.contract_repo import fetch_nodes
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_focus,
    build_label_map,
)
from backend.services.issue_repo import get_issue
from backend.services.llm import complete

log = structlog.get_logger()

# Audit event_type is free-form TEXT (no CHECK), so a local constant suffices — it doubles
# as the consolidation high-water mark (count patterns created since the last one).
_EVENT_PATTERNS_CONSOLIDATED = "patterns_consolidated"

# An unparseable model output yields NO patterns — the honest, safe outcome (never fabricate,
# never surface raw output, §2.4). Mirrors qa.parse_answer / recommendations.parse_draft.
_EMPTY = DistillationResult(patterns=[])


# --- pure helpers (no I/O; unit-testable, reused by the eval) ----------------


def parse_distillation(text: str) -> DistillationResult:
    """Tolerate a non-strict JSON extraction; an unparseable one becomes the empty result."""
    try:
        return DistillationResult.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return DistillationResult.model_validate_json(text[start : end + 1])
            except ValidationError:
                return _EMPTY
        return _EMPTY


def subject_ref_for(
    subject_type: str, client_id: str | None, contract_type_id: str | None
) -> str | None:
    """Derive the polymorphic subject reference from the closed issue's contract context —
    NEVER from the model. operator_style / legal_team_tendency are operator-global (null);
    counterparty_behavior keys on the client; deal_type_norm keys on the contract type."""
    if subject_type == "counterparty_behavior":
        return client_id
    if subject_type == "deal_type_norm":
        return contract_type_id
    return None  # operator_style, legal_team_tendency


def build_existing_block(patterns: list[StoredPattern]) -> str:
    """The merge-first candidate set as `[id] (<subject_type>) <insight>` lines for the
    prompt. The model may only echo these ids in reinforces_id / contradicts_id."""
    if not patterns:
        return "(no existing patterns for these subjects yet)"
    return "\n".join(f"[{p.id}] ({p.subject_type}) {p.insight}" for p in patterns)


# --- persistence (raw SQL, asyncpg) ------------------------------------------

_SELECT = """
SELECT id, subject_type, subject_ref, insight, evidence_count, confidence,
       contradiction_flag, last_reinforced_at, last_reinforced_deal_id, is_deleted,
       created_at, updated_at
FROM negotiation_patterns
"""

# Subjects relevant to one closed issue (and to retrieval on issue-open): operator-global
# style, this client's behaviour, this contract type's norm, operator-global legal tendency.
_SELECT_FOR_CONTEXT = (
    _SELECT
    + """
WHERE is_deleted = false AND (
      (subject_type = 'operator_style'      AND subject_ref IS NULL)
   OR (subject_type = 'legal_team_tendency' AND subject_ref IS NULL)
   OR (subject_type = 'counterparty_behavior' AND subject_ref = $1)
   OR (subject_type = 'deal_type_norm'        AND subject_ref = $2))
ORDER BY confidence DESC, evidence_count DESC, created_at
"""
)

_INSERT = """
INSERT INTO negotiation_patterns
    (subject_type, subject_ref, insight, confidence, contradiction_flag,
     last_reinforced_at, last_reinforced_deal_id)
VALUES ($1, $2, $3, $4, $5, now(), $6)
RETURNING id, subject_type, subject_ref, insight, evidence_count, confidence,
          contradiction_flag, last_reinforced_at, last_reinforced_deal_id, is_deleted,
          created_at, updated_at
"""

_REINFORCE = """
UPDATE negotiation_patterns
SET insight = $2,
    evidence_count = evidence_count + 1,
    confidence = LEAST(1.0, confidence + $3),
    contradiction_flag = contradiction_flag OR $4,
    last_reinforced_at = now(),
    last_reinforced_deal_id = $5,
    updated_at = now()
WHERE id = $1 AND is_deleted = false
RETURNING id, subject_type, subject_ref, insight, evidence_count, confidence,
          contradiction_flag, last_reinforced_at, last_reinforced_deal_id, is_deleted,
          created_at, updated_at
"""

_FLAG_CONTRADICTION = """
UPDATE negotiation_patterns
SET contradiction_flag = true, updated_at = now()
WHERE id = $1 AND is_deleted = false
"""

_GET_CONTRACT_CONTEXT = "SELECT deal_id, client_id, contract_type_id FROM contracts WHERE id = $1"


def _to_pattern(record: Any) -> StoredPattern:
    ref = record["subject_ref"]
    deal = record["last_reinforced_deal_id"]
    return StoredPattern(
        id=str(record["id"]),
        subject_type=record["subject_type"],
        subject_ref=str(ref) if ref is not None else None,
        insight=record["insight"],
        evidence_count=record["evidence_count"],
        confidence=record["confidence"],
        contradiction_flag=record["contradiction_flag"],
        last_reinforced_at=record["last_reinforced_at"],
        last_reinforced_deal_id=str(deal) if deal is not None else None,
        is_deleted=record["is_deleted"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


async def fetch_patterns_for_context(
    conn: Any, client_id: str | None, contract_type_id: str | None
) -> list[StoredPattern]:
    """The small candidate/retrieval set for one issue's subjects (merge-first input AND the
    retrieval injection for recommendations.py)."""
    records = await conn.fetch(_SELECT_FOR_CONTEXT, client_id, contract_type_id)
    return [_to_pattern(r) for r in records]


async def fetch_patterns_for_issue(conn: Any, contract_id: str) -> list[StoredPattern]:
    """Retrieval entry: resolve the contract's client + type, return the patterns to inject
    when Donna opens an issue on it (operator-style always; counterparty when same client;
    deal-type when same contract type). Empty list if the contract is unknown."""
    ctx = await conn.fetchrow(_GET_CONTRACT_CONTEXT, contract_id)
    if ctx is None:
        return []
    client_id = str(ctx["client_id"]) if ctx["client_id"] is not None else None
    contract_type_id = (
        str(ctx["contract_type_id"]) if ctx["contract_type_id"] is not None else None
    )
    return await fetch_patterns_for_context(conn, client_id, contract_type_id)


# --- merge-first apply (pure decision, applied via the repo) -----------------


async def apply_candidates(
    conn: Any,
    candidates: list[CandidatePattern],
    existing: list[StoredPattern],
    *,
    client_id: str | None,
    contract_type_id: str | None,
    deal_id: str | None,
) -> list[StoredPattern]:
    """Merge-first persist of the model's candidates. A candidate whose `reinforces_id` is a
    REAL existing id refines that pattern (increment + bump + refine wording); a real
    `contradicts_id` surfaces a contradiction flag (never silent overwrite); everything else
    inserts a new record. Hallucinated ids (not in the existing set) are ignored, falling
    through to an insert — so the model can never touch a row it wasn't shown."""
    settings = get_settings().distillation
    valid_ids = {p.id for p in existing}
    out: list[StoredPattern] = []
    for cand in candidates:
        insight = cand.insight.strip()
        if not insight:
            continue
        contradicts = cand.contradicts_id if cand.contradicts_id in valid_ids else None
        if contradicts is not None:
            await conn.execute(_FLAG_CONTRADICTION, contradicts)
        if cand.reinforces_id in valid_ids:
            row = await conn.fetchrow(
                _REINFORCE,
                cand.reinforces_id,
                insight,
                settings.reinforce_increment,
                contradicts is not None,
                deal_id,
            )
            if row is not None:
                out.append(_to_pattern(row))
            continue
        subject_ref = subject_ref_for(cand.subject_type, client_id, contract_type_id)
        row = await conn.fetchrow(
            _INSERT,
            cand.subject_type,
            subject_ref,
            insight,
            settings.new_confidence,
            contradicts is not None,
            deal_id,
        )
        out.append(_to_pattern(row))
    return out


# --- consolidation / prune (deterministic, no LLM — cheap cleanup, DD-35) ----

_COUNT_SINCE_LAST_CONSOLIDATION = """
SELECT count(*) FROM negotiation_patterns
WHERE is_deleted = false
  AND created_at > COALESCE(
      (SELECT max(created_at) FROM audit_log WHERE event_type = $1), 'epoch'::timestamptz)
"""

_LIVE_PATTERNS = _SELECT + "WHERE is_deleted = false ORDER BY created_at"

# Distinct deals with a closed issue resolved AFTER the pattern was last reinforced — the
# "unreinforced across N deals" TTL signal (DD-55/DD-76).
_DEALS_SINCE_REINFORCED = """
SELECT count(DISTINCT c.deal_id)
FROM issues i JOIN contracts c ON c.id = i.contract_id
WHERE i.status = 'closed' AND i.resolved_at > $1
"""

_SOFT_DELETE = "UPDATE negotiation_patterns SET is_deleted = true, updated_at = now() WHERE id = $1"

_FOLD = """
UPDATE negotiation_patterns
SET evidence_count = evidence_count + $2, updated_at = now()
WHERE id = $1
"""


async def consolidate(conn: Any) -> dict[str, int]:
    """Prune + dedup pass. Prune patterns still at minimum evidence (never reinforced) that
    have gone unreinforced across >= `prune_deals` distinct closed-issue deals; collapse
    exact-duplicate insights within the same (subject_type, subject_ref). Records the
    consolidation marker (also the N-counter high-water mark). Returns counts."""
    from backend.models.audit import AuditEvent
    from backend.services.audit_repo import record_event

    prune_deals = get_settings().distillation.prune_deals
    live = [_to_pattern(r) for r in await conn.fetch(_LIVE_PATTERNS)]

    pruned = 0
    survivors: list[StoredPattern] = []
    for p in live:
        if p.evidence_count <= 1:
            deals = await conn.fetchval(_DEALS_SINCE_REINFORCED, p.last_reinforced_at)
            if deals is not None and deals >= prune_deals:
                await conn.execute(_SOFT_DELETE, p.id)
                pruned += 1
                continue
        survivors.append(p)

    # Collapse exact-duplicate insights within a subject — keep the earliest, fold evidence.
    merged = 0
    seen: dict[tuple[str, str | None, str], StoredPattern] = {}
    for p in survivors:
        key = (p.subject_type, p.subject_ref, p.insight.strip().lower())
        keeper = seen.get(key)
        if keeper is None:
            seen[key] = p
        else:
            await conn.execute(_FOLD, keeper.id, p.evidence_count)
            await conn.execute(_SOFT_DELETE, p.id)
            merged += 1

    await record_event(
        conn,
        AuditEvent(
            event_type=_EVENT_PATTERNS_CONSOLIDATED,
            entity_type="negotiation_patterns",
            entity_id=None,
            actor=get_settings().operator_actor,
            payload={"pruned": pruned, "merged": merged},
        ),
    )
    return {"pruned": pruned, "merged": merged}


async def _maybe_consolidate(conn: Any) -> None:
    """N-counter trigger: consolidate once >= consolidate_after_n new patterns have been
    added since the last consolidation (the deal-close handler exists at
    settings_repo.update_deal but is outside this build's file scope — see DD-76)."""
    n = get_settings().distillation.consolidate_after_n
    new_count = await conn.fetchval(_COUNT_SINCE_LAST_CONSOLIDATION, _EVENT_PATTERNS_CONSOLIDATED)
    if new_count is not None and new_count >= n:
        await consolidate(conn)


# --- orchestration -----------------------------------------------------------


async def _distill(conn: Any, issue: StoredIssue) -> list[StoredPattern]:
    """The LLM extraction + merge-first persist for one closed issue, on the given conn."""
    ctx = await conn.fetchrow(_GET_CONTRACT_CONTEXT, issue.contract_id)
    deal_id = str(ctx["deal_id"]) if ctx and ctx["deal_id"] is not None else None
    client_id = str(ctx["client_id"]) if ctx and ctx["client_id"] is not None else None
    contract_type_id = (
        str(ctx["contract_type_id"]) if ctx and ctx["contract_type_id"] is not None else None
    )

    nodes = await fetch_nodes(conn, issue.contract_id)
    labels = build_label_map(nodes)
    existing = await fetch_patterns_for_context(conn, client_id, contract_type_id)

    settings = get_settings()
    prompt = render(
        "distill_v1.txt",
        issue=build_issue_focus(issue, labels),
        clauses=build_clause_grounding(nodes, issue.node_id, labels)
        or "(no anchored clause — free-floating issue)",
        existing=build_existing_block(existing),
    )
    result = await complete(
        tier="medium",
        messages=[{"role": "user", "content": prompt}],
        caller="distillation",
        max_tokens=settings.distillation.max_tokens,
        temperature=settings.distillation.temperature,
        json_response=True,
    )
    parsed = parse_distillation(result.text)
    if not parsed.patterns:
        return []
    stored = await apply_candidates(
        conn,
        parsed.patterns,
        existing,
        client_id=client_id,
        contract_type_id=contract_type_id,
        deal_id=deal_id,
    )
    await _maybe_consolidate(conn)
    return stored


async def distill_issue(conn: Any, issue_id: str) -> list[StoredPattern]:
    """Distil patterns from one closed issue on the given connection (raises on a real error;
    used by tests and any caller that wants the result). Skips a non-closed issue."""
    issue = await get_issue(conn, issue_id)
    if issue is None or issue.status != "closed":
        return []
    return await _distill(conn, issue)


async def distill_on_issue_close(issue_id: str) -> None:
    """FAILURE-ISOLATED background entry fired from the issue-close route (DD-76). Acquires
    its OWN connection and swallows every error (logged) — a distillation failure must NEVER
    fail or roll back the issue close (mirrors the defined-terms auto-extract-on-commit
    isolation in api/imports.py)."""
    try:
        async with acquire() as conn:
            patterns = await distill_issue(conn, issue_id)
        log.info("distillation_done", issue_id=issue_id, patterns=len(patterns))
    except Exception:
        log.warning("distillation_failed", issue_id=issue_id, exc_info=True)
