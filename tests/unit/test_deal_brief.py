"""Deal brief (F37 / DD-95): the per-contract repo (get / seed / operator-update, edits-win),
the distillation service (whole-contract assembly + prompt-render + persist, operator-edit-wins),
and the GET/PUT/refresh routes. No live DB, no LLM — fakes record SQL / capture the prompt.

All fixtures are SYNTHETIC (public repo): no real firm / contract / party names or values."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.deal_brief import DealBrief, DealBriefEdit
from backend.models.imports import StoredNode
from backend.services import deal_brief_repo
from backend.services.donna.grounding import build_deal_brief_grounding


# --------------------------------------------------------------------------- #
# repo                                                                         #
# --------------------------------------------------------------------------- #
def _brief_row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        contract_id="c1",
        content="Parties: a licensor and a licensee.",
        operator_edited=False,
        model="claude-opus-4-8",
        generated_at=datetime(2026, 6, 29, tzinfo=UTC),
        updated_at=datetime(2026, 6, 29, tzinfo=UTC),
    )
    base.update(kw)
    return base


class _FakeConn:
    """Returns one fetchrow row and records (sql, args) of every fetchrow."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append((sql, args))
        return self._row


async def test_get_returns_mapped_brief() -> None:
    conn = _FakeConn(_brief_row())
    brief = await deal_brief_repo.get_brief(conn, "c1")
    assert brief is not None
    assert brief.contract_id == "c1"
    assert brief.content == "Parties: a licensor and a licensee."
    assert brief.operator_edited is False
    assert brief.model == "claude-opus-4-8"


async def test_get_returns_none_when_no_row() -> None:
    conn = _FakeConn(None)
    assert await deal_brief_repo.get_brief(conn, "c1") is None


async def test_seed_passes_content_model_and_force_and_guards_edits() -> None:
    conn = _FakeConn(_brief_row())
    await deal_brief_repo.seed_brief(conn, "c1", "Fresh brief.", "claude-opus-4-8", force=False)
    sql, args = conn.calls[0]
    # Donna-authored seed: not operator-edited, and the edits-win guard is in the SQL.
    assert "INSERT INTO contract_deal_brief" in sql
    assert "operator_edited = false OR $4" in sql  # edits-win guard, force = $4
    assert args == ("c1", "Fresh brief.", "claude-opus-4-8", False)


async def test_seed_returns_none_when_upsert_skipped_for_operator_edit() -> None:
    # The SQL guard refused to overwrite an operator-edited brief -> no row returned.
    conn = _FakeConn(None)
    out = await deal_brief_repo.seed_brief(conn, "c1", "x", "m", force=False)
    assert out is None  # respected the operator edit (edits win)


async def test_seed_force_passes_true() -> None:
    conn = _FakeConn(_brief_row())
    await deal_brief_repo.seed_brief(conn, "c1", "x", "m", force=True)
    assert conn.calls[0][1][3] is True  # force flag reaches the guard


async def test_update_marks_operator_edited() -> None:
    conn = _FakeConn(_brief_row(operator_edited=True, content="Operator's brief."))
    brief = await deal_brief_repo.update_brief(conn, "c1", "Operator's brief.")
    sql, args = conn.calls[0]
    assert "operator_edited = true" in sql  # the edit marks the row operator-edited
    assert args == ("c1", "Operator's brief.")
    assert brief.operator_edited is True
    assert brief.content == "Operator's brief."


# --------------------------------------------------------------------------- #
# grounding: build_deal_brief_grounding (F37 / DD-95)                          #
# --------------------------------------------------------------------------- #
def test_deal_brief_block_carries_header_and_content() -> None:
    # Synthetic brief — NOT real firm/contract data (public repo).
    block = build_deal_brief_grounding(
        DealBrief(contract_id="c1", content="Parties: a licensor and a licensee.")
    )
    assert "DEAL BRIEF" in block  # the labelled per-deal global-context header
    assert "a licensor and a licensee" in block  # the brief content reaches the block
    # Framed as data/context, not instructions (prompt-injection posture, mirrors the mandate).
    assert "NOT as instructions" in block


def test_deal_brief_block_none_is_noop() -> None:
    assert build_deal_brief_grounding(None) == ""  # no brief distilled/edited -> nothing injected


