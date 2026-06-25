"""Deterministic DB → .docx renderer (F14, DD-43).

Export is a pure function `render(skeleton) → .docx`: the live node tree plus the
contract's `style_config` in, a clean Word document out — no AI, no network
(DD-43). It is the inverse of the import spine and doubles as the import verifier:
re-extracting a rendered document must recover the original content unchanged
(§2.1). This renders the CURRENT CLEAN state only — tracked-change export (F15) is
a later slice.

Reuses the import spine's primitives rather than re-deriving them (DD-43, one
numbering): clause numbers come from `import_.numbering.derive_numbers` (DD-02,
position-derived, never stored), so import and export pivot on the same numbering.

Content-integrity boundaries this renderer must hold:
  - Clause numbers are emitted as Word auto-numbering (`w:numPr` + a generated
    numbering definition), so the number lives outside the run text and never
    enters the content stream — re-extraction recovers the heading text exactly.
  - `caps` (DD-37) is the Word all-caps *display* property (`w:caps`), not a
    `str.upper()` — the stored text stays original-case and round-trips intact
    while Word renders it uppercase.
"""

from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from backend.models.contract_tree import ParsedTree, TreeNode
from backend.models.imports import StoredNode
from backend.models.style import LevelStyle, StyleConfig
from backend.services.import_.numbering import derive_numbers

# High ids to avoid colliding with any abstractNum/num the default template ships.
_ABSTRACT_ID = 7777
_NUM_ID = 7777

# Numbering format per outline level for each scheme (DD-37). read_docx reads only
# ilvl + numId off w:numPr, so these affect Word's display, not the round-trip.
_MIXED_FMT = ("decimal", "decimal", "lowerLetter", "lowerRoman")


def _w(tag: str) -> str:
    return qn(f"w:{tag}")


def _group_children(nodes: list[StoredNode]) -> dict[str | None, list[StoredNode]]:
    """parent_id → siblings ordered by order_index. A parent_id absent from the
    node set (or null) is a root — mirrors ContractTreeResponse.from_rows."""
    present = {n.id for n in nodes}
    children: dict[str | None, list[StoredNode]] = {}
    for n in nodes:
        parent = n.parent_id if (n.parent_id is not None and n.parent_id in present) else None
        children.setdefault(parent, []).append(n)
    for siblings in children.values():
        siblings.sort(key=lambda n: n.order_index)
    return children


def _plan(nodes: list[StoredNode]) -> list[tuple[StoredNode, str | None]]:
    """Document-order (pre-order DFS) list of (node, derived_number). The number is
    None for non-clause nodes and clause nodes carry their DD-02 decimal-outline
    number, derived through the shared import numbering on an adapter tree."""
    children = _group_children(nodes)
    ordered: list[StoredNode] = []

    def dfs(parent: str | None) -> None:
        for node in children.get(parent, []):
            ordered.append(node)
            dfs(node.id)

    dfs(None)

    index_of = {node.id: i for i, node in enumerate(ordered)}
    tree_nodes: list[TreeNode] = []
    for i, node in enumerate(ordered):
        parent_id = node.parent_id if node.parent_id in index_of else None
        tree_nodes.append(
            TreeNode(
                index=i,
                parent_index=index_of[parent_id] if parent_id is not None else None,
                depth=0,
                order_index=node.order_index,
                kind="table" if node.content_type == "table" else "prose",
                text=(node.heading or node.body or ""),
                role=node.role,
            )
        )
    numbers = derive_numbers(ParsedTree(nodes=tree_nodes))
    return [(node, numbers.get(i)) for i, node in enumerate(ordered)]


def _lvl_text(ilvl: int, scheme: str) -> str:
    """Word lvlText: "%1.%2.…." — each ancestor level's counter, dot-joined."""
    return ".".join(f"%{i + 1}" for i in range(ilvl + 1)) + "."


def _num_fmt(ilvl: int, scheme: str) -> str:
    if scheme == "mixed":
        return _MIXED_FMT[ilvl] if ilvl < len(_MIXED_FMT) else "decimal"
    return "decimal"


