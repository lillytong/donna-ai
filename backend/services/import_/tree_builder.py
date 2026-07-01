"""Assemble flat extracted blocks into the clause hierarchy (F03, DD-36).

A best-effort first pass, by design: the spec never trusts the parse blindly —
the operator verifies and corrects structure in the import-review UI (F04), and
DD-36 auto-corrects only the clear cases. So this builder aims for a good tree
on the common shape and *flags* ambiguous nodes (`uncertain`) for review rather
than guessing silently.

Algorithm — dominant backbone (validated on real contracts). The depth signal is
Word auto-numbering (`w:abstractNumId` + `w:ilvl`), but `ilvl` is a depth only
*within* one numbering definition, and a document is one dominant body scheme plus
many small nested side-lists. Grouping is by **`abstractNumId`** (the list
*definition*), not `numId` (the *instance*): Word splits one multilevel outline
across many numIds sharing an abstractNumId, so numId-grouping shatters the real
backbone into fragments and lets a deep side-list win dominance (DD-36). So:
  - the **dominant scheme** (the `abstractNumId` with the most nodes) is the
    backbone, anchored at depth 0;
  - **other numbered schemes** (side-lists) open one level under the
    immediately-preceding node — numbered OR an unnumbered lead-in — so a list
    nests under its real parent (a definition's roman sub-list under that
    definition, a sub-clause's `(a)` list under that sub-clause); this keeps
    depth-first render order equal to document order (every node attaches on the
    current right spine);
  - **within each scheme**, an **outline stack** of `(ilvl, depth)` frames maps
    its `ilvl`s to depth: a same-`ilvl` item reuses that level's depth (sibling), a
    deeper `ilvl` opens a new level at the absolute one-depth-per-`ilvl` target —
    but **capped to the current right-spine tail** (`prev_depth + 1`). The cap makes
    a genuinely skipped level compress (`ilvl 1->3` with nothing between → sibling
    depth), while an intervening unnumbered leaf still becomes the real parent (a
    definition's `(a)` sub-list nests under the definition → `1.1.72(a)`, not beside
    it → `1.1(a)`). It also guarantees a parent exists, so no node is stranded as a
    parentless root. This keeps `(a)(b)(c)` siblings even when `(a)` carries an
    `(A)(B)` sub-list;
  - **unnumbered blocks** attach as leaves under the current backbone clause; the
    backbone advances on numbered **clauses only, never enumerated items**, so the
    next definition after an `(a)…(e)` list returns to `1.1.73` instead of being
    captured under `(e)`. Flagged `uncertain` when heading-shaped or with no
    backbone parent — the F04 worklist.
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


def _scheme_key(b: ExtractedBlock) -> int | None:
    """The numbering-definition key a block groups under: its `abstractNumId` when
    resolved, else its `numId` (synthetic blocks, or numbering.xml absent). Word
    splits one outline across numIds sharing an abstractNumId, so the abstract id
    is the correct backbone key; the numId fallback preserves behaviour where no
    abstract mapping exists."""
    return b.abstract_num_id if b.abstract_num_id is not None else b.num_id


def build_tree(doc: ParsedDocument) -> ParsedTree:
    numbered = [
        b for b in doc.blocks if b.kind == "paragraph" and b.has_autonumber and b.num_id is not None
    ]
    counts = Counter(_scheme_key(b) for b in numbered)
    dominant = counts.most_common(1)[0][0] if counts else None

    nodes: list[TreeNode] = []
    last_at_depth: dict[int, int] = {}  # depth -> index of most recent node there
    sib_count: dict[int | None, int] = {}
    backbone_index: int | None = None  # dominant-scheme open clause — leaf attachment
    backbone_depth = -1
    prev_depth = -1  # depth of the immediately-preceding added node (any kind)
    # scheme -> its current open list as a stack of (ilvl, depth) frames
    scheme_stack: dict[int | None, list[tuple[int, int]]] = {}

    def add(parent: int | None, depth: int, b: ExtractedBlock, numb: bool, unsure: bool) -> int:
        idx = len(nodes)
        slot = sib_count.get(parent, 0) + 1
        sib_count[parent] = slot
        if b.kind == "table":
            node_kind = "table"
        elif b.kind == "attachment":
            node_kind = "attachment"
        else:
            node_kind = "prose"
        nodes.append(
            TreeNode(
                index=idx,
                parent_index=parent,
                depth=depth,
                order_index=slot * _ORDER_GAP,
                kind=node_kind,
                text=b.text,
                rows=b.rows,
                numbered=numb,
                uncertain=unsure,
                is_bullet_list=b.is_bullet_list,
                enumerated=b.enumerated,
                enumerator_format=b.enumerator_format,
                image_data=b.image_data,
                image_mime=b.image_mime,
                image_cx_emu=b.image_cx_emu,
                image_cy_emu=b.image_cy_emu,
            )
        )
        return idx

    def anchor(depth: int, idx: int) -> None:
        last_at_depth[depth] = idx
        for d in [k for k in last_at_depth if k > depth]:
            del last_at_depth[d]

    def close_deeper(depth: int) -> None:
        # A node at `depth` ends every open list frame sitting strictly deeper, so the next
        # item of that scheme reopens a fresh run at the right place (and a list resumes as
        # siblings only while its own frame survives).
        for k in list(scheme_stack):
            st = scheme_stack[k]
            while st and st[-1][1] > depth:
                st.pop()
            if not st:
                del scheme_stack[k]

    for b in doc.blocks:
        leaf_depth = backbone_depth + 1 if backbone_index is not None else 0

        if b.kind in ("table", "attachment"):
            idx = add(backbone_index, leaf_depth, b, numb=False, unsure=False)
            anchor(leaf_depth, idx)
            close_deeper(leaf_depth)
            prev_depth = leaf_depth
            continue

        if b.has_autonumber and b.num_id is not None:
            key = _scheme_key(b)
            level = b.list_level or 0
            # Map this scheme's ilvls to tree depth via an outline stack of (ilvl, depth)
            # frames. Pop levels at or below `level`; a same-ilvl item REUSES that level's
            # depth (sibling), a deeper ilvl opens a new level. A new level takes the
            # absolute one-depth-per-ilvl target below its parent frame, CAPPED to the
            # current right-spine tail (prev_depth + 1): a genuinely skipped source level
            # compresses (ilvl 1->3 with nothing between -> sibling depth), while an
            # intervening unnumbered leaf (a definition before its (a) sub-list) still
            # becomes the real parent, so the (a) items nest under it (1.1.72(a)), not beside
            # it (1.1(a)). The cap also guarantees parent = last_at_depth[depth-1] exists, so
            # no node is ever stranded as a parentless root.
            stack = scheme_stack.setdefault(key, [])
            sib_depth: int | None = None
            while stack and stack[-1][0] >= level:
                popped = stack.pop()
                if popped[0] == level:
                    sib_depth = popped[1]
            if sib_depth is not None:
                depth = sib_depth
            elif stack:
                parent_ilvl, parent_depth = stack[-1]
                depth = min(parent_depth + (level - parent_ilvl), prev_depth + 1)
            elif key == dominant:
                depth = 0  # the backbone anchors at the root, independent of front matter
            else:
                depth = prev_depth + 1  # a side-list's first item opens under the preceding node
            depth = max(0, depth)
            stack.append((level, depth))
            parent = None if depth == 0 else last_at_depth.get(depth - 1)
            idx = add(parent, depth, b, numb=True, unsure=parent is None and depth > 0)
            anchor(depth, idx)
            close_deeper(depth)
            prev_depth = depth
            # The backbone (leaf-attachment anchor) follows the numbered-CLAUSE spine only;
            # an enumerated item is skipped in numbering and must NOT capture the unnumbered
            # nodes that follow it — the next definition returns to 1.1.73, not under (e).
            if key == dominant and not b.enumerated:
                backbone_index, backbone_depth = idx, depth
        else:
            unsure = _looks_like_heading(b.text) or backbone_index is None
            idx = add(backbone_index, leaf_depth, b, numb=False, unsure=unsure)
            anchor(leaf_depth, idx)
            close_deeper(leaf_depth)
            prev_depth = leaf_depth

    return ParsedTree(nodes=nodes)