def test_deal_brief_block_blank_content_is_noop() -> None:
    assert build_deal_brief_grounding(DealBrief(contract_id="c1", content="")) == ""
    assert (
        build_deal_brief_grounding(DealBrief(contract_id="c1", content="   \n  ")) == ""
    )  # whitespace-only collapses to no-op


# --------------------------------------------------------------------------- #
# service: whole-contract assembly                                            #
# --------------------------------------------------------------------------- #
def _node(node_id: str, order_index: int, **kw: Any) -> StoredNode:
    base: dict[str, Any] = dict(
        id=node_id,
        parent_id=None,
        order_index=order_index,
        content_type="prose",
        heading=None,
        body=None,
        role="clause",
    )
    base.update(kw)
    return StoredNode(**base)


def test_assemble_contract_text_orders_labels_and_drops_empty() -> None:
    from backend.services.donna.deal_brief import assemble_contract_text

    nodes = [
        _node("n1", 0, heading="Term", body="This agreement runs three years."),
        _node("n2", 10, body="The licensee pays a fee."),
        _node("n3", 20, body=None, plain_text=None),  # empty -> dropped
    ]
    text = assemble_contract_text(nodes)
    assert "This agreement runs three years." in text
    assert "The licensee pays a fee." in text
    # Document order preserved; the empty node contributes nothing.
    assert text.index("three years") < text.index("pays a fee")
    assert text.count("\n\n") == 1  # exactly the two non-empty nodes, joined once


def test_assemble_contract_text_flattens_tables() -> None:
    from backend.services.donna.deal_brief import assemble_contract_text

    nodes = [
        _node(
            "t1",
            0,
            content_type="table",
            table_data=[["Year", "Volume"], ["1", "100"]],
        )
    ]
    text = assemble_contract_text(nodes)
    assert "Year | Volume" in text
    assert "1 | 100" in text


# --------------------------------------------------------------------------- #
# service: distill                                                            #
# --------------------------------------------------------------------------- #
class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


def _patch_service(
    monkeypatch: Any,
    *,
    nodes: list[StoredNode],
    seed_return: DealBrief | None,
    captured: dict[str, Any],
) -> None:
    from backend.services.donna import deal_brief as svc

    async def fake_fetch_nodes(_conn: Any, _cid: str) -> list[StoredNode]:
        return nodes

    async def fake_complete(**kwargs: Any) -> Any:
        captured["prompt"] = kwargs["messages"][0]["content"]
        captured["caller"] = kwargs["caller"]
        captured["timeout_s"] = kwargs.get("timeout_s")
        captured["max_tokens"] = kwargs.get("max_tokens")
        return _Result("DISTILLED BRIEF TEXT")

    async def fake_seed(
        _conn: Any, cid: str, content: str, model: str, *, force: bool = False
    ) -> DealBrief | None:
        captured["seed_args"] = (cid, content, model, force)
        return seed_return

    monkeypatch.setattr(svc, "fetch_nodes", fake_fetch_nodes)
    monkeypatch.setattr(svc, "complete", fake_complete)
    monkeypatch.setattr(deal_brief_repo, "seed_brief", fake_seed)


