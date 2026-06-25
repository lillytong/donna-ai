"""F31 issue-list export route — status codes, headers, streamed body.

The router is built onto a fresh app (it is not registered in backend.main — the
integrator wires it separately after merge), mirroring
tests/integration/test_cors_errors.py. The DB is faked: a fake acquire yields a dummy
conn and the repo loaders are monkeypatched to return synthetic generic issues +
nodes (no real contract content).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import issue_export
from backend.models.imports import StoredNode
from backend.models.issues import StoredIssue
from backend.models.settings import StoredContract
from backend.services import issue_repo
from fastapi import FastAPI
from fastapi.testclient import TestClient

_NOW = datetime(2026, 6, 24, tzinfo=UTC)
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_MAGIC = b"PK\x03\x04"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(issue_export.router)
    return app


def _contract() -> StoredContract:
    return StoredContract(
        id="c1",
        client_id="cl1",
        deal_id="d1",
        contract_type_id="t1",
        name="Generic Agreement",
        status="drafting",
        created_at=_NOW,
    )


def _issues() -> list[StoredIssue]:
    return [
        StoredIssue(
            id="i1",
            contract_id="c1",
            node_id="n1",
            title="Generic point",
            status="open",
            initiator="operator",
            authority="within-operator-authority",
            needs_legal_review=False,
            category="commercial",
            priority=5,
            created_at=_NOW,
        ),
        StoredIssue(
            id="i2",
            contract_id="c1",
            node_id=None,
            title="Contract-level point",
            status="open",
            initiator="counterparty",
            authority="within-operator-authority",
            needs_legal_review=False,
            category="commercial",
            priority=None,
            created_at=_NOW,
        ),
    ]


def _nodes() -> list[StoredNode]:
    return [
        StoredNode(id="n1", parent_id=None, order_index=100, content_type="prose", role="clause"),
    ]


def _install(
    monkeypatch: Any,
    *,
    contract: StoredContract | None,
    issues: list[StoredIssue],
    nodes: list[StoredNode],
) -> None:
    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[object]:
        yield object()

    async def fake_get(_conn: Any, _cid: str) -> StoredContract | None:
        return contract

    async def fake_list(_conn: Any, _cid: str, _status: str | None = None) -> list[StoredIssue]:
        return issues

    async def fake_nodes(_conn: Any, _cid: str) -> list[StoredNode]:
        return nodes

    monkeypatch.setattr(issue_export, "acquire", fake_acquire)
    monkeypatch.setattr(issue_export, "get_contract", fake_get)
    monkeypatch.setattr(issue_repo, "list_issues", fake_list)
    monkeypatch.setattr(issue_export, "fetch_nodes", fake_nodes)


def test_export_returns_docx(monkeypatch: Any) -> None:
    _install(monkeypatch, contract=_contract(), issues=_issues(), nodes=_nodes())
    resp = TestClient(_app()).get("/contracts/c1/issue-list/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == _DOCX_MEDIA_TYPE
    assert resp.headers["content-disposition"] == (
        'attachment; filename="Generic Agreement - open issues.docx"'
    )
    assert resp.content.startswith(_DOCX_MAGIC)


def test_export_404_when_contract_absent(monkeypatch: Any) -> None:
    _install(monkeypatch, contract=None, issues=[], nodes=[])
    resp = TestClient(_app()).get("/contracts/missing/issue-list/export")
    assert resp.status_code == 404


def test_export_empty_issues_still_valid_docx(monkeypatch: Any) -> None:
    _install(monkeypatch, contract=_contract(), issues=[], nodes=_nodes())
    resp = TestClient(_app()).get("/contracts/c1/issue-list/export")
    assert resp.status_code == 200
    assert resp.content.startswith(_DOCX_MAGIC)
