"""Tree assembly nests by (num_id, ilvl), nests side-lists, and flags ambiguity."""

from __future__ import annotations

from backend.models.contract_tree import ExtractedBlock, ParsedDocument
from backend.services.import_.tree_builder import build_tree


def _doc(blocks: list[ExtractedBlock]) -> ParsedDocument:
    return ParsedDocument(blocks=blocks, extracted_chars=1, ceiling_chars=1)


def _p(
    order: int, text: str, *, num_id: int | None = None, ilvl: int | None = None
) -> ExtractedBlock:
    return ExtractedBlock(
        order=order,
        kind="paragraph",
        text=text,
        has_autonumber=num_id is not None,
        num_id=num_id,
        list_level=ilvl,
    )


def test_within_scheme_ilvl_drives_depth_and_parenting() -> None:
    # Scheme 7: 1 (top) -> 1.1, 1.2 (children) -> then 2 (back to top).
    tree = build_tree(
        _doc(
            [
                _p(0, "Confidentiality", num_id=7, ilvl=0),
                _p(1, "Definition", num_id=7, ilvl=1),
                _p(2, "Exceptions", num_id=7, ilvl=1),
                _p(3, "Term", num_id=7, ilvl=0),
            ]
        )
    )
    n = tree.nodes
    assert [x.depth for x in n] == [0, 1, 1, 0]
    assert n[1].parent_index == 0 and n[2].parent_index == 0  # children of "Confidentiality"
    assert n[3].parent_index is None  # sibling top-level
    # gap-based sibling ordering
    assert n[1].order_index == 100 and n[2].order_index == 200


def test_new_scheme_nests_as_side_list_under_open_clause() -> None:
    # Scheme 7 dominates (3 nodes); scheme 13 is a side-list under "Definitions".
    tree = build_tree(
        _doc(
            [
                _p(0, "1 Definitions", num_id=7, ilvl=0),
                _p(1, "Affiliate means ...", num_id=13, ilvl=0),  # different scheme -> child
                _p(2, "Control means ...", num_id=13, ilvl=0),
                _p(3, "2 Scope", num_id=7, ilvl=0),
                _p(4, "3 Term", num_id=7, ilvl=0),
            ]
        )
    )
    n = tree.nodes
    assert n[1].parent_index == 0 and n[1].depth == 1
    assert n[2].parent_index == 0  # both side-list items under "Definitions"
    assert n[3].parent_index is None and n[3].depth == 0  # backbone resumes at top level


def test_unnumbered_body_and_heading_flagging() -> None:
    tree = build_tree(
        _doc(
            [
                _p(0, "1. Scope", num_id=7, ilvl=0),
                _p(1, "The parties agree to the following terms and conditions herein."),  # body
                _p(2, "RECITALS"),  # heading-shaped -> uncertain branch
            ]
        )
    )
    n = tree.nodes
    body = n[1]
    assert body.parent_index == 0 and body.uncertain is False
    heading = n[2]
    assert heading.uncertain is True  # flagged for F04 review
