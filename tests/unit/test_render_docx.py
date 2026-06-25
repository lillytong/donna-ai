"""Renderer pieces (F14): numbering re-derivation, the DD-37 caps transform,
table rendering, and the clause/non-clause numbering split. No DB, no network."""

from __future__ import annotations

import io

from backend.models.imports import StoredNode
from backend.services.export.render_docx import _plan, render_contract_docx
from docx import Document
from docx.oxml.ns import qn


def _node(
    node_id: str,
    parent_id: str | None,
    order_index: int,
    *,
    heading: str | None = None,
    body: str | None = None,
    table_data: list[list[str]] | None = None,
    content_type: str = "prose",
    role: str = "clause",
) -> StoredNode:
    return StoredNode(
        id=node_id,
        parent_id=parent_id,
        order_index=order_index,
        content_type=content_type,
        heading=heading,
        body=body,
        table_data=table_data,
        plain_text=heading or body or "",
        role=role,
    )


def _runs(paragraph: object) -> list[object]:
    return list(paragraph.runs)  # type: ignore[attr-defined]


def test_numbering_rederivation_from_tree_position() -> None:
    """DD-02: numbers are the decimal-outline path from tree position, never stored.
    Root clauses are 1,2…; children 1.1…; a depth-2 node carries two dots."""
    nodes = [
        _node("a", None, 100, heading="Definitions"),
        _node("b", "a", 100, body="Meaning of terms."),
        _node("c", None, 200, heading="Term"),
        _node("d", "c", 100, heading="Initial Term"),
        _node("e", "d", 100, body="Five years."),
    ]
    numbered = {n.id: num for n, num in _plan(nodes)}
    assert numbered == {"a": "1", "b": "1.1", "c": "2", "d": "2.1", "e": "2.1.1"}


def test_non_clause_nodes_are_unnumbered() -> None:
    """Front-matter (non-clause role) consumes no clause position and gets no number;
    the first real clause still numbers from 1 (DD-54)."""
    nodes = [
        _node("t", None, 100, heading="Master Agreement", role="title"),
        _node("p", None, 200, body="Between A and B.", role="parties"),
        _node("c", None, 300, heading="Definitions", role="clause"),
    ]
    numbered = {n.id: num for n, num in _plan(nodes)}
    assert numbered["t"] is None
    assert numbered["p"] is None
    assert numbered["c"] == "1"


def test_caps_renders_uppercase_but_preserves_stored_case() -> None:
    """DD-37 / §2.1: caps is the Word all-caps *display* property — the run text
    stays original-case (content boundary), Word renders it uppercase."""
    nodes = [_node("a", None, 100, heading="Confidentiality")]
    style_config = {"levels": {"0": {"bold": True, "caps": True}}}

    data = render_contract_docx(nodes, style_config)
    doc = Document(io.BytesIO(data))
    run = _runs(doc.paragraphs[0])[0]

    assert run.text == "Confidentiality"  # stored case untouched
    assert run.font.all_caps is True  # rendered uppercase via w:caps
    assert run.font.bold is True


def test_clause_paragraph_carries_numbering_non_clause_does_not() -> None:
    nodes = [
        _node("t", None, 100, heading="Master Agreement", role="title"),
        _node("c", None, 200, heading="Definitions", role="clause"),
    ]
    data = render_contract_docx(nodes, {})
    doc = Document(io.BytesIO(data))

    title_p, clause_p = doc.paragraphs[0], doc.paragraphs[1]
    assert (
        title_p._p.find(qn("w:pPr")) is None
        or title_p._p.find(  # no numbering
            qn("w:pPr")
        ).find(qn("w:numPr"))
        is None
    )
    num_pr = clause_p._p.find(qn("w:pPr")).find(qn("w:numPr"))
    assert num_pr is not None
    assert num_pr.find(qn("w:ilvl")).get(qn("w:val")) == "0"


def test_table_cells_render_faithfully() -> None:
    rows = [["Parameter", "Value"], ["Royalty", "5%"], ["Term", "5 years"]]
    nodes = [_node("tbl", None, 100, content_type="table", table_data=rows)]

    data = render_contract_docx(nodes, {})
    doc = Document(io.BytesIO(data))

    assert len(doc.tables) == 1
    table = doc.tables[0]
    rendered = [[cell.text for cell in row.cells] for row in table.rows]
    assert rendered == rows


def test_deeper_levels_inherit_nearest_style() -> None:
    """A depth-2 clause with no explicit level falls back to the deepest defined
    level (body style), so rendering never fails on under-specified configs."""
    nodes = [
        _node("a", None, 100, heading="Article"),
        _node("b", "a", 100, heading="Section"),
        _node("c", "b", 100, body="Deep clause body."),
    ]
    style_config = {"levels": {"0": {"bold": True}, "1": {"underline": True}}}
    data = render_contract_docx(nodes, style_config)
    doc = Document(io.BytesIO(data))
    deep_run = _runs(doc.paragraphs[2])[0]
    assert deep_run.text == "Deep clause body."
    assert deep_run.font.underline is True  # inherited from level 1
