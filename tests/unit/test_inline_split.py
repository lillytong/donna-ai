"""Inline-enumerator split (F03e, SPEC §6): acceptance, carve-outs, invariants."""

from __future__ import annotations

from backend.models.contract_tree import ParsedTree, TreeNode
from backend.services.import_.inline_split import (
    _split_text,
    split_inline_enumerators,
)


def _leaf(index: int, text: str, *, parent: int | None = None, depth: int = 0) -> TreeNode:
    return TreeNode(
        index=index,
        parent_index=parent,
        depth=depth,
        order_index=(index + 1) * 100,
        kind="prose",
        text=text,
        role="clause",
    )


def _reassemble(text: str) -> str | None:
    res = _split_text(text)
    if res is None:
        return None
    lead_in, children = res
    return " ".join([lead_in, *children])


# --- acceptance: lead-in: (a) X (b) Y -> 1 parent + 2 children ---------------


def test_acceptance_one_parent_two_children() -> None:
    text = "The following shall apply: (a) both parties act; (b) no party objects"
    tree = split_inline_enumerators(ParsedTree(nodes=[_leaf(0, text)]))
    assert len(tree.nodes) == 3
    parent, c1, c2 = tree.nodes
    assert parent.text == "The following shall apply:"
    assert parent.parent_index is None
    assert c1.parent_index == 0 and c2.parent_index == 0
    assert c1.text == "(a) both parties act;"
    assert c2.text == "(b) no party objects"
    assert c1.order_index < c2.order_index  # child order preserved
    assert c1.depth == 1 and c2.depth == 1
    assert c1.role == "clause" and c2.role == "clause"


def test_split_text_lead_in_and_children() -> None:
    assert _split_text("Lead in: (a) X (b) Y") == ("Lead in:", ["(a) X", "(b) Y"])


def test_roman_run_splits() -> None:
    assert _split_text("Provided that: (i) X; (ii) Y; (iii) Z") == (
        "Provided that:",
        ["(i) X;", "(ii) Y;", "(iii) Z"],
    )


# --- round-trip: reassembly is byte-identical to the (normalised) source -----


def test_reassembly_is_byte_identical() -> None:
    for text in [
        "The following shall apply: (a) both parties act; (b) no party objects",
        "Provided that: (i) X; (ii) Y; (iii) Z",
        "Lead in with punctuation — yes: (a) one, (b) two, and (c) three.",
    ]:
        assert _reassemble(text) == text


def test_glued_marker_not_split_so_no_corruption() -> None:
    # A marker not preceded by whitespace is not an enumerator boundary; leaving it
    # in-body is what keeps reassembly exact.
    assert _split_text("see subsection(a) and clause 3(b) herein") is None


# --- carve-out: defined-term definitions never split -------------------------


def test_defined_term_means_is_not_split() -> None:
    assert _split_text('"Affiliate" means (i) a parent; (ii) a subsidiary') is None


def test_defined_term_canonical_intro_is_not_split() -> None:
    assert _split_text('(the "Group") comprising (a) X and (b) Y') is None


def test_non_definition_lead_in_still_splits() -> None:
    # "means" only suppresses when it shapes the lead-in as a definition.
    assert _split_text("Each party shall: (a) act; (b) refrain") is not None


# --- flat-only: nested inner run stays in the child body ---------------------


def test_nested_run_is_not_recursed() -> None:
    res = _split_text("Obligations: (a) do X including (i) foo and (ii) bar; (b) do Y")
    assert res is not None
    lead_in, children = res
    assert lead_in == "Obligations:"
    assert children == ["(a) do X including (i) foo and (ii) bar;", "(b) do Y"]
    assert _reassemble("Obligations: (a) do X including (i) foo and (ii) bar; (b) do Y") == (
        "Obligations: (a) do X including (i) foo and (ii) bar; (b) do Y"
    )


# --- guards: no lead-in, single marker, idempotency, non-leaf ---------------


def test_no_lead_in_is_not_split() -> None:
    assert _split_text("(a) first only (b) second") is None  # nothing before (a)


def test_single_marker_is_not_split() -> None:
    assert _split_text("Lead in: (a) the only item") is None


def test_run_must_start_at_first_scheme_marker() -> None:
    assert _split_text("Lead in: (b) X (c) Y") is None  # does not start at (a)


def test_idempotent() -> None:
    text = "The following: (a) one; (b) two"
    once = split_inline_enumerators(ParsedTree(nodes=[_leaf(0, text)]))
    twice = split_inline_enumerators(once)
    assert [n.text for n in once.nodes] == [n.text for n in twice.nodes]
    assert len(twice.nodes) == 3


def test_non_leaf_node_is_not_split() -> None:
    # A node that already has children is a heading, not an inline-run lead-in.
    parent = _leaf(0, "Heading: (a) X (b) Y")
    child = _leaf(1, "1.1 a real sub-clause", parent=0, depth=1)
    tree = split_inline_enumerators(ParsedTree(nodes=[parent, child]))
    assert len(tree.nodes) == 2  # unchanged


# --- tree invariants: indices topological, parents preserved, content kept ---


def _content(tree: ParsedTree) -> str:
    return " ".join(n.text for n in tree.nodes if n.kind == "prose")


def test_indices_topological_and_parents_remapped() -> None:
    nodes = [
        _leaf(0, "Section one heading"),
        _leaf(1, "Body of one: (a) alpha; (b) beta", parent=0, depth=1),
        _leaf(2, "Section two: (i) uno; (ii) dos"),
    ]
    # node 0 has a child (node 1) -> node 0 is a non-leaf, not split; node 1 and 2 are leaves.
    tree = split_inline_enumerators(ParsedTree(nodes=nodes))
    for i, n in enumerate(tree.nodes):
        assert n.index == i  # contiguous in document order
        if n.parent_index is not None:
            assert n.parent_index < n.index  # parents precede children
    # content stream is preserved exactly (lead-in + children rejoin to the source)
    assert _content(tree) == _content(ParsedTree(nodes=nodes))
