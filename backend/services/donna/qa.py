"""Donna single-contract grounded Q&A (F10) — the read-and-explain assistant.

Pipeline (single linear shot, no LangGraph — DD-52):
  1. Retrieve the clause the operator *means* via the F05b conceptual lookup
     (`search_clause`, no embeddings); load the issue ledger + status for status-briefing
     questions; load the windowed conversation history (DD-40: last 10 turns + rolling
     summary).
  2. Assemble label-tagged grounding (grounding.py) and render the versioned prompt
     (`donna_qa_v3.txt`) — answer ONLY from that content, refer to clauses by their
     legible label (never the raw id), cite node/issue ids in the citations array,
     read-and-explain only (advice/position -> deflect, DD-14), honest miss otherwise.
  3. Call Claude at a CAPABLE tier with structured JSON output; validate citations
     against the real id set (hallucinated-id guard, mirrors clause_search).
  4. Persist the turn; fold the just-evicted turn into the rolling summary (DD-40).

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/
caller (CLAUDE.md)."""

from __future__ import annotations

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.donna import (
    DonnaAskResponse,
    DonnaClearResponse,
    DonnaStructuredAnswer,
    DonnaThreadResponse,
    DonnaTurn,
)
from backend.prompts.utils import render
from backend.services.clause_search import search_clause
from backend.services.contract_repo import fetch_nodes
from backend.services.donna.conversation_repo import (
    append_message,
    clear_conversation,
    fetch_messages,
    get_or_create_conversation,
    update_summary,
)
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_ledger,
    build_label_map,
    build_mandate_grounding,
)
from backend.services.donna.windowing import (
    evicted_turn,
    render_history,
    to_turns,
    window,
)
from backend.services.firm_profile_repo import get_firm_profile
from backend.services.issue_repo import list_issues
from backend.services.llm import complete

# Read-and-explain failsafe: an unparseable model answer is treated as an honest miss
# rather than surfaced raw — never fabricate, never leak malformed output (§2.4).
_FALLBACK = DonnaStructuredAnswer(
    answer="I couldn't read a grounded answer to that from this contract.",
    kind="not_found",
    citations=[],
)


def scrub_leaked_ids(text: str, id_labels: dict[str, str]) -> str:
    """Defense-in-depth (CLAUDE.md §2.4): even though the prompt forbids it, replace any
    raw node/issue id that slipped into the prose with its legible label, so a UUID never
    reaches the user. The citations array (carried separately) keeps the ids untouched.
    Longest ids first so a substring id can't pre-empt a longer one."""
    for id_, label in sorted(id_labels.items(), key=lambda kv: -len(kv[0])):
        if id_ in text:
            text = text.replace(id_, label)
    return text


def parse_answer(text: str) -> DonnaStructuredAnswer:
    """Tolerate a non-strict JSON answer; an unparseable one becomes an honest miss."""
    try:
        return DonnaStructuredAnswer.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return DonnaStructuredAnswer.model_validate_json(text[start : end + 1])
            except ValidationError:
                return _FALLBACK
        return _FALLBACK


async def _update_rolling_summary(
    conn: object, conversation_id: str, prior: str | None, turns_after: list[DonnaTurn]
) -> None:
    evicted = evicted_turn(turns_after)
    if evicted is None:
        return
    settings = get_settings()
    prompt = render(
        "donna_summary_v1.txt",
        prior_summary=prior or "(none)",
        question=evicted.question,
        answer=evicted.answer,
    )
    result = await complete(
        tier="low",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_summary",
        max_tokens=settings.llm.donna_summary_max_tokens,
        temperature=settings.llm.donna_summary_temperature,
    )
    await update_summary(conn, conversation_id, result.text.strip())


