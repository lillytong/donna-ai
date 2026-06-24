"""Pure logic for conceptual clause search: the candidate-list builder (heading
nodes only, child-body snippets, snippet cap) and the JSON-answer parser."""

from __future__ import annotations

from backend.models.imports import StoredNode
from backend.services import clause_search


def _node(node_id: str, **kw: object) -> StoredNode:
    base: dict[str, object] = dict(
        id=node_id,
        parent_id=None,
        order_index=0,
        content_type="prose",
        role="clause",
    )
    base.update(kw)
    return StoredNode(**base)  # type: ignore[arg-type]


def test_build_candidate_block_includes_only_heading_nodes() -> None:
    nodes = [
        _node("h1", heading="Confidentiality"),
        _node("b1", parent_id="h1", body="Each party shall keep secret..."),
        _node("b2", body="A stray body paragraph with no heading"),
    ]
    block = clause_search.build_candidate_block(nodes)
    lines = block.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("h1 :: clause :: Confidentiality :: ")
    # The body paragraph nodes are never offered as candidates.
    assert "b1" not in block
    assert "stray body paragraph" not in block


def test_build_candidate_block_snippet_is_capped() -> None:
    long_body = "x" * 500
    nodes = [
        _node("h1", heading="Term"),
        _node("b1", parent_id="h1", body=long_body),
    ]
    snippet = clause_search.build_candidate_block(nodes).split(" :: ")[-1]
    assert len(snippet) == clause_search._SNIPPET_CHARS


def test_build_candidate_block_empty_when_no_headings() -> None:
    nodes = [_node("b1", body="only body"), _node("b2", body="more body")]
    assert clause_search.build_candidate_block(nodes) == ""


def test_parse_match_reads_node_id() -> None:
    assert clause_search._parse_match('{"node_id": "abc"}').node_id == "abc"


def test_parse_match_reads_null() -> None:
    assert clause_search._parse_match('{"node_id": null}').node_id is None


def test_parse_match_tolerates_fenced_json() -> None:
    assert clause_search._parse_match('```json\n{"node_id": "abc"}\n```').node_id == "abc"


def test_parse_match_unparseable_is_no_match() -> None:
    assert clause_search._parse_match("sorry, no idea").node_id is None
