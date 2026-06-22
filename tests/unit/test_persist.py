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
) -> TreeNode:
    return TreeNode(
        index=index,
        parent_index=parent,
        depth=depth,
        order_index=(index + 1) * 100,
        kind=kind,
        text=text,
        rows=rows,
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
