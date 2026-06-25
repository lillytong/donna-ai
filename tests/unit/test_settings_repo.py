"""Repo-level cascade logic for delete_contract — SQL order and count parsing.

No live DB: a fake connection records execute() calls and returns asyncpg-style
command tags ("DELETE N" / "UPDATE N"), so the FK deletion order, the DD-63
delete-vs-SET-NULL handling of deal-shared rows, and the count parsing are
exercised without Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.services import settings_repo


class _FakeConn:
    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        # UPDATEs (cross_references / defined_terms SET NULL) report an UPDATE tag;
        # everything else is a DELETE. Count parsing must handle both verbs.
        verb = "UPDATE" if sql.lstrip().upper().startswith("UPDATE") else "DELETE"
        return f"{verb} {self._counts[len(self.calls) - 1]}"


def _op_and_table(sql: str) -> tuple[str, str]:
    """(verb, table) for a DELETE FROM <t> ... or UPDATE <t> SET ... statement."""
    tokens = sql.split()
    verb = tokens[0].upper()
    if verb == "UPDATE":
        return verb, tokens[1]
    # DELETE FROM <table>; the node-scoped deletes also contain a sub-SELECT FROM
    # nodes, so take the FIRST FROM's table only.
    return verb, sql.split("FROM", 1)[1].split()[0]


# The full FK-correct order: every node-child + issue-child + conversation-child is
# cleared before its parent; cross_references/defined_terms are handled before the
# nodes they point at; the contract row is last. Counts are distinct so the
# ContractDeletion field mapping is pinned, not just the totals.
_EXPECTED_OPS = [
    ("DELETE", "donna_recommendations"),
    ("DELETE", "brainstorm_summaries"),  # DD-77: per-issue brainstorm summaries (FK issue_id)
    ("DELETE", "issues"),
    # F03b: revision staging cleared after issues (which FK sessions), before nodes/snapshots.
    ("DELETE", "counterparty_revision_hunks"),
    ("DELETE", "counterparty_revision_changes"),
    ("DELETE", "counterparty_revision_sessions"),
    ("DELETE", "donna_messages"),
    ("DELETE", "donna_conversations"),
    ("DELETE", "node_embeddings"),
    ("DELETE", "parameter_references"),
    ("DELETE", "footnotes"),
    ("DELETE", "node_versions"),
    ("DELETE", "cross_references"),  # this contract's own (source) refs
    ("UPDATE", "cross_references"),  # sibling refs pointing in -> target SET NULL
    ("UPDATE", "defined_terms"),  # deal-scoped term preserved; source_node_id nulled
    ("DELETE", "nodes"),
    ("DELETE", "snapshot_pointers"),
    ("DELETE", "contract_snapshots"),
    ("DELETE", "contracts"),
]


async def test_delete_contract_cascades_in_fk_order() -> None:
    # Distinct counts so each ContractDeletion field is traced to its statement.
    # recs bsum issues | revH revC revS | msg conv emb pref foot nver xdel xnull
    # dtnull nodes snp snap ctr
    conn = _FakeConn([0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 7, 5, 4, 2, 6, 12, 0, 0, 1])

    result = await settings_repo.delete_contract(conn, "contract-1")

    assert result is not None
    assert (result.issues, result.nodes) == (3, 12)
    assert result.footnotes == 7
    assert result.node_versions == 5
    assert result.cross_references_deleted == 4
    assert result.cross_references_nulled == 2
    assert result.defined_terms_nulled == 6

    assert [_op_and_table(sql) for sql, _ in conn.calls] == _EXPECTED_OPS
    assert all(args == ("contract-1",) for _, args in conn.calls)


async def test_delete_contract_preserves_deal_shared_rows() -> None:
    # DD-63: defined_terms is NEVER the target of a DELETE (deal-scoped, shared);
    # it is only ever SET NULL. cross_references whose SOURCE is this contract are
    # DELETEd, while sibling refs pointing IN are SET NULL (target nulled, row kept).
    conn = _FakeConn([0] * 19)

    await settings_repo.delete_contract(conn, "contract-1")

    sqls = [sql for sql, _ in conn.calls]
    ops = [_op_and_table(sql) for sql in sqls]
    assert ("DELETE", "defined_terms") not in ops
    assert ("UPDATE", "defined_terms") in ops

    def _stmt(verb: str, table: str) -> str:
        return next(s for s in sqls if _op_and_table(s) == (verb, table))

    xref_source_delete = _stmt("DELETE", "cross_references")
    assert "source_contract_id = $1" in xref_source_delete

    xref_target_null = _stmt("UPDATE", "cross_references")
    assert "target_node_id = NULL" in xref_target_null
    assert "target_contract_id = NULL" in xref_target_null
    assert "target_contract_id = $1" in xref_target_null

    defined_terms_null = _stmt("UPDATE", "defined_terms")
    assert "source_node_id = NULL" in defined_terms_null


async def test_delete_contract_missing_returns_none() -> None:
    conn = _FakeConn([0] * 19)  # no rows anywhere -> contract did not exist

    result = await settings_repo.delete_contract(conn, "missing")

    assert result is None
