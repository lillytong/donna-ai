"""Tree -> node-row mapping: heading/body split, table_data, parent links."""

from __future__ import annotations

from backend.models.contract_tree import ParsedTree, TreeNode
from backend.services.import_.persist import tree_to_node_rows


def _node(
    index: int,
    parent: int | None,
    depth: int,
    *,
    kind: str = "prose",
    text: str = "",
    rows: list[list[str]] | None = None,
    force_kind: str | None = None,
) -> TreeNode:
    return TreeNode(
        index=index,
        parent_index=parent,
        depth=depth,
        order_index=(index + 1) * 100,
        kind=kind,
        text=text,
        rows=rows,
        force_kind=force_kind,  # type: ignore[arg-type]
    )


def test_heading_body_split_and_table_and_parents() -> None:
    tree = ParsedTree(
        nodes=[
            _node(0, None, 0, text="Confidentiality"),  # heading-shaped
            _node(1, 0, 1, text="Each party shall keep information secret."),  # body
            _node(2, 0, 1, kind="table", rows=[["Param", "Value"], ["Fee", "5%"]]),
        ]
    )
    rows = tree_to_node_rows(tree)

    head = rows[0]
    assert head.heading == "Confidentiality" and head.body is None

    body = rows[1]
    assert body.body == "Each party shall keep information secret." and body.heading is None
    assert body.parent_index == 0  # link preserved for the repository to resolve

    table = rows[2]
    assert table.content_type == "table"
    assert table.table_data == [["Param", "Value"], ["Fee", "5%"]]
    assert table.plain_text == "Param | Value | Fee | 5%"  # derived projection


def test_force_kind_overrides_shape_heuristic() -> None:
    """DD-56: the back-matter AI pass sets force_kind, which decides the heading/body
    split regardless of the text's shape — so a long heading-categorized line lands in
    `heading`, and a short body-categorized line lands in `body`."""
    tree = ParsedTree(
        nodes=[
            # Long, sentence-shaped text the heuristic would call body — forced heading.
            _node(
                0,
                None,
                0,
                text="The following variables are taken into account for the calculation.",
                force_kind="heading",
            ),
            # Short, heading-shaped text the heuristic would call heading — forced body.
            _node(1, 0, 1, text="Steam", force_kind="body"),
        ]
    )
    rows = tree_to_node_rows(tree)

    assert rows[0].heading is not None and rows[0].body is None  # forced heading
    assert rows[1].body == "Steam" and rows[1].heading is None  # forced body
