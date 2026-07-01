"""derive_numbers projects a decimal outline from tree position (DD-02)."""

from __future__ import annotations

import pytest
from backend.models.contract_tree import ParsedTree, TreeNode
from backend.services.import_.numbering import (
    derive_enumerators,
    derive_numbers,
    format_enumerator,
    is_block_enumerator,
)


def _auto(index: int, parent: int | None, order: int, fmt: str) -> TreeNode:
    """An auto-numbered enumerated child (marker derived from position, DD-99)."""
    return TreeNode(
        index=index,
        parent_index=parent,
        depth=1,
        order_index=order,
        kind="prose",
        text="item body",
        role="clause",
        enumerated=True,
        enumerator_format=fmt,
    )


def _node(index: int, parent: int | None, depth: int, order: int) -> TreeNode:
    return TreeNode(index=index, parent_index=parent, depth=depth, order_index=order, kind="prose")


def _enum(index: int, parent: int | None, depth: int, order: int, text: str) -> TreeNode:
    return TreeNode(
        index=index, parent_index=parent, depth=depth, order_index=order, kind="prose", text=text
    )


def test_roots_and_children_get_dotted_outline() -> None:
    # 1 -> 1.1, 1.2 ; 2 -> 2.1 ; 1.1 -> 1.1.1
    tree = ParsedTree(
        nodes=[
            _node(0, None, 0, 100),  # "1"
            _node(1, 0, 1, 100),  # "1.1"
            _node(2, 0, 1, 200),  # "1.2"
            _node(3, 1, 2, 100),  # "1.1.1"
            _node(4, None, 0, 200),  # "2"
            _node(5, 4, 1, 100),  # "2.1"
        ]
    )
    assert derive_numbers(tree) == {
        0: "1",
        1: "1.1",
        2: "1.2",
        3: "1.1.1",
        4: "2",
        5: "2.1",
    }


def test_numbering_follows_order_index_not_node_index() -> None:
    # Second-inserted root has a smaller order_index, so it numbers first.
    tree = ParsedTree(
        nodes=[
            _node(0, None, 0, 200),  # later in document order -> "2"
            _node(1, None, 0, 100),  # earlier -> "1"
        ]
    )
    numbers = derive_numbers(tree)
    assert numbers[1] == "1"
    assert numbers[0] == "2"


def test_depth_n_yields_n_segments() -> None:
    tree = ParsedTree(
        nodes=[
            _node(0, None, 0, 100),
            _node(1, 0, 1, 100),
            _node(2, 1, 2, 100),
            _node(3, 2, 3, 100),
        ]
    )
    numbers = derive_numbers(tree)
    assert numbers[3] == "1.1.1.1"
    assert all(len(numbers[n.index].split(".")) == n.depth + 1 for n in tree.nodes)


# --- Block enumerated items (DD-98 / F03f) -----------------------------------


@pytest.mark.parametrize(
    "markers",
    [
        ("(a) first", "(b) second", "(c) third"),  # lower alpha
        ("(A) first", "(B) second", "(C) third"),  # upper alpha
        ("(i) first", "(ii) second", "(iii) third"),  # roman
    ],
)
def test_block_enumerated_children_are_not_decimal_numbered(markers: tuple[str, ...]) -> None:
    # Lead-in clause 1.1 with three block enumerated children: the lead-in keeps
    # its decimal number, each item is its own node addressed as "1.1(b)" by its
    # retained marker — never "1.1.1/1.1.2/1.1.3".
    nodes = [
        _node(0, None, 0, 100),  # "1"
        _node(1, 0, 1, 100),  # "1.1" lead-in
    ]
    for i, text in enumerate(markers):
        nodes.append(_enum(2 + i, 1, 2, (i + 1) * 100, text))
    numbers = derive_numbers(ParsedTree(nodes=nodes))
    assert numbers[0] == "1"
    assert numbers[1] == "1.1"
    for i in range(len(markers)):
        assert (2 + i) not in numbers, f"enumerated item {markers[i]!r} got a decimal number"


def test_enumerated_items_consume_no_position_so_a_real_subclause_still_numbers() -> None:
    # Under 1.1: an enumerated "(a)" item, then a genuine numbered sub-clause. The
    # sub-clause must number "1.1.1" (the enumerated item takes no position).
    nodes = [
        _node(0, None, 0, 100),  # "1"
        _node(1, 0, 1, 100),  # "1.1"
        _enum(2, 1, 2, 100, "(a) an enumerated item"),  # no number
        _node(3, 1, 2, 200),  # real sub-clause -> "1.1.1"
    ]
    numbers = derive_numbers(ParsedTree(nodes=nodes))
    assert 2 not in numbers
    assert numbers[3] == "1.1.1"


def test_enumerated_flag_alone_also_skips_numbering() -> None:
    # The TreeNode.enumerated flag (set at parse time) skips numbering even if the
    # text predicate would not match — belt-and-suspenders with the text check.
    flagged = TreeNode(
        index=1,
        parent_index=0,
        depth=1,
        order_index=100,
        kind="prose",
        text="no marker here",
        enumerated=True,
    )
    tree = ParsedTree(nodes=[_node(0, None, 0, 100), flagged])
    numbers = derive_numbers(tree)
    assert numbers[0] == "1"
    assert 1 not in numbers


@pytest.mark.parametrize(
    "text", ["(a) x", "(b) y", "(z) z", "(A) X", "(i) x", "(ii) y", "(iv) z", "(I) X", "(IX) y"]
)
def test_is_block_enumerator_accepts_standard_markers(text: str) -> None:
    assert is_block_enumerator(text)


