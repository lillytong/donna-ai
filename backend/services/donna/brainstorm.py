"""Ephemeral brainstorm overlay (F10b, DD-73; storage shape DD-77).

Brainstorm is a STATELESS surface (DD-77): the client holds the running brainstorm turns
and replays them each request; the backend persists NOTHING until close.

  * `brainstorm_turn` — one stateless exploratory turn. Resolves the anchor (issue + its
    clause + the committed ledger) LIVE from the DB, reuses advise.py's grounding builders
    and the SAME legal-opinion floor / grounding contract, renders `brainstorm_chat_v1.txt`,
    calls Claude at the HIGH tier, and returns the reply. The running transcript is taken
    from the request (never read from or written to the DB). It persists NOTHING.

  * `distill_brainstorm_summary` — on close, distils the full transcript (held by the client,
    passed in) into ONE compact summary at the MEDIUM tier (Sonnet). Returns None on an
    empty/dismissed brainstorm or an unparseable reply — never fabricates (§2.4).

  * repo helpers `insert_brainstorm_summary` / `list_brainstorm_summaries` — raw SQL + asyncpg
    over `brainstorm_summaries` (mirrors distillation.py's repo style).

Grounding still draws ONLY on the committed ledger + clause text, NEVER the transcript
(DD-42/DD-73 §5). Every LLM call goes through `services/llm.complete`, which logs
model/tokens/latency/caller.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.brainstorm import (
    BrainstormSummary,
    BrainstormTurnRequest,
    BrainstormTurnResponse,
    DistillBrainstormResult,
    StoredBrainstormSummary,
)
from backend.models.donna import DonnaTurn
from backend.prompts.utils import render
from backend.services.contract_repo import fetch_nodes
from backend.services.donna.advise import finalize_reply, parse_reply
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_focus,
    build_issue_ledger,
    build_label_map,
    build_mandate_grounding,
)
from backend.services.donna.windowing import render_history, window
from backend.services.firm_profile_repo import get_firm_profile
from backend.services.issue_repo import get_issue, list_issues
from backend.services.llm import complete

# --- pure helpers (no I/O; unit-testable) ------------------------------------


def parse_summary(text: str) -> BrainstormSummary | None:
    """Parse the close-distillation output into a BrainstormSummary, or None on an honest
    empty (`summary: null`) / a summary with no substantive question+conclusion / an
    unparseable reply. Mirrors advise.parse_reply / distillation.parse_distillation."""

    def _coerce(raw: str) -> BrainstormSummary | None:
        result = DistillBrainstormResult.model_validate_json(raw)
        summary = result.summary
        if summary is None:
            return None
        if not summary.question.strip() and not summary.conclusion.strip():
            return None
        return summary

    try:
        return _coerce(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return _coerce(text[start : end + 1])
            except ValidationError:
                return None
        return None


def render_transcript(turns: list[DonnaTurn]) -> str:
    """The running brainstorm transcript as `You: … / Donna: …` lines (reuses the windowing
    renderer's voice). Empty marker when the operator closed without exploring."""
    return render_history(turns) or "(no brainstorm exchange)"


# --- stateless turn ----------------------------------------------------------


async def brainstorm_turn(
    contract_id: str, request: BrainstormTurnRequest
) -> BrainstormTurnResponse:
    """One stateless brainstorm turn (DD-77). Resolves the issue anchor + grounding LIVE,
    renders the brainstorm prompt with the CLIENT-SUPPLIED running transcript, calls the high
    tier, and returns the reply. Persists NOTHING. A missing/foreign issue falls back to a
    need-context style reply with no anchor."""
    async with acquire() as conn:
        nodes = await fetch_nodes(conn, contract_id)
        issues = await list_issues(conn, contract_id)
        issue = await get_issue(conn, request.issue_id)
        if issue is not None and issue.contract_id != contract_id:
            issue = None
        # F32 v1 / DD-90: the global operator-authored firm profile — the firm's standing MANDATE
        # (who we are, our interests, our red-lines). One read per request, grounds every turn.
        firm_profile = await get_firm_profile(conn)

    labels = build_label_map(nodes)
    clauses = build_clause_grounding(nodes, issue.node_id, labels) if issue is not None else ""
    settings = get_settings()
    prompt = render(
        "brainstorm_chat_v1.txt",
        clauses=clauses or "(no clause anchored)",
        issue=(build_issue_focus(issue, labels) if issue is not None else "(no active issue)"),
        ledger=build_issue_ledger(issues, labels) or "(no issues on record)",
        history=render_history(window(request.turns)) or "(no earlier brainstorm)",
        question=request.message,
    )
    # The mandate is appended AFTER the rendered prompt (not a template slot) so it stays
    # non-authoritative DATA/context and the prompt template/eval stay untouched (mirrors the
    # revision recommender, F32/DD-90). Empty profile -> '' -> no-op.
    mandate_block = build_mandate_grounding(firm_profile)
    if mandate_block:
        prompt = f"{prompt}\n\n{mandate_block}"
    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_brainstorm_turn",
        max_tokens=settings.llm.brainstorm_chat_max_tokens,
        temperature=settings.llm.brainstorm_chat_temperature,
        json_response=True,
    )

    valid_ids = {n.id for n in nodes} | {i.id for i in issues}
    id_labels = {**labels, **{i.id: i.title for i in issues}}
    reply = finalize_reply(parse_reply(result.text), valid_ids, id_labels)
    return BrainstormTurnResponse(reply=reply.reply, citations=reply.citations)


