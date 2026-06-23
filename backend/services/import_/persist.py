"""Map the parsed tree to persistable node rows (heading/body split, table_data).

Pure logic — no DB. The split rule: a heading-shaped block becomes the node's
`heading` (title), everything else is `body`; table nodes carry structured
`table_data`. `plain_text` is the derived projection used for AI context, search,
and diff (never the source of truth).
"""

from __future__ import annotations

from backend.models.contract_tree import NodeRow, ParsedTree
from backend.services.import_.tree_builder import _looks_like_heading


def _table_plain(rows: list[list[str]]) -> str:
    return " | ".join(cell for row in rows for cell in row if cell)


def tree_to_node_rows(tree: ParsedTree) -> list[NodeRow]:
    rows: list[NodeRow] = []
    for n in tree.nodes:
        if n.kind == "table":
            table = n.rows or []
            rows.append(
                NodeRow(
                    index=n.index,
                    parent_index=n.parent_index,
                    order_index=n.order_index,
                    content_type="table",
                    table_data=table,
                    plain_text=_table_plain(table),
                    uncertain=n.uncertain,
                    role=n.role,
                    has_placeholder=n.has_placeholder,
                )
            )
        else:
            # force_kind (DD-56) overrides the shape heuristic — an AI-categorized
            # back-matter heading/body lands in the right field regardless of wording.
            is_heading = (
                n.force_kind == "heading"
                if n.force_kind is not None
                else _looks_like_heading(n.text)
            )
            rows.append(
                NodeRow(
                    index=n.index,
                    parent_index=n.parent_index,
                    order_index=n.order_index,
                    content_type="prose",
                    heading=n.text if is_heading else None,
                    body=None if is_heading else n.text,
                    plain_text=n.text,
                    uncertain=n.uncertain,
                    role=n.role,
                    has_placeholder=n.has_placeholder,
                )
            )
    return rows
