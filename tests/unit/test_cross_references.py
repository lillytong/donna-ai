"""Unit tests for the deterministic cross-reference scan + resolver (F17).

Synthetic GENERIC clause text only — no real contract content (privacy rule). The
assertions pin the precision-over-recall contract: keyword-introduced designators
("clause 12.3", "Section 5", "Schedule I") are captured; bare numbers, dates and
amounts are NOT; decimal designators resolve through the shared `_plan` numbering
to a node id while letter/roman designators stay unresolved. `persist_*` idempotency
is covered with a fake asyncpg connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.models.imports import StoredNode
from backend.services.cross_references import (
    build_number_map,
    extract_cross_references,
    extract_cross_references_from_nodes,
    persist_cross_references,
    resolve_designator,
)


def _node(
    node_id: str,
    body: str | None = None,
    heading: str | None = None,
    *,
    order_index: int | None = None,
    parent_id: str | None = None,
    role: str = "clause",
) -> StoredNode:
    return StoredNode(
        id=node_id,
        parent_id=parent_id,
        order_index=order_index if order_index is not None else int(node_id[1:]) * 10,
        content_type="prose",
        heading=heading,
        body=body,
        table_data=None,
        plain_text=body,
        role=role,
        has_placeholder=False,
    )


def _designators(text: str) -> list[tuple[str, str]]:
    return [(r.kind, r.designator) for r in extract_cross_references(text)]


# --- extraction: keyword + designator forms are HITS -------------------------


def test_clause_decimal_levels_are_captured() -> None:
    assert _designators("see clause 12") == [("clause", "12")]
    assert _designators("see clause 12.3") == [("clause", "12.3")]
    assert _designators("see Clause 7.1.2 below") == [("clause", "7.1.2")]


def test_section_keyword_is_captured() -> None:
    assert _designators("as defined in Section 5") == [("section", "5")]


def test_schedule_and_appendix_letter_roman_designators_are_captured() -> None:
    assert _designators("per Schedule I and Appendix B") == [
        ("schedule", "I"),
        ("appendix", "B"),
    ]


def test_multi_designator_enumeration_is_split() -> None:
    assert _designators("see clauses 4 and 5") == [("clause", "4"), ("clause", "5")]
    assert _designators("clauses 7 to 9") == [("clause", "7"), ("clause", "9")]
    assert _designators("sections 2, 4 and 6") == [
        ("section", "2"),
        ("section", "4"),
        ("section", "6"),
    ]


def test_connector_word_letter_is_not_a_designator() -> None:
    # Regression: "and"/"to"/"through" must not leak a single-letter designator
    # ("an[d]" -> "d") via the IGNORECASE [A-Z] branch of the tail re-scan.
    for _kind, designator in _designators("clause 3 through 6"):
        assert designator not in {"d", "o", "h"}
    assert _designators("clause 3 through 6") == [("clause", "3"), ("clause", "6")]


def test_extraction_is_case_insensitive_on_keyword() -> None:
    assert _designators("SEE CLAUSE 4") == [("clause", "4")]
    assert _designators("see clause 4") == [("clause", "4")]


# --- extraction: bare numbers / dates / amounts are NON-HITS -----------------


def test_bare_number_is_not_a_reference() -> None:
    assert extract_cross_references("payment is due within 30 days.") == []


def test_year_is_not_a_reference() -> None:
    assert extract_cross_references("dated 5 January 2026 by the parties.") == []


def test_money_amount_is_not_a_reference() -> None:
    assert extract_cross_references("a fee of $5 million per annum.") == []


# --- resolution against the shared numbering ---------------------------------


def test_decimal_designator_resolves_to_node_id() -> None:
    nodes = [_node("n1", "x"), _node("n2", "y"), _node("n3", "z")]
    number_map = build_number_map(nodes)
    assert number_map == {"1": "n1", "2": "n2", "3": "n3"}
    assert resolve_designator("2", number_map) == "n2"


def test_unresolvable_decimal_designator_is_none() -> None:
    nodes = [_node("n1", "x"), _node("n2", "y")]
    number_map = build_number_map(nodes)
    assert resolve_designator("99", number_map) is None


def test_letter_and_roman_designators_do_not_resolve() -> None:
    nodes = [_node("n1", "x"), _node("n2", "y")]
    number_map = build_number_map(nodes)
    assert resolve_designator("B", number_map) is None
    assert resolve_designator("IV", number_map) is None


def test_from_nodes_binds_source_and_resolves_target() -> None:
    nodes = [
        _node("n1", "the rate applies as set out in clause 2."),
        _node("n2", "Payment terms."),
        _node("n3", "see clause 99 for details."),
    ]
    refs = {r.source_node_id: r for r in extract_cross_references_from_nodes(nodes)}
    assert refs["n1"].target_node_id == "n2"
    assert refs["n1"].resolved is True
    assert refs["n3"].target_node_id is None
    assert refs["n3"].resolved is False


def test_self_referential_designator_is_dropped() -> None:
    # n1 is clause 1 and cites "clause 1" — a clause citing its own number is noise.
    nodes = [_node("n1", "as described in clause 1 above."), _node("n2", "y")]
    refs = extract_cross_references_from_nodes(nodes)
    assert all(r.source_node_id != "n1" for r in refs)


def test_heading_text_is_also_scanned() -> None:
    nodes = [
        _node("n1", body=None, heading="Defined in clause 2"),
        _node("n2", "Payment terms."),
    ]
    refs = extract_cross_references_from_nodes(nodes)
    assert refs[0].source_node_id == "n1"
    assert refs[0].target_node_id == "n2"


def test_duplicate_designator_in_one_node_is_deduped() -> None:
    nodes = [
        _node("n1", "clause 2 governs; see clause 2 again."),
        _node("n2", "Payment terms."),
    ]
    refs = [r for r in extract_cross_references_from_nodes(nodes) if r.source_node_id == "n1"]
    assert len(refs) == 1


# --- persistence idempotency -------------------------------------------------


class _FakeConn:
    """Minimal asyncpg stand-in: records DELETEs and INSERTs, hands back rows."""

    def __init__(self) -> None:
        self.deletes = 0
        self.rows: list[tuple[Any, ...]] = []
        self._counter = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, _sql: str, *_args: Any) -> None:
        self.deletes += 1
        self.rows.clear()  # DELETE clears this contract's existing rows

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any]:
        source_node_id, source_contract_id, target_node_id, target_contract_id = args
        self._counter += 1
        self.rows.append(args)
        return dict(
            id=self._counter,
            source_node_id=source_node_id,
            source_contract_id=source_contract_id,
            target_node_id=target_node_id,
            target_contract_id=target_contract_id,
        )


async def test_persist_is_idempotent_on_rerun() -> None:
    nodes = [
        _node("n1", "as set out in clause 2."),
        _node("n2", "Payment terms."),
        _node("n3", "see clause 99 for details."),
    ]
    conn = _FakeConn()

    first = await persist_cross_references(conn, "c1", nodes)
    after_first = len(conn.rows)
    second = await persist_cross_references(conn, "c1", nodes)

    # Re-running clears then re-inserts: the row set converges, never duplicates.
    assert conn.deletes == 2
    assert len(conn.rows) == after_first == len(first) == len(second)
    # The resolved ref carries both target columns; the unresolvable one leaves NULL.
    by_source = {r.source_node_id: r for r in second}
    assert by_source["n1"].target_node_id == "n2"
    assert by_source["n1"].target_contract_id == "c1"
    assert by_source["n3"].target_node_id is None
    assert by_source["n3"].target_contract_id is None
