"""Pure logic for Donna's per-change revision recommendation (F03c, DD-78): change-kind
derivation, the edit-focus grounding block, structured-output parse + honest fallback, and the
finalize invariant (counter-language exists iff verdict == counter; trivial never carries it).
No LLM, no live DB."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from backend.models.imports import StoredNode
from backend.models.revision_recommend import (
    RevisionRecommendation,
    RevisionRecommendSummary,
    VerdictTally,
)
from backend.prompts.utils import render
from backend.services.donna import revision_recommend as rr
from backend.services.donna.grounding import build_clause_grounding, build_projected_label_map
from backend.services.donna.revision_recommend import (
    _cluster_key,
    build_change_focus,
    derive_kind,
    extract_clause_anchors,
    finalize_recommendation,
    parse_recommendation,
    recommend_on_import,
    reconstruct_proposed_clause,
    reduce_counter_span,
    referenceable_ids,
)


def _hunk(
    original: str | None, proposed: str | None, significance: str = "substantive"
) -> dict[str, Any]:
    return dict(
        id="h",
        significance=significance,
        original_text=original,
        proposed_text=proposed,
    )


# --- _cluster_key (cross-document clustering, DD-89) ------------------------


def test_cluster_key_unites_edits_differing_only_by_surrounding_punctuation() -> None:
    # The real bug: a defined-term rename appears bare in one clause and wrapped in a leading "("
    # in another (plus case/whitespace noise). Both must share a cluster key so they are judged
    # once, not contradictorily.
    bare = _cluster_key(_hunk("Buyer", "Purchaser"))
    wrapped = _cluster_key(_hunk("(Buyer", "  (purchaser)  "))
    assert bare is not None
    assert bare == wrapped


def test_cluster_key_does_not_merge_opposite_direction_figure_edits() -> None:
    # Same value appears on both sides but the edits move in OPPOSITE directions; they are NOT the
    # same change and must not be judged together. (Illustrative values, not real contract data.)
    up = _cluster_key(_hunk("5%", "10%"))
    down = _cluster_key(_hunk("10%", "5%"))
    assert up is not None and down is not None
    assert up != down


def test_cluster_key_trivial_hunk_is_not_clustered() -> None:
    assert _cluster_key(_hunk("Buyer", "Purchaser", significance="trivial")) is None


def test_cluster_key_whole_node_new_or_deleted_is_not_clustered() -> None:
    assert _cluster_key(_hunk(None, "a brand new clause")) is None  # whole-node add
    assert _cluster_key(_hunk("a deleted clause", None)) is None  # whole-node delete


def test_cluster_key_degenerate_after_strip_is_not_clustered() -> None:
    # Both sides collapse to empty once surrounding punctuation is stripped -> no usable key.
    assert _cluster_key(_hunk("(", ")")) is None


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
    out = finalize_recommendation(rec, {}, "Scope is limited to our subsidiaries.")
    assert out.verdict == "counter"
    assert out.counter_language == "Scope is limited to the named affiliates."


def test_finalize_strips_counter_language_from_non_counter() -> None:
    rec = RevisionRecommendation(
        verdict="accept",
        significance="substantive",
        reasoning="Fair.",
        counter_language="leftover language the model should not have set",
    )
    out = finalize_recommendation(rec, {}, "our original text")
    assert out.counter_language is None


def test_finalize_trivial_never_carries_counter_language() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="trivial",
        reasoning="Formatting only.",
        counter_language="should be dropped",
    )
    out = finalize_recommendation(rec, {}, "our original text")
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
    out = finalize_recommendation(rec, {}, "our original text")
    assert out.verdict == "keep"
    assert out.counter_language is None


def test_finalize_counter_restoring_original_span_collapses_to_keep() -> None:
    # A counter whose language (modulo whitespace/case) equals our original span IS a reject:
    # the guard forces verdict "keep" and clears counter-language so it applies as a no-op.
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Restore our wording.",
        counter_language="  Capped   AT FEES paid  ",
    )
    out = finalize_recommendation(rec, {}, "capped at fees paid")
    assert out.counter_language is None
    out2 = finalize_recommendation(rec, {}, "Capped at fees paid")  # exact-but-cased match too
    assert out2.verdict == "keep"
    assert out2.counter_language is None


def test_finalize_genuine_counter_differing_from_original_is_preserved() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Cap higher than original but lower than their proposal.",
        counter_language="capped at two times the fees paid",
    )
    out = finalize_recommendation(rec, {}, "capped at fees paid")
    assert out.verdict == "counter"
    assert out.counter_language == "capped at two times the fees paid"


# --- F35 / DD-92: inline clause-citation anchors ---------------------------


def test_extract_clause_anchors_dedupes_in_order() -> None:
    text = "Tension with [[clause:n-2]]; also [[clause:n-1]] then [[clause:n-2]] again."
    assert extract_clause_anchors(text) == ["n-2", "n-1"]


def test_extract_clause_anchors_none_when_absent() -> None:
    assert extract_clause_anchors("A plain rationale with no anchors.") == []


def test_referenceable_ids_are_grounding_brackets_gated_to_real_nodes() -> None:
    # The bracketed ids in a grounding block, intersected with the real node-id set — a stray
    # bracket in clause body text ([NOTE]) can never widen the referenceable set.
    grounding = "[n-1] clause 3 — pays [NOTE] quarterly\n[n-2] clause 4 — see schedule"
    assert referenceable_ids(grounding, {"n-1", "n-2", "n-9"}) == {"n-1", "n-2"}


def test_finalize_keeps_valid_anchor_verbatim_and_collects_citation() -> None:
    rec = RevisionRecommendation(
        verdict="keep",
        significance="substantive",
        reasoning="This widens the indemnity beyond [[clause:n-1]].",
        counter_language=None,
    )
    out = finalize_recommendation(rec, {"n-1": "clause 3 (Indemnity)"}, None, referenceable={"n-1"})
    # The valid anchor survives verbatim so the frontend can render it as a live-numbered link,
    # and its id is collected into the structured citations contract.
    assert "[[clause:n-1]]" in out.reasoning
    assert out.citations == ["n-1"]


def test_finalize_degrades_unreferenceable_anchor_to_label() -> None:
    rec = RevisionRecommendation(
        verdict="keep",
        significance="substantive",
        reasoning="Conflicts with [[clause:n-9]].",
        counter_language=None,
    )
    # n-9 is a known node but NOT in this change's referenceable set -> degrade to its label,
    # never a broken link, never a raw id, and not cited.
    out = finalize_recommendation(rec, {"n-9": "clause 7 (Term)"}, None, referenceable={"n-1"})
    assert "[[clause:" not in out.reasoning
    assert "clause 7 (Term)" in out.reasoning
    assert out.citations == []


def test_finalize_degrades_hallucinated_anchor_to_neutral_phrase() -> None:
    rec = RevisionRecommendation(
        verdict="keep",
        significance="substantive",
        reasoning="See [[clause:made-up-id]] for the carve-out.",
        counter_language=None,
    )
    out = finalize_recommendation(rec, {}, None, referenceable={"n-1"})
    assert "made-up-id" not in out.reasoning
    assert "the referenced clause" in out.reasoning
    assert out.citations == []


def test_finalize_scrubs_bare_leaked_id_but_preserves_valid_anchor() -> None:
    # A bare leaked id in the prose is still scrubbed to its label (defense in depth), while the
    # id INSIDE a kept anchor is masked from the scrub so the anchor is not broken.
    rec = RevisionRecommendation(
        verdict="keep",
        significance="substantive",
        reasoning="n-1 raises the cap; compare [[clause:n-1]].",
        counter_language=None,
    )
    out = finalize_recommendation(rec, {"n-1": "clause 3 (Cap)"}, None, referenceable={"n-1"})
    assert "[[clause:n-1]]" in out.reasoning  # anchor intact
    assert "clause 3 (Cap) raises the cap" in out.reasoning  # bare leak scrubbed to label
    assert out.citations == ["n-1"]


def test_finalize_without_referenceable_degrades_all_anchors() -> None:
    # The back-compat path: no referenceable set supplied -> every anchor degrades, none cited.
    rec = RevisionRecommendation(
        verdict="accept",
        significance="trivial",
        reasoning="Tidy in [[clause:n-1]].",
        counter_language=None,
    )
    out = finalize_recommendation(rec, {"n-1": "clause 3"}, None)
    assert "[[clause:" not in out.reasoning
    assert out.citations == []


def test_recommend_prompt_renders_referenceable_clause_with_projected_number() -> None:
    # End-to-end of the grounding contract: a referenceable clause is presented to Donna labelled
    # with its DD-88 PROJECTED number (here "4", not the baseline "1"), so when she anchors it via
    # [[clause:id]] the rendered live number matches the pane. The prompt also instructs anchor-only
    # references.
    nodes = [
        StoredNode(
            id="n-1", order_index=0, content_type="prose", heading="Indemnity", role="clause"
        )
    ]
    labels = build_projected_label_map(nodes, {"n-1": "4"})
    clause = build_clause_grounding(nodes, "n-1", labels)
    assert "[n-1] clause 4 (Indemnity)" in clause
    prompt = render(
        "revision_recommend_v2.txt",
        deal_context="Contract type: licence",
        clause=clause,
        change="Change type: an edit.\nOur original text:\nx\nTheir proposed text:\ny",
    )
    assert "[n-1] clause 4 (Indemnity)" in prompt  # referenceable clause carries the live number
    assert "[[clause:id]]" in prompt  # the anchor-only reference instruction is in the prompt


# --- deterministic counter-span reduction ----------------------------------

_BASELINE = (
    "if the underpayment exceeds 5% of Royalties paid for audited Relevant Financial Quarter."
)
_PROPOSED_CLAUSE = (
    "if the underpayment exceeds 10% of Royalties paid for audited Relevant Financial Quarter."
)
_ECHOED_COUNTER = (
    "if the underpayment exceeds 7.5% of Royalties paid for audited Relevant Financial Quarter."
)


def test_reconstruct_proposed_clause_replays_inline_hunk() -> None:
    # Replaying the 5% -> 10% inline hunk over the baseline recovers the proposed clause body.
    out = reconstruct_proposed_clause(_BASELINE, [(_BASELINE.index("5%"), "5%", "10%")])
    assert out == _PROPOSED_CLAUSE


def test_reduce_counter_isolates_changed_token_span() -> None:
    # The echoed sentence shares everything with the proposed clause except the changed core.
    assert reduce_counter_span(_ECHOED_COUNTER, _PROPOSED_CLAUSE, "10%") == "7.5%"


def test_reduce_counter_fallback_when_changed_region_misaligns() -> None:
    # Two differing regions (not the single hunk span) => ambiguous => no reduction.
    two_diffs = (
        "if the OVERPAYMENT exceeds 7.5% of Royalties paid for audited Relevant Financial Quarter."
    )
    assert reduce_counter_span(two_diffs, _PROPOSED_CLAUSE, "10%") is None


def test_finalize_reduces_echoed_counter_to_changed_span() -> None:
    # The verified real example: counter echoed the whole sentence; finalize reduces it to "7.5%"
    # and the verdict stays "counter".
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="10% is too high; meet at 7.5%.",
        counter_language=_ECHOED_COUNTER,
    )
    out = finalize_recommendation(
        rec, {}, "5%", proposed_text="10%", proposed_clause=_PROPOSED_CLAUSE
    )
    assert out.verdict == "counter"
    assert out.counter_language == "7.5%"


def test_finalize_reduced_counter_restoring_original_collapses_to_keep() -> None:
    # Donna echoed the sentence but restored our original 5% -> reduces to "5%", which equals the
    # original span, so the collapse-to-keep guard (run on the REDUCED counter) forces "keep".
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Hold at our original.",
        counter_language=_BASELINE,
    )
    out = finalize_recommendation(
        rec, {}, "5%", proposed_text="10%", proposed_clause=_PROPOSED_CLAUSE
    )
    assert out.verdict == "keep"
    assert out.counter_language is None


def test_finalize_whole_node_counter_not_reduced() -> None:
    # A whole-node new hunk (empty original_text, no proposed clause to align against) keeps its
    # whole-clause counter verbatim — reduction is inline-edit only.
    whole_clause = "Each party shall indemnify the other against all third-party claims."
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Narrow the indemnity.",
        counter_language=whole_clause,
    )
    out = finalize_recommendation(rec, {}, None, proposed_text=whole_clause, proposed_clause="")
    assert out.verdict == "counter"
    assert out.counter_language == whole_clause


def test_finalize_degenerate_counter_falls_back_unreduced() -> None:
    # A counter that does not cleanly align to this hunk's span is stored UNREDUCED (the safe
    # fallback) rather than guessed — verdict unchanged, no crash.
    misaligned = (
        "if the OVERPAYMENT exceeds 7.5% of Royalties paid for audited Relevant Financial Quarter."
    )
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="Push back.",
        counter_language=misaligned,
    )
    out = finalize_recommendation(
        rec, {}, "5%", proposed_text="10%", proposed_clause=_PROPOSED_CLAUSE
    )
    assert out.verdict == "counter"
    assert out.counter_language == misaligned


# --- recommend_on_import auto-run cost guard (F03c, DD-35) ------------------


def _patch_ceiling(monkeypatch: pytest.MonkeyPatch, ceiling: int) -> None:
    """Override only the auto-run ceiling knob in settings (read from settings per DD-35 —
    never a hardcoded 50). Mirrors test_revision_review's `setattr(svc, "get_settings", ...)`."""
    fake = SimpleNamespace(llm=SimpleNamespace(revision_recommend_auto_max_changes=ceiling))
    monkeypatch.setattr(rr, "get_settings", lambda: fake)


