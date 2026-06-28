"""Redline export (F15): the tracked-changes renderer emits w:ins/w:del with the
configured author (never "Donna"), unchanged nodes carry no markup, and the diff
service reconstructs the change set from node_versions against the baseline.

No live DB: render is pure; the service path uses a fake connection modelling the
pointer lookup, the snapshot fetch, the live-node read, and the change-set query.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.models.imports import StoredNode
from backend.models.redline import DeletedNode, MovedNode, NodeDiff
from backend.services.export import redline
from backend.services.export.redline import (
    BaselineNotFound,
    NoBaselineSnapshot,
    build_redline,
)
from backend.services.export.render_redline import render_redline_docx

_AUTHOR = "Northwind Trading Ltd"
_TS = "2026-06-24T12:00:00Z"
_BASE_TS = datetime(2026, 6, 1, tzinfo=UTC)


def _node(node_id: str, **kw: Any) -> StoredNode:
    base: dict[str, Any] = dict(
        id=node_id,
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body="Body.",
        role="clause",
    )
    base.update(kw)
    return StoredNode(**base)


def _document_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_edited_node_emits_del_old_and_ins_new() -> None:
    live = [_node("e", body="The term is five (5) years.")]
    diffs = {
        "e": NodeDiff(
            node_id="e",
            change_type="edited",
            text_before="The term is three (3) years.",
            text_after="The term is five (5) years.",
        )
    }
    data = render_redline_docx(live, diffs, [], {}, _AUTHOR, _TS)
    xml = _document_xml(data)

    assert "<w:del " in xml
    assert "<w:ins " in xml
    assert "three (3) years" in xml  # struck old text (w:delText)
    assert "five (5) years" in xml  # inserted new text (w:t)
    assert f'w:author="{_AUTHOR}"' in xml
    assert f'w:date="{_TS}"' in xml
    assert "Donna" not in xml


def test_inserted_node_emits_only_ins() -> None:
    live = [_node("i", body="A new indemnity clause.")]
    diffs = {
        "i": NodeDiff(node_id="i", change_type="inserted", text_after="A new indemnity clause.")
    }
    data = render_redline_docx(live, diffs, [], {}, _AUTHOR, _TS)
    xml = _document_xml(data)

    assert "<w:ins " in xml
    assert "<w:del " not in xml
    assert "A new indemnity clause." in xml


def test_deleted_node_emits_only_del() -> None:
    deleted = [
        DeletedNode(
            id="d", parent_id=None, order_index=200, content_type="prose", text="An struck clause."
        )
    ]
    data = render_redline_docx([], {}, deleted, {}, _AUTHOR, _TS)
    xml = _document_xml(data)

    assert "<w:del " in xml
    assert "<w:ins " not in xml
    assert "An struck clause." in xml


def test_unchanged_node_carries_no_markup() -> None:
    live = [_node("u", body="This clause is untouched.")]
    data = render_redline_docx(live, {}, [], {}, _AUTHOR, _TS)
    xml = _document_xml(data)

    assert "<w:ins " not in xml
    assert "<w:del " not in xml
    assert "This clause is untouched." in xml


def test_author_never_donna_even_when_mixed() -> None:
    live = [_node("e", body="new")]
    diffs = {"e": NodeDiff(node_id="e", change_type="edited", text_before="old", text_after="new")}
    deleted = [
        DeletedNode(id="d", parent_id=None, order_index=300, content_type="prose", text="gone")
    ]
    xml = _document_xml(render_redline_docx(live, diffs, deleted, {}, _AUTHOR, _TS))

    assert xml.count(f'w:author="{_AUTHOR}"') == 3  # del(old) + ins(new) + del(gone)
    assert "Donna" not in xml


# --- diff / orchestration path (fake connection) ---------------------------------


def _version(
    node_id: str, before: str | None, after: str | None, *, deleted: bool = False
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "body_before": before,
        "body_after": after,
        "created_at": datetime(2026, 6, 10, tzinfo=UTC),
        "is_deleted": deleted,
    }


def _live_record(node_id: str, body: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "parent_id": None,
        "order_index": 100,
        "content_type": "prose",
        "heading": None,
        "body": body,
        "table_data": None,
        "plain_text": body,
        "role": "clause",
        "has_placeholder": False,
    }


class _FakeConn:
    def __init__(
        self,
        *,
        pointer: dict[str, Any] | None,
        snapshot: dict[str, Any] | None,
        live: list[dict[str, Any]],
        versions: list[dict[str, Any]],
    ) -> None:
        self.pointer = pointer
        self.snapshot = snapshot
        self.live = live
        self.versions = versions

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "node_versions" in sql:
            return self.versions
        if "FROM nodes" in sql:
            return self.live
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "snapshot_pointers" in sql:
            return self.pointer
        if "FROM contract_snapshots" in sql:
            return self.snapshot
        return None


def _snapshot_row(tree: list[dict[str, Any]]) -> dict[str, Any]:
    import json

    return {
        "id": "snapB",
        "contract_id": "c1",
        "label": "shared",
        "tree": json.dumps(tree),
        "origin": "export",
        "created_at": _BASE_TS,
    }


def _baseline_tree_node(node_id: str, body: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=node_id,
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body=body,
        is_deleted=False,
    )
    base.update(kw)
    return base


async def test_build_redline_reconstructs_edit_insert_delete() -> None:
    baseline_tree = [
        _baseline_tree_node("u", "Untouched clause."),
        _baseline_tree_node("e", "Old wording.", order_index=200),
        _baseline_tree_node("d", "Doomed clause.", order_index=300),
    ]
    conn = _FakeConn(
        pointer={"id": "snapB", "created_at": _BASE_TS},
        snapshot=_snapshot_row(baseline_tree),
        live=[
            _live_record("u", "Untouched clause."),
            _live_record("e", "New wording."),
            _live_record("i", "Inserted clause."),
        ],
        versions=[
            _version("e", "Old wording.", "New wording."),
            _version("i", None, "Inserted clause."),
            _version("d", "Doomed clause.", None, deleted=True),
        ],
    )

    data = await build_redline(conn, "c1", None, {})
    xml = _document_xml(data)

    assert "New wording." in xml and "<w:ins " in xml  # edit-new + insert
    assert "Old wording." in xml and "<w:del " in xml  # edit-old
    assert "Doomed clause." in xml  # deletion struck
    assert "Inserted clause." in xml
    assert "Untouched clause." in xml
    # the untouched clause text is not inside any change wrapper
    assert "Untouched clause." in xml.split("<w:ins")[0].split("<w:del")[0]


async def test_build_redline_no_baseline_raises() -> None:
    conn = _FakeConn(pointer=None, snapshot=None, live=[], versions=[])
    with pytest.raises(NoBaselineSnapshot):
        await build_redline(conn, "c1", None, {})


async def test_build_redline_unknown_override_raises() -> None:
    conn = _FakeConn(pointer=None, snapshot=None, live=[], versions=[])
    with pytest.raises(BaselineNotFound):
        await build_redline(conn, "c1", "ghost", {})


async def test_build_redline_override_wrong_contract_raises() -> None:
    conn = _FakeConn(
        pointer=None,
        snapshot=_snapshot_row([]),  # belongs to c1
        live=[],
        versions=[],
    )
    with pytest.raises(BaselineNotFound):
        await build_redline(conn, "other", "snapB", {})


async def test_reverted_edit_is_suppressed() -> None:
    """A node edited then reverted (net before == after) carries no markup."""
    baseline_tree = [_baseline_tree_node("r", "Same text.")]
    conn = _FakeConn(
        pointer={"id": "snapB", "created_at": _BASE_TS},
        snapshot=_snapshot_row(baseline_tree),
        live=[_live_record("r", "Same text.")],
        versions=[
            {
                "node_id": "r",
                "body_before": "Same text.",
                "body_after": "Changed.",
                "created_at": datetime(2026, 6, 10, tzinfo=UTC),
                "is_deleted": False,
            },
            {
                "node_id": "r",
                "body_before": "Changed.",
                "body_after": "Same text.",
                "created_at": datetime(2026, 6, 11, tzinfo=UTC),
                "is_deleted": False,
            },
        ],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))
    assert "<w:ins " not in xml
    assert "<w:del " not in xml
    assert "Same text." in xml


# --- structural diff: moves + table insert/delete (F15 v1-gap closure) -----------


def _live_rec(node_id: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=node_id,
        parent_id=None,
        order_index=100,
        content_type="prose",
        heading=None,
        body="Body.",
        table_data=None,
        plain_text="Body.",
        role="clause",
        has_placeholder=False,
    )
    base.update(kw)
    return base


def _struct_conn(
    *,
    baseline_tree: list[dict[str, Any]],
    live: list[dict[str, Any]],
    versions: list[dict[str, Any]] | None = None,
) -> _FakeConn:
    return _FakeConn(
        pointer={"id": "snapB", "created_at": _BASE_TS},
        snapshot=_snapshot_row(baseline_tree),
        live=live,
        versions=versions or [],
    )


def test_render_move_emits_del_at_old_and_ins_at_new() -> None:
    live = [_node("m", body="Moved clause.")]
    moved = {
        "m": MovedNode(
            id="m",
            baseline_parent_id=None,
            baseline_order_index=50,
            content_type="prose",
            baseline_text="Moved clause.",
            current_text="Moved clause.",
            move_kind="reorder",
        )
    }
    xml = _document_xml(render_redline_docx(live, {}, [], {}, _AUTHOR, _TS, moved, set()))

    assert "<w:del " in xml and "<w:ins " in xml  # del+ins move fallback
    assert xml.count("Moved clause.") >= 2  # struck at old position + inserted at new
    assert f'w:author="{_AUTHOR}"' in xml
    assert "Donna" not in xml


async def test_reparent_and_reorder_node_marked_as_move() -> None:
    baseline_tree = [
        _baseline_tree_node("p1", "Parent one.", order_index=100),
        _baseline_tree_node("p2", "Parent two.", order_index=200),
        _baseline_tree_node("c", "Child clause.", order_index=100, parent_id="p1"),
    ]
    conn = _struct_conn(
        baseline_tree=baseline_tree,
        live=[
            _live_rec("p1", body="Parent one.", order_index=100),
            _live_rec("p2", body="Parent two.", order_index=200),
            _live_rec("c", body="Child clause.", order_index=50, parent_id="p2"),
        ],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))

    assert "<w:del " in xml and "<w:ins " in xml
    assert xml.count("Child clause.") >= 2  # moved-from struck + moved-to inserted
    assert "Donna" not in xml


async def test_reorder_among_siblings_marked_as_move() -> None:
    baseline_tree = [
        _baseline_tree_node("a", "Clause A.", order_index=100),
        _baseline_tree_node("b", "Clause B.", order_index=200),
        _baseline_tree_node("c", "Clause C.", order_index=300),
    ]
    conn = _struct_conn(  # swap b and c -> a, c, b
        baseline_tree=baseline_tree,
        live=[
            _live_rec("a", body="Clause A.", order_index=100),
            _live_rec("c", body="Clause C.", order_index=200),
            _live_rec("b", body="Clause B.", order_index=300),
        ],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))

    assert "<w:del " in xml and "<w:ins " in xml  # the reordered node shown as a move


async def test_pure_renumber_shift_not_marked_as_move() -> None:
    baseline_tree = [
        _baseline_tree_node("a", "Clause A.", order_index=100),
        _baseline_tree_node("b", "Clause B.", order_index=200),
        _baseline_tree_node("c", "Clause C.", order_index=300),
    ]
    conn = _struct_conn(  # insert x between a and b: b/c numbers shift, order unchanged
        baseline_tree=baseline_tree,
        live=[
            _live_rec("a", body="Clause A.", order_index=100),
            _live_rec("x", body="Inserted X.", order_index=150),
            _live_rec("b", body="Clause B.", order_index=200),
            _live_rec("c", body="Clause C.", order_index=300),
        ],
        versions=[_version("x", None, "Inserted X.")],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))

    assert "<w:ins " in xml  # x is a genuine insertion
    assert "<w:del " not in xml  # b/c renumber-only -> NOT a move, nothing struck
    assert "Clause B." in xml and "Clause C." in xml


async def test_inserted_table_marked_inserted() -> None:
    baseline_tree = [_baseline_tree_node("c", "A clause.", order_index=100)]
    conn = _struct_conn(
        baseline_tree=baseline_tree,
        live=[
            _live_rec("c", body="A clause.", order_index=100),
            _live_rec(
                "t",
                content_type="table",
                order_index=200,
                body=None,
                table_data=[["H1", "H2"], ["v1", "v2"]],
            ),
        ],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))

    assert "<w:tbl" in xml
    assert "<w:ins " in xml
    assert "H1" in xml and "v1" in xml  # cell content present, marked inserted
    assert "Donna" not in xml


async def test_deleted_table_marked_deleted() -> None:
    baseline_tree = [
        _baseline_tree_node("c", "A clause.", order_index=100),
        _baseline_tree_node("t", "", order_index=200, content_type="table"),
    ]
    conn = _struct_conn(
        baseline_tree=baseline_tree,
        live=[_live_rec("c", body="A clause.", order_index=100)],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))

    assert "<w:tbl" in xml  # struck (empty) table at the baseline position
    assert "<w:del " in xml


async def test_edited_and_moved_node_shows_text_change_and_move() -> None:
    baseline_tree = [
        _baseline_tree_node("a", "Clause A.", order_index=100),
        _baseline_tree_node("m", "Old wording.", order_index=100, parent_id="a"),
    ]
    conn = _struct_conn(  # m reparented to root AND edited
        baseline_tree=baseline_tree,
        live=[
            _live_rec("a", body="Clause A.", order_index=100),
            _live_rec("m", body="New wording.", order_index=200),
        ],
        versions=[_version("m", "Old wording.", "New wording.")],
    )

    xml = _document_xml(await build_redline(conn, "c1", None, {}))

    assert "Old wording." in xml and "<w:del " in xml  # struck at old position
    assert "New wording." in xml and "<w:ins " in xml  # inserted (edited) at new position


async def test_resolve_author_uses_db_override_then_explicit(monkeypatch: Any) -> None:
    # _resolve_author now delegates to the DB-aware resolver (F25, DD-44): the editable
    # org-name override is the export author; an explicit DONNA_REDLINE_AUTHOR still wins.
    from backend.config.settings import Settings
    from backend.services import operator_org_repo

    class _OrgConn:
        def __init__(self, override: str) -> None:
            self._override = override

        async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any]:
            return {"organization_name": self._override}

    def _clean_settings(**env: str) -> Settings:
        monkeypatch.setenv("DATABASE_URL", "postgresql://donna:donna@localhost:5432/donna")
        for key in ("DONNA_OPERATOR_ORG_NAME", "DONNA_REDLINE_AUTHOR"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    monkeypatch.setattr(operator_org_repo, "get_settings", lambda: _clean_settings())
    assert await redline._resolve_author(_OrgConn("Configured Org")) == "Configured Org"

    monkeypatch.setattr(
        operator_org_repo,
        "get_settings",
        lambda: _clean_settings(DONNA_REDLINE_AUTHOR="Legal Department"),
    )
    assert await redline._resolve_author(_OrgConn("Configured Org")) == "Legal Department"
