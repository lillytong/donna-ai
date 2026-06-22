"""Faithful .docx content extraction for the import spine (Phase 0).

Promotes the validated spike-#1 extraction (accept-all-changes text, w:numPr
numbering) and closes its one defect: block-level content controls (`w:sdt`)
whose paragraphs `python-docx`'s `doc.paragraphs` silently skips, losing
fill-in field text (party names, dates, placeholders) — DD-45.

Extraction is accept-all-changes: insertions (`w:ins`) kept, deletions
(`w:del`/`w:delText`) dropped. Formatting is intentionally not read here; it is
rebuilt from style_config on export (DD-43). Content only.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn

from backend.models.contract_tree import ExtractedBlock, ParsedDocument

W_P = qn("w:p")
W_TBL = qn("w:tbl")
W_SDT = qn("w:sdt")
W_SDTCONTENT = qn("w:sdtContent")
W_T = qn("w:t")
W_INS = qn("w:ins")
W_DEL = qn("w:del")
W_PPR = qn("w:pPr")
W_NUMPR = qn("w:numPr")
W_ILVL = qn("w:ilvl")
W_NUMID = qn("w:numId")
W_VAL = qn("w:val")
W_TR = qn("w:tr")
W_TC = qn("w:tc")
W_PSTYLE = qn("w:pStyle")
W_OUTLINELVL = qn("w:outlineLvl")
W_STYLE = qn("w:style")
W_TYPE = qn("w:type")
W_STYLEID = qn("w:styleId")
W_BASEDON = qn("w:basedOn")
W_NUM = qn("w:num")
W_ABSTRACTNUM = qn("w:abstractNum")
W_ABSTRACTNUMID = qn("w:abstractNumId")
W_STYLELINK = qn("w:styleLink")
W_NUMSTYLELINK = qn("w:numStyleLink")

# Word's outlineLvl=9 is the "body text" sentinel, not a heading level (0-8).
_BODY_OUTLINE_LEVEL = 9


class _Numbering:
    """Resolved numbering context for one document: style-inherited (ilvl, numId,
    outlineLvl) per paragraph styleId (basedOn chains followed), and numId ->
    canonical abstractNumId (numStyleLink/styleLink indirection collapsed). Built
    once per import from styles.xml + numbering.xml; empty maps when those parts
    are absent (synthetic docs), in which case only direct numPr is read."""

    def __init__(self, style_index: dict[str, _StyleNum], abstract_map: dict[int, int]) -> None:
        self._style_index = style_index
        self._abstract_map = abstract_map

    def style(self, style_id: str | None) -> _StyleNum:
        if style_id is None:
            return (None, None, None)
        return self._style_index.get(style_id, (None, None, None))

    def abstract_of(self, num_id: int | None) -> int | None:
        if num_id is None:
            return None
        return self._abstract_map.get(num_id)


# (ilvl, numId, outlineLvl) carried (directly or by inheritance) by a paragraph style.
_StyleNum = tuple[int | None, int | None, int | None]


def _int_val(parent: Any, tag: str) -> int | None:
    el = parent.find(tag)
    if el is None:
        return None
    raw = el.get(W_VAL)
    return int(raw) if raw is not None else None


def _numpr_levels(numpr: Any) -> tuple[int | None, int | None]:
    """(ilvl, numId) read off a w:numPr element."""
    return _int_val(numpr, W_ILVL), _int_val(numpr, W_NUMID)


def _build_style_index(styles_root: Any) -> dict[str, _StyleNum]:
    """styleId -> resolved (ilvl, numId, outlineLvl), nearest definition winning
    over a basedOn ancestor. Only paragraph styles participate."""
    # styleId -> (based_on, own ilvl, own numId, own outlineLvl) before inheritance.
    raw: dict[str, tuple[str | None, int | None, int | None, int | None]] = {}
    for st in styles_root.findall(W_STYLE):
        if st.get(W_TYPE) != "paragraph":
            continue
        sid = st.get(W_STYLEID)
        if sid is None:
            continue
        pPr = st.find(W_PPR)
        ilvl = numid = outline = None
        if pPr is not None:
            numpr = pPr.find(W_NUMPR)
            if numpr is not None:
                ilvl, numid = _numpr_levels(numpr)
            outline = _int_val(pPr, W_OUTLINELVL)
        based_on = st.find(W_BASEDON)
        raw[sid] = (based_on.get(W_VAL) if based_on is not None else None, ilvl, numid, outline)

    resolved: dict[str, _StyleNum] = {}

    def resolve(sid: str | None, stack: frozenset[str]) -> _StyleNum:
        if sid is None or sid not in raw or sid in stack:
            return (None, None, None)
        if sid in resolved:
            return resolved[sid]
        based_on, ilvl, numid, outline = raw[sid]
        p_ilvl, p_numid, p_outline = resolve(based_on, stack | {sid})
        out: _StyleNum = (
            ilvl if ilvl is not None else p_ilvl,
            numid if numid is not None else p_numid,
            outline if outline is not None else p_outline,
        )
        resolved[sid] = out
        return out

    for sid in raw:
        resolve(sid, frozenset())
    return resolved


def _build_abstract_map(numbering_root: Any) -> dict[int, int]:
    """numId -> canonical abstractNumId. A numId references an abstractNumId; an
    abstractNum that is a numStyleLink shell (it delegates to a paragraph style)
    is collapsed onto the abstractNum that *defines* that style (styleLink), so
    two numIds backing one outline group together (DD-36)."""
    num_to_abs: dict[int, int] = {}
    for num in numbering_root.findall(W_NUM):
        nid = num.get(W_NUMID)
        abs_el = num.find(W_ABSTRACTNUMID)
        if nid is not None and abs_el is not None and abs_el.get(W_VAL) is not None:
            num_to_abs[int(nid)] = int(abs_el.get(W_VAL))

    style_to_abs: dict[str, int] = {}
    num_style_link: dict[int, str] = {}
    for an in numbering_root.findall(W_ABSTRACTNUM):
        aid_raw = an.get(W_ABSTRACTNUMID)
        if aid_raw is None:
            continue
        aid = int(aid_raw)
        sl = an.find(W_STYLELINK)
        if sl is not None and sl.get(W_VAL) is not None:
            style_to_abs[sl.get(W_VAL)] = aid
        nsl = an.find(W_NUMSTYLELINK)
        if nsl is not None and nsl.get(W_VAL) is not None:
            num_style_link[aid] = nsl.get(W_VAL)

    def canon(aid: int) -> int:
        seen: set[int] = set()
        while aid in num_style_link and aid not in seen:
            seen.add(aid)
            target = style_to_abs.get(num_style_link[aid])
            if target is None or target == aid:
                break
            aid = target
        return aid

    return {nid: canon(aid) for nid, aid in num_to_abs.items()}


# Typed clause-number prefixes the parser must recognise: "3.", "3.1", "(a)", "(iv)".
_LITERAL_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*\.?|\([a-z]+\)|\([ivx]+\))\s+\S")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _accept_all_text(element: Any) -> str:
    """Concatenated <w:t> text under `element`, excluding any inside a <w:del>
    subtree. Captures inline content-control (<w:sdt>) runs automatically, since
    they are descendant <w:t> of the paragraph."""
    parts: list[str] = []
    for node in element.iter(W_T):
        if not node.text:
            continue
        anc = node.getparent()
        inside_del = False
        while anc is not None and anc is not element:
            if anc.tag == W_DEL:
                inside_del = True
                break
            anc = anc.getparent()
        if not inside_del:
            parts.append(node.text)
    return "".join(parts)


def _paragraph_numbering(
    p: Any, numbering: _Numbering
) -> tuple[bool, int | None, int | None, int | None, int | None]:
    """(has_autonumber, list_level, num_id, abstract_num_id, outline_level).

    Numbering is read directly off w:p/w:pPr/w:numPr where present, else inherited
    from the paragraph style (w:pStyle -> styles.xml, basedOn chains followed) — the
    section-heading numbering that direct-only reads missed (DD-36). A paragraph
    counts as auto-numbered when it carries a numPr directly or resolves to a numId
    via its style; outlineLvl is metadata that travels with style-numbered headings."""
    pPr = p.find(W_PPR)
    if pPr is None:
        return False, None, None, None, None

    direct_numpr = pPr.find(W_NUMPR)
    direct_ilvl, direct_numid = (
        _numpr_levels(direct_numpr)
        if direct_numpr is not None
        else (
            None,
            None,
        )
    )

    pStyle = pPr.find(W_PSTYLE)
    style_ilvl, style_numid, style_outline = numbering.style(
        pStyle.get(W_VAL) if pStyle is not None else None
    )

    eff_ilvl = direct_ilvl if direct_ilvl is not None else style_ilvl
    eff_numid = direct_numid if direct_numid is not None else style_numid
    direct_outline = _int_val(pPr, W_OUTLINELVL)
    eff_outline = direct_outline if direct_outline is not None else style_outline
    if eff_outline == _BODY_OUTLINE_LEVEL:
        eff_outline = None

    has_auto = direct_numpr is not None or eff_numid is not None
    abstract = numbering.abstract_of(eff_numid)
    return has_auto, eff_ilvl, eff_numid, abstract, eff_outline


def _table_rows(tbl: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in tbl.findall(W_TR):
        rows.append([_norm(_accept_all_text(tc)) for tc in tr.findall(W_TC)])
    return rows


def _walk(container: Any, blocks: list[ExtractedBlock], in_cc: bool, numbering: _Numbering) -> None:
    """Append blocks for the direct w:p / w:tbl / w:sdt children of `container`,
    in document order, descending into block-level content controls (DD-45)."""
    for child in container:
        tag = child.tag
        if tag == W_P:
            text = _norm(_accept_all_text(child))
            if not text:
                continue
            has_auto, level, num_id, abstract, outline = _paragraph_numbering(child, numbering)
            m = _LITERAL_NUM.match(text)
            blocks.append(
                ExtractedBlock(
                    order=len(blocks),
                    kind="paragraph",
                    text=text,
                    has_autonumber=has_auto,
                    list_level=level,
                    num_id=num_id,
                    abstract_num_id=abstract,
                    outline_level=outline,
                    literal_prefix=m.group(1) if m else None,
                    in_content_control=in_cc,
                )
            )
        elif tag == W_TBL:
            rows = _table_rows(child)
            if any(any(c for c in row) for row in rows):
                blocks.append(
                    ExtractedBlock(
                        order=len(blocks), kind="table", rows=rows, in_content_control=in_cc
                    )
                )
        elif tag == W_SDT:
            content = child.find(W_SDTCONTENT)
            if content is not None:
                _walk(content, blocks, in_cc=True, numbering=numbering)


def _ceiling_chars(path: Path) -> int:
    """Non-space char count across every <w:t> in document.xml — the accept-all
    text ceiling our extraction must approach (deletions use <w:delText>, excluded)."""
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    return sum(len(re.sub(r"\s", "", el.text)) for el in root.iter(W_T) if el.text)


def _extracted_chars(blocks: list[ExtractedBlock]) -> int:
    total = 0
    for b in blocks:
        if b.kind == "paragraph":
            total += len(re.sub(r"\s", "", b.text))
        elif b.rows:
            total += sum(len(re.sub(r"\s", "", c)) for row in b.rows for c in row)
    return total


def count_tracked_changes(path: str | Path) -> tuple[int, int]:
    """(<w:ins> count, <w:del> count) across document.xml — the clean-document
    guard's tracked-change signal (DD-46). Extraction already flattens these to
    their accepted state (insertions kept, deletions dropped); this just surfaces
    that any were present, so the operator can re-upload a clean draft if needed."""
    from lxml import etree

    with zipfile.ZipFile(Path(path)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    insertions = sum(1 for _ in root.iter(W_INS))
    deletions = sum(1 for _ in root.iter(W_DEL))
    return insertions, deletions


def _load_numbering(path: Path) -> _Numbering:
    """Build the style/abstractNum resolution context from styles.xml +
    numbering.xml. Both parts may be absent (synthetic docs); resolution then
    degrades to direct-numPr-only with empty maps."""
    from lxml import etree

    style_index: dict[str, _StyleNum] = {}
    abstract_map: dict[int, int] = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/styles.xml" in names:
            style_index = _build_style_index(etree.fromstring(z.read("word/styles.xml")))
        if "word/numbering.xml" in names:
            abstract_map = _build_abstract_map(etree.fromstring(z.read("word/numbering.xml")))
    return _Numbering(style_index, abstract_map)


def read_docx(path: str | Path) -> ParsedDocument:
    path = Path(path)
    body = Document(str(path)).element.body
    numbering = _load_numbering(path)
    blocks: list[ExtractedBlock] = []
    _walk(body, blocks, in_cc=False, numbering=numbering)
    return ParsedDocument(
        blocks=blocks,
        extracted_chars=_extracted_chars(blocks),
        ceiling_chars=_ceiling_chars(path),
    )