async def ask(
    contract_id: str, question: str, deflection_text: str | None = None
) -> DonnaAskResponse:
    # `deflection_text` (F10b): when the context-aware chat calls in with the softer
    # acquire-context wording, a DEFLECTED turn is persisted with THAT text (and no
    # citations) so a reloaded thread matches the live reply. The F10 direct path passes
    # nothing and keeps the model's own deflection prose — preserving the read-and-explain eval.
    # F05b conceptual retrieval (own connection); no embeddings (DD-62).
    retrieval = await search_clause(contract_id, question)

    async with acquire() as conn:
        conversation = await get_or_create_conversation(conn, contract_id)
        prior_summary = conversation.running_summary
        messages = await fetch_messages(conn, conversation.id)
        issues = await list_issues(conn, contract_id)
        nodes = await fetch_nodes(conn, contract_id)
        # F32 v1 / DD-90: the global operator-authored firm profile — the firm's standing MANDATE
        # (who we are, our interests, our red-lines). One read per request, grounds every answer.
        firm_profile = await get_firm_profile(conn)

    turns = to_turns(messages)
    labels = build_label_map(nodes)
    prompt = render(
        "donna_qa_v3.txt",
        clauses=build_clause_grounding(nodes, retrieval.node_id, labels)
        or "(no matching clause found)",
        issues=build_issue_ledger(issues, labels) or "(no issues on record)",
        summary=prior_summary or "(none)",
        history=render_history(window(turns)) or "(no earlier conversation)",
        question=question,
    )
    # The mandate is appended AFTER the rendered prompt (not a template slot) so it stays
    # non-authoritative DATA/context and the prompt template/eval stay untouched (mirrors the
    # revision recommender, F32/DD-90). Empty profile -> '' -> no-op.
    mandate_block = build_mandate_grounding(firm_profile)
    if mandate_block:
        prompt = f"{prompt}\n\n{mandate_block}"

    settings = get_settings()
    result = await complete(
        tier="medium",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_qa",
        max_tokens=settings.llm.donna_qa_max_tokens,
        temperature=settings.llm.donna_qa_temperature,
        json_response=True,
    )

    answer = parse_answer(result.text)
    valid_ids = {n.id for n in nodes} | {i.id for i in issues}
    citations = [c for c in answer.citations if c in valid_ids]

    # Scrub any leaked id from the prose (the citations array keeps the ids).
    id_labels = {**labels, **{i.id: i.title for i in issues}}
    answer_text = scrub_leaked_ids(answer.answer, id_labels)

    # F10b: persist the softer acquire-context wording for a deflection (and drop the
    # wall's citations) so the stored turn matches what advise.from_qa returns live.
    if deflection_text is not None and answer.kind == "deflected":
        answer_text = deflection_text
        citations = []

    async with acquire() as conn:
        await append_message(conn, conversation.id, "user", question)
        await append_message(
            conn,
            conversation.id,
            "assistant",
            answer_text,
            kind=answer.kind,
            citations=citations,
        )
        turns_after = [*turns, DonnaTurn(question=question, answer=answer_text)]
        await _update_rolling_summary(conn, conversation.id, prior_summary, turns_after)

    return DonnaAskResponse(
        answer=answer_text,
        citations=citations,
        deflected=answer.kind == "deflected",
        kind=answer.kind,
    )


async def get_thread(contract_id: str) -> DonnaThreadResponse:
    """The persisted per-contract conversation (full history, read on demand)."""
    async with acquire() as conn:
        conversation = await get_or_create_conversation(conn, contract_id)
        messages = await fetch_messages(conn, conversation.id)
    return DonnaThreadResponse(
        conversation_id=conversation.id,
        running_summary=conversation.running_summary,
        messages=messages,
    )


async def clear_thread(contract_id: str) -> DonnaClearResponse:
    """Wipe the contract's persisted Donna conversation (messages + rolling summary) so
    the next `ask`/`thread` starts empty (DD-40 threads never auto-clear)."""
    async with acquire() as conn:
        await clear_conversation(conn, contract_id)
    return DonnaClearResponse(cleared=True)
