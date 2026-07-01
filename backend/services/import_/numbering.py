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

Block enumerated items (DD-98 / F03f) are also excluded: an `(a)`/`(A)`/`(i)`
item that sits as its own block under a clause keeps its literal marker as native
text and is addressed as "1.2.1(b)", never decimal-renumbered to "1.2.1.2". The
skip keys on the marker that is already in the node text (`is_block_enumerator`)
plus the import-set `enumerated` flag (which also catches a Word alpha/roman
auto-numbered item whose marker is generated, not in text). Like a non-clause
node, an enumerated item consumes no sibling position, so a real sub-clause
beside it still numbers correctly.
"""

from __future__ import annotations

import re
from collections import defaultdict

from backend.models.contract_tree import ParsedTree, TreeNode

# A leading parenthesised enumerator marker — "(a)", "(A)", "(iv)", "(II)". The
# marker may be glued to the text ("(A)The …" in real recitals), so no trailing
# space is required.
_ENUM_MARKER = re.compile(r"^\s*\(([A-Za-z]{1,4})\)")
_ROMAN_LABELS = frozenset(
    [
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
)


def is_block_enumerator(text: str) -> bool:
    """True when `text` opens with a block-enumerated-item marker: a single alpha
    letter or a roman numeral in parentheses. Curated to standard legal
    enumerators (single letter a–z, or roman i–xx) so a parenthetical word
    ("(the) …", "(or) …") is never mistaken for an enumerator (DD-98)."""
    m = _ENUM_MARKER.match(text)
    if m is None:
        return False
    label = m.group(1).lower()
    return (len(label) == 1 and label.isalpha()) or label in _ROMAN_LABELS


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
            if node.role == "clause" and not (node.enumerated or is_block_enumerator(node.text)):
                position += 1
                number = f"{prefix}.{position}" if prefix else str(position)
                numbers[node.index] = number
                assign(node.index, number)
            else:
                # Non-clause or block-enumerated node: no number, no position
                # consumed. Recurse with the same prefix so any clause nested
                # beneath still numbers.
                assign(node.index, prefix)

    assign(None, "")
    return numbers


def _alpha(n: int) -> str:
    """1->a, 2->b, … 26->z, 27->aa (bijective base-26, lowercase)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


def _roman(n: int) -> str:
    """Standard lowercase roman numeral for n>=1."""
    table = [
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ]
    out = ""
    for value, sym in table:
        while n >= value:
            out += sym
            n -= value
    return out


def format_enumerator(ordinal: int, enumerator_format: str | None) -> str:
    """The parenthesised marker glyph for the `ordinal`-th (1-based) item of a list
    in `enumerator_format` — "(a)" / "(A)" / "(i)" / "(I)". Empty for an unknown
    format. The glyph is derived from position, so it auto-renumbers (DD-99)."""
    if enumerator_format == "lowerLetter":
        return f"({_alpha(ordinal)})"
    if enumerator_format == "upperLetter":
        return f"({_alpha(ordinal).upper()})"
    if enumerator_format == "lowerRoman":
        return f"({_roman(ordinal)})"
    if enumerator_format == "upperRoman":
        return f"({_roman(ordinal).upper()})"
    if enumerator_format == "decimal":
        return f"({ordinal})"
    return ""


def derive_enumerators(tree: ParsedTree) -> dict[int, str]:
    """Marker glyph (e.g. "(a)") for every AUTO-NUMBERED enumerated node (those with
    an `enumerator_format`), derived from list position so it auto-renumbers on
    delete/insert (DD-99/F03f). The ordinal counts CONSECUTIVE enumerated siblings of
    the same format under one parent, restarting at 1 when the run starts, a
    non-enumerated sibling breaks the run, or the format changes — this is the
    restart-per-list-run behaviour real Word lists need (one source numId is reused
    across many separate lists). Literal-marker items (no `enumerator_format`, marker
    frozen in body) are intentionally excluded."""
    children: dict[int | None, list[TreeNode]] = defaultdict(list)
    for node in tree.nodes:
        children[node.parent_index].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda n: n.order_index)

    markers: dict[int, str] = {}
    for siblings in children.values():
        count = 0
        prev_format: str | None = None
        for node in siblings:
            fmt = node.enumerator_format if node.enumerated else None
            if fmt is None:
                prev_format = None  # break the run
                continue
            count = count + 1 if fmt == prev_format else 1
            markers[node.index] = format_enumerator(count, fmt)
            prev_format = fmt
    return markers