@pytest.mark.parametrize(
    "text",
    [
        "(the) clause",  # English word, not an enumerator
        "(and) more",
        "(or) else",
        "(Company) means ...",  # defined-term parenthetical
        "1.1 a decimal clause",
        "a) no opening paren",
        "plain body text",
        "",
    ],
)
def test_is_block_enumerator_rejects_non_markers(text: str) -> None:
    assert not is_block_enumerator(text)


# --- Block enumerated AUTO-NUMBERED items: derived markers + auto-renumber (DD-99) ---


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("lowerLetter", ["(a)", "(b)", "(c)"]),
        ("upperLetter", ["(A)", "(B)", "(C)"]),
        ("lowerRoman", ["(i)", "(ii)", "(iii)"]),
        ("upperRoman", ["(I)", "(II)", "(III)"]),
        ("decimal", ["(1)", "(2)", "(3)"]),
    ],
)
def test_derive_enumerators_markers_per_format(fmt: str, expected: list[str]) -> None:
    nodes = [_node(0, None, 0, 100)]  # lead-in clause "1"
    nodes += [_auto(1 + i, 0, (i + 1) * 100, fmt) for i in range(3)]
    markers = derive_enumerators(ParsedTree(nodes=nodes))
    assert [markers[1 + i] for i in range(3)] == expected
    # And they are NOT decimal-numbered.
    nums = derive_numbers(ParsedTree(nodes=nodes))
    assert all((1 + i) not in nums for i in range(3))


def test_delete_first_item_renumbers_rest() -> None:
    # ACCEPTANCE CORE: (a)X (b)Y (c)Z, delete (a)X -> (a)Y (b)Z (auto-renumber).
    nodes = [_node(0, None, 0, 100)]
    nodes += [
        _auto(1, 0, 100, "lowerLetter"),
        _auto(2, 0, 200, "lowerLetter"),
        _auto(3, 0, 300, "lowerLetter"),
    ]
    before = derive_enumerators(ParsedTree(nodes=nodes))
    assert [before[1], before[2], before[3]] == ["(a)", "(b)", "(c)"]
    # Delete the first item; re-derive over the survivors.
    survivors = [nodes[0], nodes[2], nodes[3]]
    after = derive_enumerators(ParsedTree(nodes=survivors))
    assert after[2] == "(a)" and after[3] == "(b)"


def test_insert_mid_list_renumbers() -> None:
    # (a)X (b)Z, insert a new item between them -> (a)X (b)NEW (c)Z.
    nodes = [
        _node(0, None, 0, 100),
        _auto(1, 0, 100, "lowerLetter"),
        _auto(2, 0, 300, "lowerLetter"),
    ]
    nodes.append(_auto(3, 0, 200, "lowerLetter"))  # inserted between (order 200)
    markers = derive_enumerators(ParsedTree(nodes=nodes))
    assert markers[1] == "(a)" and markers[3] == "(b)" and markers[2] == "(c)"


def test_restart_per_run_not_per_numid() -> None:
    # Two separate lists under one parent, split by a non-enumerated clause between
    # them, must EACH restart at (a) — the 36-lists-share-one-numId real case.
    nodes = [
        _node(0, None, 0, 100),
        _auto(1, 0, 100, "lowerLetter"),
        _auto(2, 0, 200, "lowerLetter"),
        _node(3, 0, 300, 0),  # non-enumerated sibling breaks the run
        _auto(4, 0, 400, "lowerLetter"),
        _auto(5, 0, 500, "lowerLetter"),
    ]
    nodes[3] = TreeNode(
        index=3, parent_index=0, depth=1, order_index=300, kind="prose", text="plain"
    )
    markers = derive_enumerators(ParsedTree(nodes=nodes))
    assert [markers[1], markers[2]] == ["(a)", "(b)"]
    assert [markers[4], markers[5]] == ["(a)", "(b)"]  # second run restarts


def test_format_change_restarts_run() -> None:
    # (a)(b) then immediately (i)(ii): the format change starts a new run at 1.
    nodes = [
        _node(0, None, 0, 100),
        _auto(1, 0, 100, "lowerLetter"),
        _auto(2, 0, 200, "lowerLetter"),
        _auto(3, 0, 300, "lowerRoman"),
        _auto(4, 0, 400, "lowerRoman"),
    ]
    markers = derive_enumerators(ParsedTree(nodes=nodes))
    assert [markers[1], markers[2]] == ["(a)", "(b)"]
    assert [markers[3], markers[4]] == ["(i)", "(ii)"]


def test_literal_marker_items_excluded_from_derived_enumerators() -> None:
    # A literal-marker item (no enumerator_format, marker frozen in body) keeps its
    # marker and is NOT given a derived/auto-renumber marker.
    lit = TreeNode(
        index=1,
        parent_index=0,
        depth=1,
        order_index=100,
        kind="prose",
        text="(a) frozen literal",
        role="clause",
        enumerated=True,
        enumerator_format=None,
    )
    markers = derive_enumerators(ParsedTree(nodes=[_node(0, None, 0, 100), lit]))
    assert 1 not in markers


@pytest.mark.parametrize(
    "n,fmt,expected",
    [
        (1, "lowerLetter", "(a)"),
        (26, "lowerLetter", "(z)"),
        (27, "lowerLetter", "(aa)"),
        (4, "lowerRoman", "(iv)"),
        (9, "upperRoman", "(IX)"),
        (2, "upperLetter", "(B)"),
        (1, "decimal", "(1)"),
        (3, "decimal", "(3)"),
    ],
)
def test_format_enumerator_glyphs(n: int, fmt: str, expected: str) -> None:
    assert format_enumerator(n, fmt) == expected
