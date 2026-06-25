"""Unit tests for the deterministic defined-terms scan (F16).

Synthetic GENERIC clause text only — no real contract content (privacy rule). The
assertions pin the precision-over-recall contract: clear `means` / `("Term")` forms
are captured with the right term/definition/source node; ordinary quoted phrases,
lower-case quotes, and `{Reference}` markers are NOT.
"""

from __future__ import annotations

from backend.models.imports import StoredNode
from backend.services.defined_terms import extract_terms_from_nodes


def _node(node_id: str, body: str | None = None, heading: str | None = None) -> StoredNode:
    return StoredNode(
        id=node_id,
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=heading,
        body=body,
        table_data=None,
        plain_text=body,
        role="clause",
        has_placeholder=False,
    )


def _by_term(nodes: list[StoredNode]) -> dict[str, tuple[str | None, str | None]]:
    return {t.term: (t.definition, t.source_node_id) for t in extract_terms_from_nodes(nodes)}


def test_means_form_captures_term_definition_and_source_node() -> None:
    nodes = [_node("n1", '"Widget Rate" means the rate per unit set out in [[Section 3]].')]
    result = _by_term(nodes)
    assert "Widget Rate" in result
    definition, source = result["Widget Rate"]
    assert definition == "the rate per unit set out in [[Section 3]]."
    assert source == "n1"


def test_shall_mean_variant_is_captured() -> None:
    nodes = [_node("n2", '"Acme Threshold" shall mean the minimum quantity of units.')]
    result = _by_term(nodes)
    assert result["Acme Threshold"][0] == "the minimum quantity of units."


def test_canonical_parenthetical_intro_captured_with_null_definition() -> None:
    nodes = [_node("n3", 'The parties (the "Master Agreement") agree as follows.')]
    result = _by_term(nodes)
    assert "Master Agreement" in result
    assert result["Master Agreement"] == (None, "n3")


def test_canonical_marker_with_means_captures_definition() -> None:
    nodes = [_node("n4", '("Base Fee") means the amount payable each quarter.')]
    result = _by_term(nodes)
    assert result["Base Fee"][0] == "the amount payable each quarter."


def test_each_a_prefix_intro_is_captured() -> None:
    nodes = [_node("n5", 'the suppliers (each a "Approved Supplier") shall comply.')]
    result = _by_term(nodes)
    assert "Approved Supplier" in result


def test_definition_trimmed_to_first_sentence() -> None:
    nodes = [
        _node(
            "n6",
            '"Delivery Window" means the agreed period. A separate clause governs delays.',
        )
    ]
    assert _by_term(nodes)["Delivery Window"][0] == "the agreed period."


def test_ordinary_quoted_phrase_is_not_captured() -> None:
    nodes = [_node("n7", 'The supplier shall deliver the "goods" promptly and safely.')]
    assert extract_terms_from_nodes(nodes) == []


def test_lowercase_quoted_term_is_rejected() -> None:
    nodes = [_node("n8", '"reasonable efforts" means commercially sensible steps.')]
    assert extract_terms_from_nodes(nodes) == []


def test_reference_and_crossref_markers_are_not_definitions() -> None:
    nodes = [_node("n9", "The fee is computed under {Widget Rate} per [[Section 3]].")]
    assert extract_terms_from_nodes(nodes) == []


def test_means_form_wins_over_bare_intro_for_same_term() -> None:
    nodes = [
        _node("n10", 'Introduced here (the "Royalty Rate").'),
        _node("n11", '"Royalty Rate" means three percent of net sales.'),
    ]
    result = _by_term(nodes)
    assert result["Royalty Rate"] == ("three percent of net sales.", "n10")


def test_heading_text_is_also_scanned() -> None:
    nodes = [_node("n12", body=None, heading='"Effective Date" means the signing date.')]
    assert _by_term(nodes)["Effective Date"][0] == "the signing date."


def test_multiple_terms_in_one_node_are_all_found() -> None:
    nodes = [
        _node(
            "n13",
            '"Product" means the finished item. "Territory" means the agreed geographic area.',
        )
    ]
    result = _by_term(nodes)
    assert set(result) == {"Product", "Territory"}
