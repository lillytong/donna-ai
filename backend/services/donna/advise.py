"""Context-aware Donna chat (F10b) — the advise + draft conversational surface that
relaxes the F10 read-and-explain boundary, but ONLY on a grounded anchor (DD-14 spirit:
the property that made an issue safe — a grounded, cited anchor — is now the entry point,
not the existence of an issue row). Legal opinions stay categorically walled regardless of
context.

Two paths, one entry (`chat`):

  * NO context (nothing selected) -> delegate to the validated F10 `qa.ask` so its
    read-and-explain behavior (locate/explain/status, honest miss, citation guard, DD-40
    windowing + persistence) is preserved EXACTLY. The only change: an advice/position
    request that F10 would *deflect* is re-skinned to the softer "tell me which clause"
    acquire-context deflection (mode `need_context`) — a path forward, not the old wall.

  * WITH context (selected clause(s) and/or an active issue) -> assemble grounding LIVE
    from the DB each turn (clause subtrees via build_clause_grounding + issue focus/ledger
    + the DD-40 windowed history), render the versioned `donna_chat_advise_v1.txt`, call
    Claude at the HIGH tier (advice/drafting is high-consequence — mirrors F11/F08d; Opus
    rejects temp 0.0 so the tier is pinned to 1.0), and return a structured chat reply.
    Citations are validated against the real id set and every prose field is id-scrubbed
    (reused from qa.py). The turn is persisted via conversation_repo with `mode` mapped
    down to the schema-pinned `kind`.

Every LLM call goes through `services/llm.complete`, which logs model/tokens/latency/caller.
"""

from __future__ import annotations

from pydantic import ValidationError

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.donna import (
    DonnaAnswerKind,
    DonnaAskResponse,
    DonnaChatMode,
    DonnaChatReply,
    DonnaChatResponse,
    DonnaContext,
    DonnaTurn,
)
from backend.prompts.utils import render
from backend.services.contract_repo import fetch_nodes
from backend.services.donna import qa
from backend.services.donna.conversation_repo import (
    append_message,
    fetch_messages,
    get_or_create_conversation,
)
from backend.services.donna.grounding import (
    build_clause_grounding,
    build_issue_focus,
    build_issue_ledger,
    build_label_map,
)
from backend.services.donna.qa import scrub_leaked_ids
from backend.services.donna.windowing import render_history, to_turns, window
from backend.services.issue_repo import get_issue, list_issues
from backend.services.llm import complete

# The softer no-context deflection (F10b): acquire context rather than wall off (DD-14
# relaxed). Replaces the old "raise an issue / get a lawyer" deflection text for a plain
# advice/position request when nothing is selected.
_ACQUIRE_CONTEXT = (
    "Tell me which clause you mean — select it, or open the issue — and I'll advise you "
    "there. With nothing in context I can only read and explain this contract."
)

# An unparseable model reply is surfaced as an honest miss, never raw, never fabricated
# (CLAUDE.md privacy/§2.4). No draft, no citations.
_FALLBACK = DonnaChatReply(
    reply="I couldn't read a grounded reply to that from this contract.",
    mode="explain",
    citations=[],
    draft_language=None,
)

# mode -> the schema-pinned persisted `kind` (db/schema.sql donna_messages.kind CHECK is
# answer/not_found/deflected). advise/draft/explain are answers; legal_referral/need_context
# are deflection-shaped treatments. The richer `mode` lives in the API response only.
_MODE_TO_KIND: dict[DonnaChatMode, DonnaAnswerKind] = {
    "explain": "answer",
    "advise": "answer",
    "draft": "answer",
    "legal_referral": "deflected",
    "need_context": "deflected",
}


def has_context(context: DonnaContext | None) -> bool:
    """A grounded anchor is present iff at least one node is selected or an issue is open.
    An empty context object is treated as no-context (read-and-explain)."""
    return context is not None and (bool(context.node_ids) or context.issue_id is not None)


def parse_reply(text: str) -> DonnaChatReply:
    """Tolerate a non-strict JSON reply; an unparseable one becomes the honest fallback
    (mirrors qa.parse_answer / recommendations.parse_draft)."""
    try:
        return DonnaChatReply.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return DonnaChatReply.model_validate_json(text[start : end + 1])
            except ValidationError:
                return _FALLBACK
        return _FALLBACK


