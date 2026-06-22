"""Settings CRUD routes: request parsing, response shape, status codes.

The DB and repo boundaries are mocked — no live database. TestClient is used
without its context manager so the app lifespan (pool open/close) never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.api import settings as settings_api
from backend.main import app
from backend.models.settings import (
    StoredClient,
    StoredContract,
    StoredContractType,
    StoredDeal,
)
from fastapi.testclient import TestClient

client = TestClient(app)

_NOW = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def _fake_acquire() -> AsyncIterator[object]:
    yield object()


def test_create_client_returns_stored(monkeypatch: Any) -> None:
    async def fake_create(_conn: Any, payload: Any) -> str:
        return "client-1"

    async def fake_get(_conn: Any, client_id: str) -> StoredClient:
        return StoredClient(
            id=client_id,
            name="Acme",
            relationship_type="partner",
            status="active",
            notes=None,
            created_at=_NOW,
        )

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "create_client", fake_create)
    monkeypatch.setattr(settings_api.settings_repo, "get_client", fake_get)

    resp = client.post("/clients", json={"name": "Acme", "relationship_type": "partner"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "client-1"
    assert body["relationship_type"] == "partner"
    assert body["status"] == "active"


def test_create_client_rejects_bad_enum() -> None:
    resp = client.post("/clients", json={"name": "Acme", "relationship_type": "bogus"})
    assert resp.status_code == 422


def test_list_clients(monkeypatch: Any) -> None:
    async def fake_list(_conn: Any) -> list[StoredClient]:
        return [
            StoredClient(
                id="c1",
                name="Acme",
                relationship_type="counterparty",
                status="active",
                created_at=_NOW,
            )
        ]

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "list_clients", fake_list)

    resp = client.get("/clients")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "c1"


def test_create_deal_returns_stored(monkeypatch: Any) -> None:
    async def fake_create(_conn: Any, payload: Any) -> str:
        return "deal-1"

    async def fake_get(_conn: Any, deal_id: str) -> StoredDeal:
        return StoredDeal(
            id=deal_id,
            client_id="client-1",
            name="Tech Licence",
            description=None,
            status="active",
            position="licensor",
            created_at=_NOW,
        )

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "create_deal", fake_create)
    monkeypatch.setattr(settings_api.settings_repo, "get_deal", fake_get)

    resp = client.post(
        "/deals",
        json={"client_id": "client-1", "name": "Tech Licence", "position": "licensor"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "deal-1"
    assert body["position"] == "licensor"


def test_create_deal_rejects_bad_position() -> None:
    resp = client.post(
        "/deals", json={"client_id": "client-1", "name": "X", "position": "overlord"}
    )
    assert resp.status_code == 422


def test_create_contract_type_returns_stored(monkeypatch: Any) -> None:
    async def fake_create(_conn: Any, payload: Any) -> str:
        return "ct-1"

    async def fake_get(_conn: Any, contract_type_id: str) -> StoredContractType:
        return StoredContractType(id=contract_type_id, name="NDA", is_default=True, created_at=_NOW)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "create_contract_type", fake_create)
    monkeypatch.setattr(settings_api.settings_repo, "get_contract_type", fake_get)

    resp = client.post("/contract-types", json={"name": "NDA", "is_default": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "ct-1"
    assert body["name"] == "NDA"
    assert body["is_default"] is True


def test_create_contract_returns_stored(monkeypatch: Any) -> None:
    async def fake_create(_conn: Any, payload: Any) -> str:
        return "contract-1"

    async def fake_get(_conn: Any, contract_id: str) -> StoredContract:
        return StoredContract(
            id=contract_id,
            client_id="client-1",
            deal_id="deal-1",
            contract_type_id="ct-1",
            name="Licence Agreement",
            status="drafting",
            style_config={},
            created_at=_NOW,
        )

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "create_contract", fake_create)
    monkeypatch.setattr(settings_api.settings_repo, "get_contract", fake_get)

    resp = client.post(
        "/contracts",
        json={
            "client_id": "client-1",
            "deal_id": "deal-1",
            "contract_type_id": "ct-1",
            "name": "Licence Agreement",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "contract-1"
    assert body["status"] == "drafting"
    assert body["style_config"] == {}


def test_get_contract_not_found(monkeypatch: Any) -> None:
    async def fake_get(_conn: Any, _contract_id: str) -> None:
        return None

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "get_contract", fake_get)

    resp = client.get("/contracts/missing")
    assert resp.status_code == 404


def test_get_contract_found(monkeypatch: Any) -> None:
    async def fake_get(_conn: Any, contract_id: str) -> StoredContract:
        return StoredContract(
            id=contract_id,
            client_id="client-1",
            deal_id="deal-1",
            contract_type_id="ct-1",
            name="Offtake Agreement",
            status="under negotiation",
            style_config={"font": "Calibri"},
            created_at=_NOW,
        )

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api.settings_repo, "get_contract", fake_get)

    resp = client.get("/contracts/contract-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "contract-1"
    assert body["style_config"] == {"font": "Calibri"}
