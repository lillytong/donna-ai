"""Pure logic for Donna-assisted clause drafting (F08d): the tolerant structured-output
parse + honest empty-body fallback, and the citation guard + id scrub (finalize_draft).
No LLM, no DB — these mirror the F11 recommendation unit tests."""

from __future__ import annotations

from backend.models.clause_draft import ClauseDraft
from backend.services.donna.drafting import finalize_draft, parse_draft

# --- parse_draft -----------------------------------------------------------


def test_parse_draft_reads_structured_fields() -> None:
    draft = parse_draft(
        '{"heading": "Confidentiality", "body": "Each party shall keep the other party\'s'
        ' Confidential Information secret.", "citations": ["n-conf"]}'
    )
    assert draft.heading == "Confidentiality"
    assert draft.body.startswith("Each party shall keep")
    assert draft.citations == ["n-conf"]


def test_parse_draft_tolerates_surrounding_prose() -> None:
    draft = parse_draft(
        'Here is the clause:\n{"heading": null, "body": "The Supplier shall deliver the'
        ' Goods within the Lead Time.", "citations": []}\nLet me know if that works.'
    )
    assert draft.heading is None
    assert draft.body.startswith("The Supplier shall deliver")
    assert draft.citations == []


def test_parse_draft_unparseable_is_honest_empty_body_fallback() -> None:
    draft = parse_draft("sorry, I cannot draft that")
    assert draft.heading is None
    assert draft.body == ""  # empty body = the honest "couldn't draft" signal
    assert draft.citations == []


# --- finalize_draft (citation guard + id scrub) ----------------------------


def test_finalize_drops_hallucinated_citations() -> None:
    draft = ClauseDraft(heading="Term", body="ok", citations=["n-term", "n-ghost"])
    out = finalize_draft(draft, valid_ids={"n-term"}, id_labels={})
    assert out.citations == ["n-term"]  # only the real node id survives


def test_finalize_scrubs_leaked_id_from_heading_and_body() -> None:
    draft = ClauseDraft(
        heading="See n-liab for the cap",
        body="As set out in n-liab, liability is capped.",
        citations=["n-liab"],
    )
    out = finalize_draft(
        draft,
        valid_ids={"n-liab"},
        id_labels={"n-liab": "clause 6.1 (Limitation of Liability)"},
    )
    assert out.heading is not None and "n-liab" not in out.heading
    assert "clause 6.1 (Limitation of Liability)" in out.heading
    assert "n-liab" not in out.body
    assert "clause 6.1 (Limitation of Liability)" in out.body
    assert out.citations == ["n-liab"]  # the array keeps the real id


def test_finalize_leaves_null_heading_null() -> None:
    out = finalize_draft(
        ClauseDraft(heading=None, body="A plain operative clause.", citations=[]),
        valid_ids=set(),
        id_labels={},
    )
    assert out.heading is None