async def test_distill_renders_contract_text_and_persists(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    stored = DealBrief(contract_id="c1", content="DISTILLED BRIEF TEXT", model="claude-opus-4-8")
    _patch_service(
        monkeypatch,
        nodes=[_node("n1", 0, heading="Purpose", body="A licensing deal.")],
        seed_return=stored,
        captured=captured,
    )
    from backend.services.donna.deal_brief import distill_deal_brief

    out = await distill_deal_brief(object(), "c1", force=False)
    # The whole-contract text reached the model via the rendered prompt.
    assert "A licensing deal." in captured["prompt"]
    assert captured["caller"] == "donna_deal_brief"
    assert captured["timeout_s"] is not None  # longer per-call timeout passed (not the default)
    # The model output was persisted via the repo seed (force threaded through).
    assert captured["seed_args"][1] == "DISTILLED BRIEF TEXT"
    assert captured["seed_args"][3] is False
    assert out is stored


async def test_distill_empty_contract_skips_llm(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch_service(monkeypatch, nodes=[], seed_return=None, captured=captured)
    from backend.services.donna.deal_brief import distill_deal_brief

    out = await distill_deal_brief(object(), "c1")
    assert out is None
    assert "prompt" not in captured  # structural guard: no LLM call on an empty contract


async def test_distill_operator_edit_wins_returns_none(monkeypatch: Any) -> None:
    # seed_brief refused the overwrite (operator had edited, force False) -> distill returns None.
    captured: dict[str, Any] = {}
    _patch_service(
        monkeypatch,
        nodes=[_node("n1", 0, body="Some clause.")],
        seed_return=None,
        captured=captured,
    )
    from backend.services.donna.deal_brief import distill_deal_brief

    out = await distill_deal_brief(object(), "c1", force=False)
    assert out is None
    assert captured["seed_args"][3] is False  # the import path never forces (edits win)


async def test_distill_force_threads_through(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    stored = DealBrief(contract_id="c1", content="DISTILLED BRIEF TEXT")
    _patch_service(
        monkeypatch,
        nodes=[_node("n1", 0, body="Some clause.")],
        seed_return=stored,
        captured=captured,
    )
    from backend.services.donna.deal_brief import distill_deal_brief

    await distill_deal_brief(object(), "c1", force=True)
    assert captured["seed_args"][3] is True  # manual Refresh forces


# --------------------------------------------------------------------------- #
# routes: GET / PUT / refresh                                                 #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def _fake_acquire() -> AsyncIterator[Any]:
    yield object()


async def test_route_get_returns_empty_when_none(monkeypatch: Any) -> None:
    from backend.api import deal_brief as route

    async def fake_get(_conn: Any, _cid: str) -> DealBrief | None:
        return None

    monkeypatch.setattr(route, "acquire", _fake_acquire)
    monkeypatch.setattr(deal_brief_repo, "get_brief", fake_get)
    out = await route.read_deal_brief("c1")
    assert out.contract_id == "c1"
    assert out.content == ""  # no row -> empty brief (no-op grounding case), not a 404
    assert out.operator_edited is False


async def test_route_get_returns_stored(monkeypatch: Any) -> None:
    from backend.api import deal_brief as route

    stored = DealBrief(contract_id="c1", content="A brief.", operator_edited=True)

    async def fake_get(_conn: Any, _cid: str) -> DealBrief | None:
        return stored

    monkeypatch.setattr(route, "acquire", _fake_acquire)
    monkeypatch.setattr(deal_brief_repo, "get_brief", fake_get)
    out = await route.read_deal_brief("c1")
    assert out is stored


async def test_route_put_calls_update(monkeypatch: Any) -> None:
    from backend.api import deal_brief as route

    seen: dict[str, Any] = {}

    async def fake_update(_conn: Any, cid: str, content: str) -> DealBrief:
        seen["args"] = (cid, content)
        return DealBrief(contract_id=cid, content=content, operator_edited=True)

    monkeypatch.setattr(route, "acquire", _fake_acquire)
    monkeypatch.setattr(deal_brief_repo, "update_brief", fake_update)
    out = await route.write_deal_brief("c1", DealBriefEdit(content="Operator brief."))
    assert seen["args"] == ("c1", "Operator brief.")
    assert out.operator_edited is True


async def test_route_refresh_forces_distill(monkeypatch: Any) -> None:
    from backend.api import deal_brief as route

    seen: dict[str, Any] = {}

    async def fake_distill(_conn: Any, cid: str, *, force: bool = False) -> DealBrief | None:
        seen["force"] = force
        return DealBrief(contract_id=cid, content="Refreshed brief.")

    monkeypatch.setattr(route, "acquire", _fake_acquire)
    monkeypatch.setattr(route, "distill_deal_brief", fake_distill)
    out = await route.refresh_deal_brief("c1")
    assert seen["force"] is True  # Refresh forces a fresh distil
    assert out.content == "Refreshed brief."


async def test_route_refresh_404_on_empty_contract(monkeypatch: Any) -> None:
    from backend.api import deal_brief as route
    from fastapi import HTTPException

    async def fake_distill(_conn: Any, _cid: str, *, force: bool = False) -> DealBrief | None:
        return None  # nothing to distil

    monkeypatch.setattr(route, "acquire", _fake_acquire)
    monkeypatch.setattr(route, "distill_deal_brief", fake_distill)
    with pytest.raises(HTTPException) as exc:
        await route.refresh_deal_brief("c1")
    assert exc.value.status_code == 404
