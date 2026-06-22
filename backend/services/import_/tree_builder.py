"""Assemble flat extracted blocks into the clause hierarchy (F03, DD-36).

A best-effort first pass, by design: the spec never trusts the parse blindly —
the operator verifies and corrects structure in the import-review UI (F04), and
DD-36 auto-corrects only the clear cases. So this builder aims for a good tree
on the common shape and *flags* ambiguous nodes (`uncertain`) for review rather
than guessing silently.

Algorithm — dominant backbone (validated on real contracts). The depth signal is
Word auto-numbering (`w:numId` + `w:ilvl`), but `ilvl` is a depth only *within*
one `num_id`, and a document is one dominant body scheme plus many small nested
side-lists. So:
  - the **dominant scheme** (the `num_id` with the most nodes) is the backbone;
    its depth is its `ilvl` normalised to the scheme's minimum level;
  - **other numbered schemes** (side-lists) hang one level under the current
    open backbone clause, their own `ilvl` adding relative depth;
  - **unnumbered blocks** attach as leaves under the current backbone clause,
    flagged `uncertain` when heading-shaped or with no backbone parent — the F04
    worklist.
This keeps depth bounded by real nesting, not by how much prose a clause carries.
"""

from __future__ import annotations

from collections import Counter

from backend.models.contract_tree import ExtractedBlock, ParsedDocument, ParsedTree, TreeNode

_ORDER_GAP = 100  # gap-based order_index leaves room to insert between siblings (OQ-07)
_HEADING_MAX_LEN = 80


def _looks_like_heading(text: str) -> bool:
    # Formatting (bold/caps) is dropped at extraction, so a heading is inferred
    # from shape: short and not ending like a sentence/clause body.
    return len(text) <= _HEADING_MAX_LEN and not text.rstrip().endswith((".", ";", ":", ","))


def build_tree(doc: ParsedDocument) -> ParsedTree:
    numbered = [
        b for b in doc.blocks if b.kind == "paragraph" and b.has_autonumber and b.num_id is not None
    ]
    counts = Counter(b.num_id for b in numbered)
    dominant = counts.most_common(1)[0][0] if counts else None
    scheme_min: dict[int | None, int] = {}
    for b in numbered:
        lvl = b.list_level or 0
        scheme_min[b.num_id] = min(scheme_min.get(b.num_id, lvl), lvl)
    dom_min = scheme_min.get(dominant, 0)

    nodes: list[TreeNode] = []
    last_at_depth: dict[int, int] = {}  # depth -> index of most recent node there
    sib_count: dict[int | None, int] = {}
    backbone_index: int | None = None
    backbone_depth = -1

    def add(parent: int | None, depth: int, b: ExtractedBlock, numb: bool, unsure: bool) -> int:
        idx = len(nodes)
        slot = sib_count.get(parent, 0) + 1
        sib_count[parent] = slot
        nodes.append(
            TreeNode(
                index=idx,
                parent_index=parent,
                depth=depth,
                order_index=slot * _ORDER_GAP,
                kind="table" if b.kind == "table" else "prose",
                text=b.text,
                rows=b.rows,
                numbered=numb,
                uncertain=unsure,
            )
        )
        return idx

    def anchor(depth: int, idx: int) -> None:
        last_at_depth[depth] = idx
        for d in [k for k in last_at_depth if k > depth]:
            del last_at_depth[d]

    for b in doc.blocks:
        leaf_depth = backbone_depth + 1 if backbone_index is not None else 0

        if b.kind == "table":
            add(backbone_index, leaf_depth, b, numb=False, unsure=False)
            continue

        if b.has_autonumber and b.num_id is not None:
            if b.num_id == dominant:
                depth = max(0, (b.list_level or 0) - dom_min)
            else:
                base = backbone_depth + 1 if backbone_index is not None else 0
                depth = base + ((b.list_level or 0) - scheme_min[b.num_id])
            parent = None if depth == 0 else last_at_depth.get(depth - 1)
            idx = add(parent, depth, b, numb=True, unsure=parent is None and depth > 0)
            anchor(depth, idx)
            if b.num_id == dominant:
                backbone_index, backbone_depth = idx, depth
        else:
            unsure = _looks_like_heading(b.text) or backbone_index is None
            add(backbone_index, leaf_depth, b, numb=False, unsure=unsure)

    return ParsedTree(nodes=nodes)
