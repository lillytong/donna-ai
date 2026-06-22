"""Deterministic content-role classification + boundary detection (DD-54).

Synthetic fixtures only — no docx, no DB, no LLM. The boundary is the
agreement-statement line; everything up to and including it is front-matter, the
operative tree begins after it. TOC lines are dropped; drafting notes are kept."""

from __future__ import annotations

from backend.models.contract_tree import ExtractedBlock
from backend.services.import_.classify import classify, find_boundary


def _p(order: int, text: str, *, num_id: int | None = None) -> ExtractedBlock:
    return ExtractedBlock(
        order=order,
        kind="paragraph",
        text=text,
        has_autonumber=num_id is not None,
        num_id=num_id,
    )


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
    # The deterministic pass no longer guesses a title (the whole-region AI pass
    # owns it, DD-54); the un-keyworded first block defaults to recital.
    assert roles[0] == "recital"
    assert roles[1] == "date"
    assert roles[2] == "parties"
    assert roles[3] == "recital"
    assert roles[4] == "agreement_statement"
    # Everything after the boundary is operative.
    assert roles[5] == "clause"
    assert roles[6] == "clause"


def test_deterministic_does_not_blindly_title_the_first_block() -> None:
    # A leading bracketed note must NOT become the title (the real-data bug). The
    # deterministic pass defers titling to the region pass; the note is a
    # drafting_note.
    blocks = [
        _p(0, "[CAM Notes: confirm stamping before execution]"),
        _p(1, "TECHNOLOGY LICENSING AGREEMENT"),
        _p(2, "AGREED AS FOLLOWS:"),
        _p(3, "1. Scope"),
    ]
    classified = classify(blocks)
    assert classified[0].role == "drafting_note"
    assert classified[1].role != "title"  # left for the region pass / F04
    assert classified[2].role == "agreement_statement"
    assert classified[3].role == "clause"


def test_signature_block_structural_and_appendix() -> None:
    blocks = [
        _p(0, "TLA"),
        _p(1, "WITNESSETH:"),
        _p(2, "1. Grant of Licence", num_id=7),
        _p(3, "IN WITNESS WHEREOF the parties have executed this Agreement."),
        _p(4, "FOR AND ON BEHALF OF ACME CORP"),
        _p(5, "SCHEDULE 1 — Territory"),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[1] == "agreement_statement"  # WITNESSETH is the boundary, not a recital
    assert roles[2] == "clause"
    assert roles[3] == "signature_block"  # trailing region + signature shape
    assert roles[4] == "signature_block"  # the run continues
    assert roles[5] == "appendix"


def test_operative_clause_with_executed_keyword_stays_clause() -> None:
    # The real-data false-positive: operative boilerplate that merely says
    # "executed"/"signed" must NOT be pushed out of the clause tree (DD-54).
    blocks = [
        _p(0, "OFFTAKE AGREEMENT"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. This Agreement may be executed in any number of counterparts.", num_id=7),
        _p(3, "2. Any amendment must be in writing and signed by both Parties.", num_id=7),
        _p(4, "3. Each Party represents it duly executed and delivered this Agreement.", num_id=7),
        _p(5, "IN WITNESS WHEREOF the Parties have signed this Agreement."),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[2] == "clause"
    assert roles[3] == "clause"
    assert roles[4] == "clause"
    assert roles[5] == "signature_block"  # only the genuine execution line


def test_plural_drafting_note_detected() -> None:
    # "\bNOTE\b" missed the plural; "[CAM Notes: …]" / "[Notes to Draft: …]" must
    # still be drafting notes.
    blocks = [
        _p(0, "AGREEMENT"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. Supply", num_id=7),
        _p(3, "[CAM Notes: align defined terms across the Transaction Documents]"),
        _p(4, "[Notes to Draft: confirm the volume with counsel before sending]"),
    ]
    classified = classify(blocks)
    assert classified[3].role == "drafting_note"
    assert classified[4].role == "drafting_note"


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


def test_schedule_heading_closes_operative_region() -> None:
    # The operator-found bug: schedule BODY paragraphs were staying `clause` and
    # getting numbered ("98.27" under the Schedules). The first schedule heading
    # CLOSES the operative region — everything after is back-matter, unnumbered.
    blocks = [
        _p(0, "OFFTAKE AGREEMENT"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. Supply", num_id=7),
        _p(3, "2. Term", num_id=7),
        _p(4, "SCHEDULE I"),
        _p(5, "1. Delivery point", num_id=9),  # numbered in the source, but in a schedule
        _p(6, "The seller shall deliver to the agreed point.", num_id=9),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[2] == "clause"
    assert roles[3] == "clause"
    assert roles[4] == "appendix"  # the heading
    assert roles[5] == "appendix"  # schedule body — NOT a clause, so never numbered
    assert roles[6] == "appendix"


def test_appendix_heading_distinguished_from_clause_opening_with_word() -> None:
    # A real operative clause that merely OPENS with "Annexure" must not be mistaken
    # for the schedule heading and close the region (the real TLA false positive).
    blocks = [
        _p(0, "LICENCE AGREEMENT"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. Grant", num_id=7),
        _p(3, "2. Annexure A may be updated from time-to-time by the Licensor.", num_id=7),
        _p(4, "3. Termination", num_id=7),
        _p(5, "ANNEXURE A"),
        _p(6, "Mixed-case body text of the annexure follows here.", num_id=9),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[3] == "clause"  # opens with "Annexure" but is a running sentence
    assert roles[4] == "clause"
    assert roles[5] == "appendix"  # the genuine all-caps heading closes the region
    assert roles[6] == "appendix"


def test_no_schedule_no_signature_operative_runs_to_end() -> None:
    # Edge: a contract that ends in a cost table — no schedule, no signature block.
    # No back boundary is forced; the operative region runs to the end.
    blocks = [
        _p(0, "SUPPLY AGREEMENT"),
        _p(1, "AGREED AS FOLLOWS:"),
        _p(2, "1. Price", num_id=7),
        _p(3, "2. The price per tonne is set out below.", num_id=7),
    ]
    roles = {i: c.role for i, c in classify(blocks).items()}
    assert roles[2] == "clause"
    assert roles[3] == "clause"


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