def _inject_numbering(doc: Any, max_ilvl: int, scheme: str) -> None:
    """Add one multilevel abstractNum (levels 0..max_ilvl) and a num referencing it
    to the document's numbering part, so numbered paragraphs render real Word
    numbers — kept out of the run text (the content-integrity boundary)."""
    numbering = doc.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(_w("abstractNumId"), str(_ABSTRACT_ID))
    multi = OxmlElement("w:multiLevelType")
    multi.set(_w("val"), "multilevel")
    abstract.append(multi)
    for ilvl in range(max_ilvl + 1):
        lvl = OxmlElement("w:lvl")
        lvl.set(_w("ilvl"), str(ilvl))
        start = OxmlElement("w:start")
        start.set(_w("val"), "1")
        lvl.append(start)
        fmt = OxmlElement("w:numFmt")
        fmt.set(_w("val"), _num_fmt(ilvl, scheme))
        lvl.append(fmt)
        text = OxmlElement("w:lvlText")
        text.set(_w("val"), _lvl_text(ilvl, scheme))
        lvl.append(text)
        jc = OxmlElement("w:lvlJc")
        jc.set(_w("val"), "left")
        lvl.append(jc)
        abstract.append(lvl)
    numbering.insert(0, abstract)
    num = OxmlElement("w:num")
    num.set(_w("numId"), str(_NUM_ID))
    ref = OxmlElement("w:abstractNumId")
    ref.set(_w("val"), str(_ABSTRACT_ID))
    num.append(ref)
    numbering.append(num)


def _apply_numbering(paragraph: Any, ilvl: int) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    numPr = OxmlElement("w:numPr")
    ilvl_el = OxmlElement("w:ilvl")
    ilvl_el.set(_w("val"), str(ilvl))
    numPr.append(ilvl_el)
    num_id = OxmlElement("w:numId")
    num_id.set(_w("val"), str(_NUM_ID))
    numPr.append(num_id)
    pPr.append(numPr)


def _style_run(run: Any, level: LevelStyle, body_size_pt: int, font: str) -> None:
    run.font.name = font
    run.font.bold = level.bold
    run.font.underline = level.underline
    # DD-37: display-only uppercase. The run text stays original-case so the
    # round-trip recovers it; Word renders it uppercase.
    run.font.all_caps = level.caps
    run.font.size = Pt(level.font_size_pt if level.font_size_pt is not None else body_size_pt)


def _add_table(doc: Any, rows: list[list[str]]) -> None:
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=n_cols)
    table.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, cell_text in enumerate(row):
            table.rows[i].cells[j].text = cell_text


def _ilvl_of(number: str) -> int:
    """Outline depth a clause number implies: "1" → 0, "5.2" → 1, "5.2.1" → 2."""
    return number.count(".")


def render_contract_docx(nodes: list[StoredNode], style_config: dict[str, Any]) -> bytes:
    """Render the live node tree to a clean .docx (current state, no tracked
    changes). Pure CPU — the caller owns the async DB read and offloads this."""
    style = StyleConfig.from_config(style_config)
    plan = _plan(nodes)

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = style.font
    normal.font.size = Pt(style.body_font_size_pt)

    max_ilvl = max(
        (_ilvl_of(num) for node, num in plan if node.role == "clause" and num is not None),
        default=0,
    )
    _inject_numbering(doc, max_ilvl, style.numbering_scheme)

    for node, number in plan:
        if node.content_type == "table":
            _add_table(doc, node.table_data or [])
            continue
        text = node.heading if node.heading is not None else (node.body or "")
        if not text:
            continue
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(text)
        if node.role == "clause" and number is not None:
            ilvl = _ilvl_of(number)
            _apply_numbering(paragraph, ilvl)
            level = style.level(ilvl)
            if style.indent_per_level_pt:
                paragraph.paragraph_format.left_indent = Pt(style.indent_per_level_pt * ilvl)
        else:
            level = LevelStyle()
        _style_run(run, level, style.body_font_size_pt, style.font)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
