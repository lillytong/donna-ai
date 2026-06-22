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


def _paragraph_numbering(p: Any) -> tuple[bool, int | None, int | None]:
    pPr = p.find(W_PPR)
    if pPr is None:
        return False, None, None
    numPr = pPr.find(W_NUMPR)
    if numPr is None:
        return False, None, None
    ilvl = numPr.find(W_ILVL)
    num_id = numPr.find(W_NUMID)
    level = int(ilvl.get(W_VAL)) if ilvl is not None and ilvl.get(W_VAL) is not None else None
    nid = int(num_id.get(W_VAL)) if num_id is not None and num_id.get(W_VAL) is not None else None
    return True, level, nid


def _table_rows(tbl: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in tbl.findall(W_TR):
        rows.append([_norm(_accept_all_text(tc)) for tc in tr.findall(W_TC)])
    return rows


def _walk(container: Any, blocks: list[ExtractedBlock], in_cc: bool) -> None:
    """Append blocks for the direct w:p / w:tbl / w:sdt children of `container`,
    in document order, descending into block-level content controls (DD-45)."""
    for child in container:
        tag = child.tag
        if tag == W_P:
            text = _norm(_accept_all_text(child))
            if not text:
                continue
            has_auto, level, num_id = _paragraph_numbering(child)
            m = _LITERAL_NUM.match(text)
            blocks.append(
                ExtractedBlock(
                    order=len(blocks),
                    kind="paragraph",
                    text=text,
                    has_autonumber=has_auto,
                    list_level=level,
                    num_id=num_id,
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
                _walk(content, blocks, in_cc=True)


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


def read_docx(path: str | Path) -> ParsedDocument:
    path = Path(path)
    body = Document(str(path)).element.body
    blocks: list[ExtractedBlock] = []
    _walk(body, blocks, in_cc=False)
    return ParsedDocument(
        blocks=blocks,
        extracted_chars=_extracted_chars(blocks),
        ceiling_chars=_ceiling_chars(path),
    )
