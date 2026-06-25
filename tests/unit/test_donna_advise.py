"""Pure logic for Donna's context-aware chat (F10b): structured-reply parse + honest
fallback, the citation guard + id scrub + non-draft draft-stripping (finalize_reply),
the context-present predicate, the F10->F10b envelope mapping, and the mode->kind
persistence map. No LLM, no DB."""

from __future__ import annotations

from datetime import datetime

from backend.models.donna import DonnaAskResponse, DonnaChatReply, DonnaContext
from backend.models.recommendations import StoredRecommendation
from backend.services.donna.advise import (
    _ACQUIRE_CONTEXT,
    _MODE_TO_KIND,
    _compose_brainstorm_opening,
    finalize_reply,
    from_qa,
    has_context,
    parse_reply,
)


def _rec(**kw: object) -> StoredRecommendation:
    base: dict[str, object] = dict(
        id="r1",
        issue_id="i1",
        rationale="A twelve-month cap is favorable-but-fair.",
        draft_recommended_position="Hold the twelve-month cap.",
        draft_counter_language="Liability shall not exceed the fees paid.",
        citations=["n-liab"],
        model="claude-opus-4-8",
        generated_at=datetime(2026, 6, 25),
        confirmed=False,
    )
    base.update(kw)
    return StoredRecommendation(**base)

# --- parse_reply -----------------------------------------------------------


def test_parse_reply_reads_structured_fields() -> None:
    reply = parse_reply(
        '{"reply": "Counter at the 12-month cap.", "mode": "advise",'
        ' "citations": ["n-liab"], "draft_language": null}'
    )
    assert reply.mode == "advise"
    assert reply.citations == ["n-liab"]
    assert reply.draft_language is None


def test_parse_reply_reads_draft_language() -> None:
    reply = parse_reply(
        '{"reply": "Here is a tighter clause.", "mode": "draft",'
        ' "citations": ["n-conf"], "draft_language": "Each party shall keep..."}'
    )
    assert reply.mode == "draft"
    assert reply.draft_language is not None and reply.draft_language.startswith("Each party")


def test_parse_reply_tolerates_surrounding_prose() -> None:
    reply = parse_reply(
        'Sure:\n{"reply": "Get a lawyer.", "mode": "legal_referral", "citations": [],'
        ' "draft_language": null}\nthanks'
    )
    assert reply.mode == "legal_referral"


def test_parse_reply_unparseable_is_honest_miss() -> None:
    reply = parse_reply("sorry, no json here")
    assert reply.mode == "explain"
    assert reply.citations == []
    assert reply.draft_language is None
    assert reply.reply  # a non-empty honest message, never fabricated


# --- finalize_reply (citation guard + id scrub + draft stripping) ----------


def test_finalize_drops_hallucinated_citations() -> None:
    out = finalize_reply(
        DonnaChatReply(reply="ok", mode="advise", citations=["n-liab", "n-ghost"]),
        valid_ids={"n-liab"},
        id_labels={},
    )
    assert out.citations == ["n-liab"]


def test_finalize_scrubs_leaked_id_from_reply_and_draft() -> None:
    out = finalize_reply(
        DonnaChatReply(
            reply="See n-liab for the cap.",
            mode="draft",
            citations=["n-liab"],
            draft_language="Per n-liab, liability is capped.",
        ),
        valid_ids={"n-liab"},
        id_labels={"n-liab": "clause 6.1 (Limitation of Liability)"},
    )
    assert "n-liab" not in out.reply
    assert out.draft_language is not None and "n-liab" not in out.draft_language
    assert "clause 6.1 (Limitation of Liability)" in out.reply
    assert out.citations == ["n-liab"]  # the array keeps the real id


def test_finalize_strips_draft_language_on_non_draft_mode() -> None:
    # Defense-in-depth: only a draft turn ships clause text; an advise turn never does.
    out = finalize_reply(
        DonnaChatReply(
            reply="I recommend countering.",
            mode="advise",
            citations=[],
            draft_language="Each party shall...",
        ),
        valid_ids=set(),
        id_labels={},
    )
    assert out.draft_language is None


# --- has_context (the grounded-anchor predicate) ---------------------------


def test_has_context_false_for_none_and_empty() -> None:
    assert has_context(None) is False
    assert has_context(DonnaContext()) is False
    assert has_context(DonnaContext(node_ids=[], issue_id=None)) is False


def test_has_context_true_for_node_or_issue() -> None:
    assert has_context(DonnaContext(node_ids=["n1"])) is True
    assert has_context(DonnaContext(issue_id="i1")) is True


# --- from_qa (F10 read-and-explain -> F10b envelope) -----------------------


def test_from_qa_passes_through_grounded_answer() -> None:
    out = from_qa(
        DonnaAskResponse(
            answer="It's in 11.2.", citations=["n-liab"], deflected=False, kind="answer"
        )
    )
    assert out.mode == "explain"
    assert out.reply == "It's in 11.2."
    assert out.citations == ["n-liab"]


def test_from_qa_passes_through_honest_miss_as_explain() -> None:
    out = from_qa(
        DonnaAskResponse(
            answer="Nothing on that here.", citations=[], deflected=False, kind="not_found"
        )
    )
    assert out.mode == "explain"


def test_from_qa_reskins_deflection_to_need_context() -> None:
    # The old wall becomes the softer acquire-context deflection.
    out = from_qa(
        DonnaAskResponse(answer="Raise an issue / get a lawyer.", citations=["n-liab"],
                         deflected=True, kind="deflected")
    )
    assert out.mode == "need_context"
    assert out.reply == _ACQUIRE_CONTEXT
    assert out.citations == []  # the wall's citations are dropped with the wall


# --- mode -> persisted kind map (schema-pinned) ----------------------------


def test_mode_to_kind_maps_onto_the_three_schema_kinds() -> None:
    assert _MODE_TO_KIND["explain"] == "answer"
    assert _MODE_TO_KIND["advise"] == "answer"
    assert _MODE_TO_KIND["draft"] == "answer"
    assert _MODE_TO_KIND["legal_referral"] == "deflected"
    assert _MODE_TO_KIND["need_context"] == "deflected"
    assert set(_MODE_TO_KIND.values()) <= {"answer", "not_found", "deflected"}


# --- _compose_brainstorm_opening (server-composed primed turn) --------------


def test_compose_brainstorm_restates_all_three_parts() -> None:
    text = _compose_brainstorm_opening(3, _rec())
    assert "issue #3" in text
    assert "A twelve-month cap is favorable-but-fair." in text
    assert "**Recommended position:** Hold the twelve-month cap." in text
    assert "**Counter-language:**\nLiability shall not exceed the fees paid." in text


def test_compose_brainstorm_omits_empty_optional_fields() -> None:
    text = _compose_brainstorm_opening(
        1, _rec(draft_recommended_position=None, draft_counter_language="   ")
    )
    assert "Recommended position" not in text
    assert "Counter-language" not in text
    # The rationale still leads the restatement.
    assert "favorable-but-fair" in text


def test_compose_brainstorm_falls_back_when_no_ordinal() -> None:
    text = _compose_brainstorm_opening(None, _rec())
    assert "this issue" in text
    assert "issue #" not in text
