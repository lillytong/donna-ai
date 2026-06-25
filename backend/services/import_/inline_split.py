"""Split inline enumerator runs into ordered child nodes (F03e, SPEC §6).

A single prose paragraph carrying an inline enumerator run — a lead-in followed
by `(a) … (b) …` or `(i) … (ii) …` as continuous text — is split into the
lead-in (the parent's body) plus one ordered child per marker. The child retains
its own `(a)`/`(i)` marker as native text (NOT a derived number): Donna does not
reliably regenerate alpha/roman enumerators, so keeping the literal marker is what
protects the §2.1 byte-identical round-trip (decimal addressing is still derived
positionally, like any other clause node).

Permanent / v1 carve-outs:
  - defined-term DEFINITIONS (`"Term" means (i) … (ii) …`) are never split — the
    parts belong to the definition, the unit the operator negotiates and F16
    registers (SPEC §6);
  - flat-only — only the top-level run under the lead-in is split; a nested run
    (`(a) … (i) … (b)`) keeps the inner markers inside the child body;
  - a paragraph with no lead-in text, or fewer than two ordered markers, is left
    intact (idempotent: a child `(a) …` has no lead-in, so it never re-splits).

Round-trip safety is structural, not statistical: markers are only split at a
whitespace boundary on already-normalised text, so reassembly (lead-in + children
joined by single spaces) reproduces the source paragraph exactly. A mis-detected
marker can therefore only ever produce an extra, operator-correctable node — never
lost or corrupted content. The reassembly oracle in tests/system asserts this.

The transform runs AFTER role stamping and preserves every spine invariant: node
`index` stays a topological id (a child's index is always greater than its lead-in
parent's), `order_index` is gap-based, and children inherit the lead-in's role.
"""

from __future__ import annotations

import re

from backend.models.contract_tree import ParsedTree, TreeNode

_ORDER_GAP = 100  # gap-based order_index, mirrors tree_builder (OQ-07)

# A parenthesised lowercase token, e.g. "(a)", "(iv)". Boundary (whitespace before)
# is enforced separately so "subsection(a)" / "clause 3(b)" are never split points.
_MARKER = re.compile(r"\(([a-z]+)\)")

# Defined-term definition signals (mirror F16 services/defined_terms.py) — the
# permanent no-split carve-out. A lead-in shaped like a definition keeps its inline
# parts verbatim. Checked against the lead-in only (text before the first marker),
# so a stray "means" deep in body prose does not suppress a real split.
_DEF_MEANS = re.compile(
    r'["“][^"”\n]{1,80}["”]\s*\)?\s*(?:shall\s+mean|means)\b',
    re.IGNORECASE,
)
_DEF_PAREN_INTRO = re.compile(
    r'\(\s*(?:[a-z][a-z, ]*\s+)?["“][^"”\n]{1,80}["”]\s*\)',
)

_ALPHA = "abcdefghijklmnopqrstuvwxyz"
_ROMAN = [
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
]

# A candidate marker: (char offset of "(", label) at a whitespace boundary.
_Marker = tuple[int, str]


def _candidate_markers(text: str) -> list[_Marker]:
    out: list[_Marker] = []
    for m in _MARKER.finditer(text):
        start = m.start()
        if start > 0 and not text[start - 1].isspace():
            continue  # not an enumerator boundary, e.g. "clause 3(b)"
        out.append((start, m.group(1)))
    return out


def _ordered_run(candidates: list[_Marker]) -> list[_Marker]:
    """The flat top-level run: starting at `(a)` or `(i)`, the markers that
    continue the expected sequence in order. Non-matching markers (a nested run,
    a stray cross-ref) are skipped, not terminating — flat-only keeps the inner
    markers inside the child body."""
    if not candidates:
        return []
    first = candidates[0][1]
    if first == "a":
        expected = list(_ALPHA)
    elif first == "i":
        expected = _ROMAN
    else:
        return []  # a real run starts at the first marker of its scheme
    used: list[_Marker] = []
    pos = 0
    for marker in candidates:
        if pos < len(expected) and marker[1] == expected[pos]:
            used.append(marker)
            pos += 1
    return used


def _is_definition(lead_in: str) -> bool:
    return bool(_DEF_MEANS.search(lead_in) or _DEF_PAREN_INTRO.search(lead_in))


def _split_text(text: str) -> tuple[str, list[str]] | None:
    """`(lead_in, [child, …])` for a splittable inline run, else None. Children
    retain their `(a)`/`(i)` marker; `" ".join([lead_in, *children])` reproduces
    `text` (normalised)."""
    run = _ordered_run(_candidate_markers(text))
    if len(run) < 2:
        return None
    first_pos = run[0][0]
    lead_in = text[:first_pos].rstrip()
    if not lead_in:
        return None  # no lead-in to carry as the parent body
    if _is_definition(lead_in):
        return None  # defined-term carve-out (permanent)
    children: list[str] = []
    for i, (start, _label) in enumerate(run):
        end = run[i + 1][0] if i + 1 < len(run) else len(text)
        children.append(text[start:end].strip())
    return lead_in, children


def split_inline_enumerators(tree: ParsedTree) -> ParsedTree:
    """Rewrite the tree, splitting every eligible leaf prose node into its lead-in
    plus ordered children. Only leaves are split — a node that already has children
    is a structural heading, not an inline-run lead-in, and splitting it would risk
    sibling-order collisions. Indices are reassigned contiguously with children
    placed immediately after their lead-in, preserving the parents-before-children
    topological order the persist layer requires."""
    has_children = {n.parent_index for n in tree.nodes if n.parent_index is not None}

    emitted: list[TreeNode] = []
    old_to_new: dict[int, int] = {}

    for node in tree.nodes:
        split = (
            _split_text(node.text)
            if node.kind == "prose" and node.index not in has_children
            else None
        )
        new_index = len(emitted)
        old_to_new[node.index] = new_index
        # A parent always has a lower OLD index (parents precede children in the
        # source tree) and is therefore already resolved — no second pass needed.
        new_parent = old_to_new[node.parent_index] if node.parent_index is not None else None

        if split is None:
            emitted.append(node.model_copy(update={"index": new_index, "parent_index": new_parent}))
            continue

        lead_in, child_texts = split
        emitted.append(
            node.model_copy(
                update={"index": new_index, "parent_index": new_parent, "text": lead_in}
            )
        )
        for i, child_text in enumerate(child_texts):
            emitted.append(
                TreeNode(
                    index=len(emitted),
                    parent_index=new_index,
                    depth=node.depth + 1,
                    order_index=(i + 1) * _ORDER_GAP,
                    kind="prose",
                    text=child_text,
                    numbered=False,
                    uncertain=False,
                    role=node.role,
                    has_placeholder=node.has_placeholder,
                )
            )

    return ParsedTree(nodes=emitted)
