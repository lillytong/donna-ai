"""Derive positional clause numbers from tree position (F04, DD-02).

Clause numbers are a *projection* of structure, not stored source text: a node's
number is its decimal-outline path from the roots. Roots (parent_index None),
ordered by order_index, are "1", "2", …; their children "1.1", "1.2", …; a node
at depth N gets N dotted segments. Re-derived on every structural edit so a
promote/demote/move renumbers the subtree automatically.

This intentionally ignores the source's own letter/roman numbering (e.g. "(a)",
"(iv)") for v1 — decimal outline only. Letter/roman scheme preservation is a
later concern; positional decimals are unambiguous and sufficient for review.

Only `clause`-role nodes are numbered (DD-54): front-matter, signature_block,
appendix, and drafting_note are excluded from the clause tree and carry no
number. Non-clause siblings do not consume a position, so the operative tree
re-derives from the first real clause — fixing the spurious 1/2/3 the parser used
to stamp on the title page and recitals.
"""

from __future__ import annotations

from collections import defaultdict

from backend.models.contract_tree import ParsedTree, TreeNode


def derive_numbers(tree: ParsedTree) -> dict[int, str]:
    children: dict[int | None, list[TreeNode]] = defaultdict(list)
    for node in tree.nodes:
        children[node.parent_index].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda n: n.order_index)

    numbers: dict[int, str] = {}

    def assign(parent: int | None, prefix: str) -> None:
        position = 0
        for node in children.get(parent, []):
            if node.role == "clause":
                position += 1
                number = f"{prefix}.{position}" if prefix else str(position)
                numbers[node.index] = number
                assign(node.index, number)
            else:
                # Non-clause node: no number, no position consumed. Recurse with
                # the same prefix so any clause nested beneath still numbers.
                assign(node.index, prefix)

    assign(None, "")
    return numbers
