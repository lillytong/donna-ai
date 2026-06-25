"""Pure logic for Donna's per-change revision recommendation (F03c, DD-78): change-kind
derivation, the edit-focus grounding block, structured-output parse + honest fallback, and the
finalize invariant (counter-language exists iff verdict == counter; trivial never carries it).
No LLM, no live DB."""

from __future__ import annotations

from backend.models.revision_recommend import RevisionRecommendation
from backend.services.donna.revision_recommend import (
    build_change_focus,
    derive_kind,
    finalize_recommendation,
    parse_recommendation,
)

# --- derive_kind -----------------------------------------------------------


def test_derive_kind_covers_every_bucket() -> None:
    assert derive_kind("n1", 0.8, None) == "edited"
    assert derive_kind("n1", None, None) == "deleted"
    assert derive_kind(None, None, 3) == "new"
    assert derive_kind(None, 0.4, None) == "abstain"


# --- build_change_focus ----------------------------------------------------


def test_change_focus_new_uses_proposed_text() -> None:
    block = build_change_focus("new", "insertion", None, "A brand new indemnity clause.")
    assert "ADDED" in block
    assert "A brand new indemnity clause." in block


def test_change_focus_deleted_uses_original_text() -> None:
    block = build_change_focus("deleted", "deletion", "The old survival clause.", None)
    assert "DELETED" in block
    assert "The old survival clause." in block


def test_change_focus_edited_shows_both_sides() -> None:
    block = build_change_focus("edited", "replacement", "capped at fees paid", "uncapped")
    assert "capped at fees paid" in block
    assert "uncapped" in block
    assert "replacement" in block


# --- parse_recommendation --------------------------------------------------


def test_parse_reads_structured_fields() -> None:
    rec = parse_recommendation(
        '{"verdict": "counter", "significance": "substantive",'
        ' "reasoning": "Uncapped liability is deal-breaking.",'
        ' "counter_language": "Liability shall not exceed the fees paid."}'
    )
    assert rec.verdict == "counter"
    assert rec.significance == "substantive"
    assert rec.counter_language is not None and rec.counter_language.startswith("Liability")


def test_parse_tolerates_surrounding_prose() -> None:
    rec = parse_recommendation(
        'Sure:\n{"verdict": "accept", "significance": "trivial",'
        ' "reasoning": "Punctuation only.", "counter_language": null}\ndone'
    )
    assert rec.verdict == "accept"
    assert rec.significance == "trivial"


def test_parse_unparseable_is_conservative_fallback() -> None:
    rec = parse_recommendation("no json here")
    assert rec.verdict == "keep"  # safe hold, never auto-accept an unreadable change
    assert rec.significance == "substantive"
    assert rec.counter_language is None
    assert rec.reasoning  # honest, non-empty


# --- finalize_recommendation (invariant enforcement) -----------------------


def test_finalize_keeps_counter_language_only_for_counter() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Push back on scope.",
        counter_language="Scope is limited to the named affiliates.",
    )
    out = finalize_recommendation(rec, {})
    assert out.verdict == "counter"
    assert out.counter_language == "Scope is limited to the named affiliates."


def test_finalize_strips_counter_language_from_non_counter() -> None:
    rec = RevisionRecommendation(
        verdict="accept",
        significance="substantive",
        reasoning="Fair.",
        counter_language="leftover language the model should not have set",
    )
    out = finalize_recommendation(rec, {})
    assert out.counter_language is None


def test_finalize_trivial_never_carries_counter_language() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="trivial",
        reasoning="Formatting only.",
        counter_language="should be dropped",
    )
    out = finalize_recommendation(rec, {})
    assert out.significance == "trivial"
    assert out.counter_language is None
    assert out.verdict == "keep"  # a counter with no usable language collapses to the safe hold


def test_finalize_counter_without_language_collapses_to_keep() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Wanted to push back but gave no words.",
        counter_language="   ",
    )
    out = finalize_recommendation(rec, {})
    assert out.verdict == "keep"
    assert out.counter_language is None


def test_finalize_scrubs_leaked_id_from_prose() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="See n-liab for the cap.",
        counter_language="Per n-liab, liability is capped.",
    )
    out = finalize_recommendation(rec, {"n-liab": "clause 6.1 (Limitation of Liability)"})
    assert "n-liab" not in out.reasoning
    assert out.counter_language is not None and "n-liab" not in out.counter_language
    assert "clause 6.1 (Limitation of Liability)" in out.reasoning