# --- on-close distillation ---------------------------------------------------


async def distill_brainstorm_summary(
    conn: Any, issue_id: str, turns: list[DonnaTurn]
) -> BrainstormSummary | None:
    """Distil the brainstorm transcript into ONE compact summary at the MEDIUM tier (DD-73).
    Returns None on a missing/foreign issue, an empty transcript, an honest-empty model reply,
    or an unparseable reply — never fabricates (§2.4). Reads the issue's committed ledger +
    anchored clause for context; the transcript is the thing being summarised, not grounding."""
    if not turns:
        return None
    issue = await get_issue(conn, issue_id)
    if issue is None:
        return None

    nodes = await fetch_nodes(conn, issue.contract_id)
    labels = build_label_map(nodes)
    settings = get_settings()
    prompt = render(
        "distill_brainstorm_v1.txt",
        issue=build_issue_focus(issue, labels),
        clauses=build_clause_grounding(nodes, issue.node_id, labels)
        or "(no anchored clause — free-floating issue)",
        transcript=render_transcript(turns),
    )
    result = await complete(
        tier="medium",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_brainstorm_distill",
        max_tokens=settings.llm.brainstorm_distill_max_tokens,
        temperature=settings.llm.brainstorm_distill_temperature,
        json_response=True,
    )
    return parse_summary(result.text)


# --- persistence (raw SQL, asyncpg) ------------------------------------------

_INSERT = """
INSERT INTO brainstorm_summaries (issue_id, question, conclusion, fallbacks)
VALUES ($1, $2, $3, $4)
RETURNING id, issue_id, question, conclusion, fallbacks, created_at
"""

_LIST_FOR_ISSUE = """
SELECT id, issue_id, question, conclusion, fallbacks, created_at
FROM brainstorm_summaries
WHERE issue_id = $1
ORDER BY created_at DESC
"""


def _to_summary(record: Any) -> StoredBrainstormSummary:
    return StoredBrainstormSummary(
        id=str(record["id"]),
        issue_id=str(record["issue_id"]),
        question=record["question"],
        conclusion=record["conclusion"],
        fallbacks=record["fallbacks"],
        created_at=record["created_at"],
    )


async def insert_brainstorm_summary(
    conn: Any, issue_id: str, summary: BrainstormSummary
) -> StoredBrainstormSummary:
    record = await conn.fetchrow(
        _INSERT, issue_id, summary.question, summary.conclusion, summary.fallbacks
    )
    return _to_summary(record)


async def list_brainstorm_summaries(conn: Any, issue_id: str) -> list[StoredBrainstormSummary]:
    """An issue's brainstorm history — every distilled pass, newest first."""
    records = await conn.fetch(_LIST_FOR_ISSUE, issue_id)
    return [_to_summary(r) for r in records]


# --- close orchestration -----------------------------------------------------


async def close_brainstorm(
    contract_id: str, issue_id: str, turns: list[DonnaTurn]
) -> StoredBrainstormSummary | None:
    """Distil the transcript and store the summary on the issue (DD-73). Returns the stored
    summary, or None when there was nothing substantive to distil (no row written). The
    `contract_id` scopes the issue read so a foreign issue is rejected before distillation."""
    async with acquire() as conn:
        issue = await get_issue(conn, issue_id)
        if issue is None or issue.contract_id != contract_id:
            return None
        summary = await distill_brainstorm_summary(conn, issue_id, turns)
        if summary is None:
            return None
        return await insert_brainstorm_summary(conn, issue_id, summary)
