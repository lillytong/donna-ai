"""derive_numbers projects a decimal outline from tree position (DD-02)."""

from __future__ import annotations

from backend.models.contract_tree import ParsedTree, TreeNode
from backend.services.import_.numbering import derive_numbers


def _node(index: int, parent: int | None, depth: int, order: int) -> TreeNode:
    return TreeNode(index=index, parent_index=parent, depth=depth, order_index=order, kind="prose")


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