def finalize_reply(
    reply: DonnaChatReply, valid_ids: set[str], id_labels: dict[str, str]
) -> DonnaChatReply:
    """Pure post-LLM cleanup: drop hallucinated citations (keep only real node/issue ids)
    and scrub any leaked id out of `reply` and `draft_language`, replacing it with its
    legible label. The citations array keeps the real ids. A non-draft mode never carries
    draft_language (defense-in-depth: only a draft turn ships clause text)."""
    citations = [c for c in reply.citations if c in valid_ids]
    draft = reply.draft_language
    if reply.mode != "draft":
        draft = None
    elif draft is not None:
        draft = scrub_leaked_ids(draft, id_labels)
    return reply.model_copy(
        update={
            "reply": scrub_leaked_ids(reply.reply, id_labels),
            "draft_language": draft,
            "citations": citations,
        }
    )


def from_qa(result: DonnaAskResponse) -> DonnaChatResponse:
    """Map the F10 read-and-explain answer into the F10b chat envelope. A deflected advice
    request becomes the softer acquire-context deflection (mode `need_context`); a grounded
    answer or honest miss stays read-and-explain (mode `explain`)."""
    if result.kind == "deflected":
        return DonnaChatResponse(
            reply=_ACQUIRE_CONTEXT, mode="need_context", citations=[], draft_language=None
        )
    return DonnaChatResponse(
        reply=result.answer, mode="explain", citations=result.citations, draft_language=None
    )


async def chat(
    contract_id: str, question: str, context: DonnaContext | None = None
) -> DonnaChatResponse:
    """The single entry. No anchor -> F10 read-and-explain (re-skinned deflection); a
    grounded anchor -> the advise/draft engine below."""
    if not has_context(context):
        # qa.ask owns retrieval + persistence + windowing; we only re-skin its envelope.
        # Pass the softer acquire-context text so a DEFLECTED turn is PERSISTED with the
        # same wording the API returns (from_qa) — a reloaded thread then matches the live
        # reply instead of showing qa's older "raise an issue / get a lawyer" prose.
        return from_qa(await qa.ask(contract_id, question, deflection_text=_ACQUIRE_CONTEXT))

    assert context is not None  # narrowed by has_context
    async with acquire() as conn:
        conversation = await get_or_create_conversation(conn, contract_id)
        prior_summary = conversation.running_summary
        messages = await fetch_messages(conn, conversation.id)
        nodes = await fetch_nodes(conn, contract_id)
        issues = await list_issues(conn, contract_id)
        # Resolve the anchor LIVE from the DB (never frozen into the window).
        active_issue = None
        if context.issue_id is not None:
            issue = await get_issue(conn, context.issue_id)
            if issue is not None and issue.contract_id == contract_id:
                active_issue = issue

    labels = build_label_map(nodes)
    valid_node_ids = {n.id for n in nodes}
    selected = [nid for nid in context.node_ids if nid in valid_node_ids]

    clause_blocks = [b for nid in selected if (b := build_clause_grounding(nodes, nid, labels))]
    turns = to_turns(messages)
    prompt = render(
        "donna_chat_advise_v1.txt",
        clauses="\n".join(clause_blocks) or "(no clause selected)",
        issue=(
            build_issue_focus(active_issue, labels)
            if active_issue is not None
            else "(no active issue)"
        ),
        ledger=build_issue_ledger(issues, labels) or "(no issues on record)",
        summary=prior_summary or "(none)",
        history=render_history(window(turns)) or "(no earlier conversation)",
        question=question,
    )

    settings = get_settings()
    result = await complete(
        tier="high",
        messages=[{"role": "user", "content": prompt}],
        caller="donna_chat_advise",
        max_tokens=settings.llm.chat_advise_max_tokens,
        temperature=settings.llm.chat_advise_temperature,
        json_response=True,
    )

    valid_ids = valid_node_ids | {i.id for i in issues}
    id_labels = {**labels, **{i.id: i.title for i in issues}}
    reply = finalize_reply(parse_reply(result.text), valid_ids, id_labels)

    async with acquire() as conn:
        await append_message(conn, conversation.id, "user", question)
        await append_message(
            conn,
            conversation.id,
            "assistant",
            reply.reply,
            kind=_MODE_TO_KIND[reply.mode],
            citations=reply.citations,
        )
        turns_after = [*turns, DonnaTurn(question=question, answer=reply.reply)]
        await qa._update_rolling_summary(conn, conversation.id, prior_summary, turns_after)

    return DonnaChatResponse(
        reply=reply.reply,
        mode=reply.mode,
        citations=reply.citations,
        draft_language=reply.draft_language,
    )
