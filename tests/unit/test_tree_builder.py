"""Tree assembly nests by (num_id, ilvl), nests side-lists, and flags ambiguity."""

from __future__ import annotations

from backend.models.contract_tree import ExtractedBlock, ParsedDocument
from backend.services.import_.tree_builder import build_tree


def _doc(blocks: list[ExtractedBlock]) -> ParsedDocument:
    return ParsedDocument(blocks=blocks, extracted_chars=1, ceiling_chars=1)


def _p(
    order: int,
    text: str,
    *,
    num_id: int | None = None,
    ilvl: int | None = None,
    enumerated: bool = False,
    fmt: str | None = None,
) -> ExtractedBlock:
    return ExtractedBlock(
        order=order,
        kind="paragraph",
        text=text,
        has_autonumber=num_id is not None,
        num_id=num_id,
        list_level=ilvl,
        enumerated=enumerated,
        enumerator_format=fmt,
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


def test_side_list_nests_under_nondominant_side_clause() -> None:
    # Three schemes: 7 dominant backbone, 8 a side-clause (1.2.1/1.2.2),
    # 9 the (a)(b)(c) list that must nest under the open scheme-8 clause.
    tree = build_tree(
        _doc(
            [
                _p(0, "1 Defs", num_id=7, ilvl=0),
                _p(1, "1.1 A", num_id=7, ilvl=1),
                _p(2, "1.2 Interpretation", num_id=7, ilvl=1),
                _p(3, "In this Agreement...", num_id=8, ilvl=0),
                _p(4, "(a) first", num_id=9, ilvl=0),
                _p(5, "(b) second", num_id=9, ilvl=0),
                _p(6, "(c) third", num_id=9, ilvl=0),
                _p(7, "If any provision...", num_id=8, ilvl=0),
                _p(8, "2 Scope", num_id=7, ilvl=0),
                _p(9, "3 Term", num_id=7, ilvl=0),
            ]
        )
    )
    by_text = {x.text: x for x in tree.nodes}
    interp = by_text["1.2 Interpretation"]
    side1 = by_text["In this Agreement..."]
    side2 = by_text["If any provision..."]

    # 1.2.1 is a child of 1.2 at depth 2
    assert side1.parent_index == interp.index and side1.depth == 2

    # (a)(b)(c) are children of 1.2.1 (not of 1.2) at depth 3
    for label in ("(a) first", "(b) second", "(c) third"):
        item = by_text[label]
        assert item.parent_index == side1.index and item.depth == 3

    # 1.2.2 is again a child of 1.2 at depth 2; backbone resumes at root
    assert side2.parent_index == interp.index and side2.depth == 2
    assert by_text["2 Scope"].parent_index is None and by_text["2 Scope"].depth == 0


def test_side_list_opening_deep_nests_not_root() -> None:
    # Scheme 7 dominates (5 nodes). Scheme 9 is first used at ilvl 0 early (so its
    # document-wide minimum ilvl is 0), then a FRESH instance reopens at ilvl 1 under
    # a deep clause. The later instance must nest under its preceding clause, not jump
    # below the right spine and become a spurious root (the global-min vs instance-min bug).
    tree = build_tree(
        _doc(
            [
                _p(0, "1 Defs", num_id=7, ilvl=0),
                _p(1, "early (i)", num_id=9, ilvl=0),  # scheme 9 opens at global-min ilvl 0
                _p(2, "early (ii)", num_id=9, ilvl=0),
                _p(3, "2 Scope", num_id=7, ilvl=0),
                _p(4, "2.1 Sub", num_id=7, ilvl=1),
                _p(5, "2.1.1 Detail", num_id=7, ilvl=2),  # the clause the later list nests under
                _p(6, "later (i)", num_id=9, ilvl=1),  # fresh instance opens deeper than global min
                _p(7, "later (ii)", num_id=9, ilvl=1),
                _p(8, "3 Term", num_id=7, ilvl=0),
            ]
        )
    )
    by_text = {x.text: x for x in tree.nodes}
    detail = by_text["2.1.1 Detail"]
    later1 = by_text["later (i)"]
    later2 = by_text["later (ii)"]

    assert detail.depth == 2
    # First item of the later instance nests under its immediately-preceding clause,
    # NOT at root.
    assert later1.parent_index is not None
    assert later1.parent_index == detail.index
    assert later1.depth == detail.depth + 1
    # Second item stays a sibling under the same clause.
    assert later2.parent_index == detail.index and later2.depth == detail.depth + 1


def test_dominant_level_gap_compresses_not_parentless_root() -> None:
    # The dominant outline skips a level (ilvl 1 -> 3, common in real Word docs). The
    # outline stack compresses it: the ilvl-3 node attaches under the ilvl-1 node with a
    # consistent (depth, parent) — never stranded as a parentless node at depth>0 (which
    # renders flush-left + reorders the export).
    tree = build_tree(
        _doc(
            [
                _p(0, "1 Heading", num_id=7, ilvl=0),
                _p(1, "1.1 Sub", num_id=7, ilvl=1),
                _p(2, "gap item", num_id=7, ilvl=3),  # jumps to ilvl 3 (ilvl 2 skipped)
                _p(3, "2 Next", num_id=7, ilvl=0),
            ]
        )
    )
    by_text = {x.text: x for x in tree.nodes}
    sub = by_text["1.1 Sub"]
    gap = by_text["gap item"]
    assert gap.parent_index == sub.index
    assert gap.depth == sub.depth + 1
    # No node is left parentless at depth>0.
    assert all(not (n.parent_index is None and n.depth > 0) for n in tree.nodes)


def test_enumerated_list_resumes_as_siblings_after_a_sub_list() -> None:
    # The reported real-JVA bug: item (a) at ilvl 3 carries an (A)(B) sub-list at ilvl 4;
    # when the list returns to ilvl 3 it must resume as SIBLINGS (b)(c) of (a) at the same
    # depth — not restart under (a) as a second (a). One dominant scheme; ilvl drives depth.
    tree = build_tree(
        _doc(
            [
                _p(0, "14 Default", num_id=7, ilvl=0),
                _p(1, "14.1 lead-in", num_id=7, ilvl=1),
                _p(2, "a item", num_id=7, ilvl=3),  # (a)
                _p(3, "A sub", num_id=7, ilvl=4),  # (A) under (a)
                _p(4, "B sub", num_id=7, ilvl=4),  # (B) under (a) — sibling of (A)
                _p(5, "b item", num_id=7, ilvl=3),  # (b) — sibling of (a), NOT a child
                _p(6, "c item", num_id=7, ilvl=3),  # (c)
            ]
        )
    )
    by_text = {x.text: x for x in tree.nodes}
    a, b, c = by_text["a item"], by_text["b item"], by_text["c item"]
    sub_a, sub_b = by_text["A sub"], by_text["B sub"]
    # (a)(b)(c) are siblings: same parent, same depth.
    assert b.parent_index == a.parent_index and c.parent_index == a.parent_index
    assert b.depth == a.depth and c.depth == a.depth
    # (A)(B) nest under (a) as siblings of each other, one level deeper.
    assert sub_a.parent_index == a.index and sub_b.parent_index == a.index
    assert sub_a.depth == a.depth + 1 and sub_b.depth == a.depth + 1


def test_enumerated_sublist_nests_under_unnumbered_leaf_parent() -> None:
    # A definition (unnumbered leaf under "1.1 Definitions") carries an (a)(b) sub-list whose
    # items are the dominant scheme at a deeper ilvl. The (a)(b) must nest under THAT
    # definition (so their number is 1.1.<def>(a)), not beside it under 1.1; and the next
    # definition must return to the definition level, not get captured under (b).
    tree = build_tree(
        _doc(
            [
                _p(0, "1 Defs", num_id=7, ilvl=0),
                _p(1, "1.1 Definitions", num_id=7, ilvl=1),
                _p(2, "TermA means the first thing"),  # unnumbered definition leaf
                _p(3, "TermB means any of the following"),  # the definition with a sub-list
                _p(4, "a sub", num_id=7, ilvl=3, enumerated=True, fmt="lowerLetter"),
                _p(5, "b sub", num_id=7, ilvl=3, enumerated=True, fmt="lowerLetter"),
                _p(6, "TermC means the next thing"),  # must return to the definition level
            ]
        )
    )
    by_text = {x.text: x for x in tree.nodes}
    defs = by_text["1.1 Definitions"]
    term_a = by_text["TermA means the first thing"]
    term_b = by_text["TermB means any of the following"]
    term_c = by_text["TermC means the next thing"]
    a, b = by_text["a sub"], by_text["b sub"]
    # The definitions are leaves under "1.1 Definitions" at the same depth.
    assert term_a.parent_index == defs.index and term_b.parent_index == defs.index
    # (a)(b) nest UNDER their definition (TermB), one level deeper — not beside it under 1.1.
    assert a.parent_index == term_b.index and b.parent_index == term_b.index
    assert a.depth == term_b.depth + 1 and b.depth == term_b.depth + 1
    # The enumerated sub-list does NOT capture the next definition: TermC returns to the
    # definition level under "1.1 Definitions" (the backbone never advanced onto (a)/(b)).
    assert term_c.parent_index == defs.index and term_c.depth == term_b.depth


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
