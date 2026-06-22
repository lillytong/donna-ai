"""ContractTreeResponse.from_rows nests flat rows correctly (pure logic)."""

from __future__ import annotations

from backend.models.imports import ContractTreeResponse, StoredNode


def _row(node_id: str, parent: str | None, order: int, **kw: object) -> StoredNode:
    return StoredNode(id=node_id, parent_id=parent, order_index=order, content_type="prose", **kw)


def test_nests_and_orders_siblings_by_order_index() -> None:
    # Two roots, each with children; order_index collides across parents (unique
    # only within a parent), so nesting must not rely on global ordering.
    rows = [
        _row("a", None, 200, heading="Second article"),
        _row("a1", "a", 100, body="first child of a"),
        _row("b", None, 100, heading="First article"),
        _row("b1", "b", 200, body="second child of b"),
        _row("b2", "b", 100, body="first child of b"),
    ]

    tree = ContractTreeResponse.from_rows("c1", rows)

    assert tree.contract_id == "c1"
    assert [n.id for n in tree.nodes] == ["b", "a"]  # roots ordered
    assert [n.id for n in tree.nodes[0].children] == ["b2", "b1"]  # siblings ordered
    assert [n.id for n in tree.nodes[1].children] == ["a1"]


def test_table_node_round_trips_structured_data() -> None:
    rows = [
        StoredNode(
            id="t",
            parent_id=None,
            order_index=100,
            content_type="table",
            table_data=[["Param", "Value"], ["Fee", "5%"]],
            plain_text="Param | Value | Fee | 5%",
        )
    ]
    tree = ContractTreeResponse.from_rows("c1", rows)
    assert tree.nodes[0].table_data == [["Param", "Value"], ["Fee", "5%"]]
