"""Round-trip oracle for the inline-enumerator split (F03e, SPEC §6 — THE GATE).

The §2.1 content-integrity guarantee for F03e: splitting one paragraph into a
lead-in + N ordered children must be byte-identical on reassembly — the lead-in
text, every child's own `(a)`/`(i)` marker, and the separators/punctuation between
them. A corruption here is a §2.4 trust failure, not a parse miss, so this gate is
falsifiable: if reassembly cannot be made to hold, the splitter must not ship.

Two layers, mirroring test_export_roundtrip.py:
  - a synthetic, always-runs fixture — exact reassembly + the 1-parent/2-children
    acceptance shape + the defined-term carve-out;
  - the real `sample-contract.docx` (skipped when absent / gitignored) — proves the
    transform is content-lossless on real prose: every node's text survives, and
    every split node reassembles to its exact pre-split paragraph.

The invariant used at the tree level: with children placed immediately after their
lead-in, the in-order concatenation of all node texts is unchanged by the split
(a lead-in's shortened text plus its children's texts rejoin to the original).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.models.contract_tree import ParsedTree, TreeNode
from backend.services.import_.docx_reader import read_docx
from backend.services.import_.inline_split import _split_text, split_inline_enumerators
from backend.services.import_.tree_builder import build_tree

_SAMPLE = Path(__file__).resolve().parents[2] / "sample-contract.docx"


def _content_stream(tree: ParsedTree) -> str:
    """In-order concatenation of every prose node's text — the conserved quantity:
    a split rewrites a node into lead-in + children placed right after it, so the
    stream is invariant iff no content was dropped, duplicated, or reordered."""
    return " ".join(n.text for n in tree.nodes if n.kind == "prose")


def _reassembles(text: str) -> bool:
    res = _split_text(text)
    if res is None:
        return True  # not split -> trivially preserved
    lead_in, children = res
    return " ".join([lead_in, *children]) == text


def _leaf(index: int, text: str) -> TreeNode:
    return TreeNode(
        index=index,
        parent_index=None,
        depth=0,
        order_index=(index + 1) * 100,
        kind="prose",
        text=text,
        role="clause",
    )


# --- Layer A: synthetic, always runs -----------------------------------------


def test_synthetic_split_reassembles_byte_identical() -> None:
    paragraphs = [
        "The following shall apply: (a) both parties act; (b) no party objects",
        "Provided that: (i) X occurs; (ii) Y occurs; (iii) Z occurs",
        "A plain clause with no enumerator run at all.",
        '"Affiliate" means (i) a parent; (ii) a subsidiary',  # carve-out, not split
    ]
    pre = ParsedTree(nodes=[_leaf(i, t) for i, t in enumerate(paragraphs)])
    post = split_inline_enumerators(pre)

    # (lead-in+2) + (lead-in+3) + plain + definition-carve-out(unsplit) = 3+4+1+1.
    assert len(post.nodes) == 9
    # acceptance shape for the first paragraph
    assert [n.text for n in post.nodes[:3]] == [
        "The following shall apply:",
        "(a) both parties act;",
        "(b) no party objects",
    ]
    # content stream conserved exactly across the whole tree
    assert _content_stream(post) == _content_stream(pre)
    # every paragraph reassembles byte-identically
    for t in paragraphs:
        assert _reassembles(t), t


def test_synthetic_topology_is_persistable() -> None:
    pre = ParsedTree(nodes=[_leaf(0, "Lead: (a) one; (b) two; (c) three")])
    post = split_inline_enumerators(pre)
    for i, n in enumerate(post.nodes):
        assert n.index == i
        if n.parent_index is not None:
            assert n.parent_index < n.index  # parents-before-children (persist invariant)


# --- Layer B: the real contract (skipped when gitignored sample is absent) ----


@pytest.mark.skipif(not _SAMPLE.exists(), reason="sample-contract.docx is gitignored / absent")
def test_real_sample_split_is_content_lossless() -> None:
    pre = build_tree(read_docx(_SAMPLE))
    post = split_inline_enumerators(pre)

    # No content lost, duplicated, or reordered by the transform.
    assert _content_stream(post) == _content_stream(pre)
    # Every node's text (split or not) reassembles to its source exactly.
    for n in pre.nodes:
        if n.kind == "prose":
            assert _reassembles(n.text), f"reassembly failed on: {n.text!r}"


@pytest.mark.skipif(not _SAMPLE.exists(), reason="sample-contract.docx is gitignored / absent")
def test_real_sample_no_regression_when_no_run_present() -> None:
    # Whether or not the real sample contains inline runs, the split must never
    # corrupt it: node count only grows by the children it adds, never shrinks, and
    # the un-split nodes are untouched.
    pre = build_tree(read_docx(_SAMPLE))
    post = split_inline_enumerators(pre)
    assert len(post.nodes) >= len(pre.nodes)