async def test_recommend_on_import_skips_oversized_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    # changes_count above the configured ceiling -> the cost guard SKIPS the recommender (no Opus
    # spend) and logs the skip; the recommend engine is never awaited.
    ceiling = 7
    _patch_ceiling(monkeypatch, ceiling)

    called = False

    async def _never(_session_id: str) -> RevisionRecommendSummary:
        nonlocal called
        called = True
        raise AssertionError("recommend_session must not run for an oversized diff")

    monkeypatch.setattr(rr, "recommend_session", _never)

    with structlog.testing.capture_logs() as logs:
        await recommend_on_import("s-oversized", ceiling + 1)

    assert called is False
    skips = [e for e in logs if e["event"] == "revision_recommend.auto_skip_oversized"]
    assert len(skips) == 1
    assert skips[0]["changes_count"] == ceiling + 1
    assert skips[0]["ceiling"] == ceiling
    assert skips[0]["session_id"] == "s-oversized"


async def test_recommend_on_import_runs_within_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    # Contrast: at/under the ceiling the recommender IS awaited (auto-run proceeds).
    ceiling = 7
    _patch_ceiling(monkeypatch, ceiling)

    seen: list[str] = []

    async def _run(session_id: str) -> RevisionRecommendSummary:
        seen.append(session_id)
        return RevisionRecommendSummary(
            session_id=session_id,
            changes_analyzed=1,
            hunks_analyzed=1,
            by_verdict=VerdictTally(accept=1, counter=0, keep=0),
        )

    monkeypatch.setattr(rr, "recommend_session", _run)

    await recommend_on_import("s-ok", ceiling)

    assert seen == ["s-ok"]


def test_finalize_scrubs_leaked_id_from_prose() -> None:
    rec = RevisionRecommendation(
        verdict="counter",
        significance="substantive",
        reasoning="See n-liab for the cap.",
        counter_language="Per n-liab, liability is capped.",
    )
    out = finalize_recommendation(
        rec, {"n-liab": "clause 6.1 (Limitation of Liability)"}, "original cap language"
    )
    assert "n-liab" not in out.reasoning
    assert out.counter_language is not None and "n-liab" not in out.counter_language
    assert "clause 6.1 (Limitation of Liability)" in out.reasoning
