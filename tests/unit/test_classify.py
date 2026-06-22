"""Deterministic content-role classification + boundary detection (DD-54).

Synthetic fixtures only — no docx, no DB, no LLM. The boundary is the
agreement-statement line; everything up to and including it is front-matter, the
operative tree begins after it. TOC lines are dropped; drafting notes are kept."""

from __future__ import annotations

from backend.models.contract_tree import ExtractedBlock
from backend.services.import_.classify import classify, find_boundary


def _p(order: int, text: str) -> ExtractedBlock:
    return ExtractedBlock(order=order, kind="paragraph", text=text)


def _front_matter() -> list[ExtractedBlock]:
    return [
        _p(0, "JOINT VENTURE AGREEMENT"),
        _p(1, "DATED 8 January 2026"),
        _p(2, "BETWEEN Acme Corp and Beta Ltd"),
        _p(3, "WHEREAS the parties wish to collaborate;"),
        _p(4, "NOW, THEREFORE IT IS AGREED AS FOLLOWS:"),
        _p(5, "1. Definitions"),
        _p(6, "In this agreement the following terms apply."),
    ]


def test_find_boundary_is_the_agreement_statement_line() -> None:
    assert find_boundary(_front_matter()) == 4


def test_find_boundary_none_when_absent() -> None:
    assert find_boundary([_p(0, "Title"), _p(1, "Some prose")]) is None


def test_frontmatter_roles_split_at_boundary() -> None:
    roles = {i: c.role for i, c in classify(_front_matter()).items()}
    assert roles[0] == "title"
    assert roles[1] == "date"
    assert roles[2] == "parties"
    assert roles[3] == "recital"
    assert roles[4] == "agreement_statement"
    # Everything after the boundary is operative.
    assert roles[5] == "clause"
    assert roles[6] == "clause"


def test_first_block_is_title_even_if_keywords_match() -> None:
    # Title page text could contain a year; the first front-matter block still wins.
    blocks = [
        _p(0, "AGREEMENT DATED 2026"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. Scope"),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[0] == "title"
    assert roles[1] == "agreement_statement"
    assert roles[2] == "clause"


def test_signature_block_and_appendix_detected_in_operative_region() -> None:
    blocks = [
        _p(0, "TLA"),
        _p(1, "WITNESSETH:"),
        _p(2, "1. Grant of Licence"),
        _p(3, "IN WITNESS WHEREOF the parties have executed this Agreement."),
        _p(4, "SCHEDULE 1 — Territory"),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[1] == "agreement_statement"  # WITNESSETH is the boundary, not a recital
    assert roles[2] == "clause"
    assert roles[3] == "signature_block"
    assert roles[4] == "appendix"


def test_drafting_note_detected_anywhere_and_kept() -> None:
    blocks = [
        _p(0, "OFFTAKE AGREEMENT"),
        _p(1, "IT IS HEREBY AGREED:"),
        _p(2, "1. Supply"),
        _p(3, "[NOTE: confirm the volume with counsel before sending]"),
    ]
    classified = classify(blocks)
    assert classified[3].role == "drafting_note"
    assert classified[3].is_toc is False  # kept, never dropped


def test_toc_lines_flagged_for_drop() -> None:
    blocks = [
        _p(0, "AGREEMENT"),
        _p(1, "TABLE OF CONTENTS"),
        _p(2, "1. Definitions .......... 3"),
        _p(3, "2. Term .......... 5"),
        _p(4, "AGREED AS FOLLOWS:"),
        _p(5, "1. Definitions"),
    ]
    classified = classify(blocks)
    assert classified[1].is_toc is True  # the header
    assert classified[2].is_toc is True
    assert classified[3].is_toc is True
    assert classified[5].is_toc is False  # the real clause is not dropped


def test_placeholder_flag_independent_of_role() -> None:
    blocks = [
        _p(0, "AGREEMENT"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. The price shall be [insert amount] per tonne."),
        _p(3, "2. Delivery on ___."),
    ]
    classified = classify(blocks)
    assert classified[2].has_placeholder is True
    assert classified[3].has_placeholder is True
    assert classified[2].role == "clause"


def test_no_boundary_falls_back_to_operative_and_flags() -> None:
    # No agreement statement -> do NOT mis-file as front-matter; treat as clause,
    # flag uncertain for F04.
    blocks = [_p(0, "Some clause text."), _p(1, "More clause text.")]
    classified = classify(blocks)
    assert all(c.role == "clause" for c in classified.values())
    assert all(c.uncertain for c in classified.values())


def test_unplaceable_frontmatter_gets_neutral_default_and_uncertain() -> None:
    blocks = [
        _p(0, "AGREEMENT"),
        _p(1, "This introductory passage defies every keyword rule the classifier knows about"),
        _p(2, "AGREED AS FOLLOWS:"),
        _p(3, "1. Scope"),
    ]
    classified = classify(blocks)
    assert classified[1].role == "recital"  # neutral front-matter prose bucket
    assert classified[1].uncertain is True  # flagged for operator confirmation
