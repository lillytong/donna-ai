"""Settings CRUD routes: request parsing, response shape, status codes.

The DB and repo boundaries are mocked — no live database. TestClient is used
without its context manager so the app lifespan (pool open/close) never runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
from backend.api import settings as settings_api
from backend.main import app
from backend.models.audit import AuditEvent, StoredAuditEvent
from backend.models.settings import (
    ContractDeletion,
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


def _dummy_stored_event(event: AuditEvent) -> StoredAuditEvent:
    return StoredAuditEvent(
        id="audit-1",
        event_type=event.event_type,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        actor=event.actor,
        payload=event.payload,
        created_at=_NOW,
    )


async def _noop_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
    return _dummy_stored_event(event)


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
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
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
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
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
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
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
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
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


def test_create_client_records_audit_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_create(_conn: Any, payload: Any) -> str:
        return "client-7"

    async def fake_get(_conn: Any, client_id: str) -> StoredClient:
        return StoredClient(
            id=client_id,
            name="Acme",
            relationship_type="partner",
            status="active",
            notes=None,
            created_at=_NOW,
        )

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return _dummy_stored_event(event)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", capture_record)
    monkeypatch.setattr(settings_api.settings_repo, "create_client", fake_create)
    monkeypatch.setattr(settings_api.settings_repo, "get_client", fake_get)

    resp = client.post("/clients", json={"name": "Acme", "relationship_type": "partner"})
    assert resp.status_code == 200
    event = captured["event"]
    assert event.event_type == "created"
    assert event.entity_type == "client"
    assert event.entity_id == "client-7"


def test_update_client_returns_stored(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_update(_conn: Any, client_id: str, payload: Any) -> StoredClient:
        captured["payload"] = payload
        return StoredClient(
            id=client_id,
            name="Acme Renamed",
            relationship_type="partner",
            status="active",
            notes=None,
            created_at=_NOW,
        )

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return _dummy_stored_event(event)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", capture_record)
    monkeypatch.setattr(settings_api.settings_repo, "update_client", fake_update)

    resp = client.patch("/clients/client-1", json={"name": "Acme Renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Acme Renamed"
    assert captured["payload"].model_dump(exclude_unset=True) == {"name": "Acme Renamed"}
    assert captured["event"].event_type == "updated"
    assert captured["event"].entity_type == "client"


def test_update_client_not_found(monkeypatch: Any) -> None:
    async def fake_update(_conn: Any, _client_id: str, _payload: Any) -> None:
        return None

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "update_client", fake_update)

    resp = client.patch("/clients/missing", json={"name": "X"})
    assert resp.status_code == 404


def test_update_deal_rejects_bad_status() -> None:
    resp = client.patch("/deals/deal-1", json={"status": "overlord"})
    assert resp.status_code == 422


def test_delete_client_no_content(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_delete(_conn: Any, _client_id: str) -> bool:
        return True

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return _dummy_stored_event(event)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", capture_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_client", fake_delete)

    resp = client.delete("/clients/client-1")
    assert resp.status_code == 204
    assert resp.content == b""
    assert captured["event"].event_type == "deleted"
    assert captured["event"].entity_type == "client"


def test_delete_client_not_found(monkeypatch: Any) -> None:
    async def fake_delete(_conn: Any, _client_id: str) -> bool:
        return False

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_client", fake_delete)

    resp = client.delete("/clients/missing")
    assert resp.status_code == 404


def test_delete_client_fk_guard_returns_409(monkeypatch: Any) -> None:
    async def fake_delete(_conn: Any, _client_id: str) -> bool:
        raise asyncpg.ForeignKeyViolationError("deals reference this client")

    async def fake_count(_conn: Any, _client_id: str) -> int:
        return 3

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_client", fake_delete)
    monkeypatch.setattr(settings_api.settings_repo, "count_deals_for_client", fake_count)

    resp = client.delete("/clients/client-1")
    assert resp.status_code == 409
    assert "3 deals" in resp.json()["detail"]


def test_delete_deal_fk_guard_returns_409(monkeypatch: Any) -> None:
    async def fake_delete(_conn: Any, _deal_id: str) -> bool:
        raise asyncpg.ForeignKeyViolationError("contracts reference this deal")

    async def fake_count(_conn: Any, _deal_id: str) -> int:
        return 1

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_deal", fake_delete)
    monkeypatch.setattr(settings_api.settings_repo, "count_contracts_for_deal", fake_count)

    resp = client.delete("/deals/deal-1")
    assert resp.status_code == 409
    assert "1 contract " in resp.json()["detail"]


def test_delete_contract_type_fk_guard_returns_409(monkeypatch: Any) -> None:
    async def fake_delete(_conn: Any, _ct_id: str) -> bool:
        raise asyncpg.ForeignKeyViolationError("contracts reference this type")

    async def fake_count(_conn: Any, _ct_id: str) -> int:
        return 2

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_contract_type", fake_delete)
    monkeypatch.setattr(settings_api.settings_repo, "count_contracts_for_contract_type", fake_count)

    resp = client.delete("/contract-types/ct-1")
    assert resp.status_code == 409
    assert "2 contracts" in resp.json()["detail"]


def test_update_contract_type_returns_stored(monkeypatch: Any) -> None:
    async def fake_update(_conn: Any, ct_id: str, _payload: Any) -> StoredContractType:
        return StoredContractType(id=ct_id, name="MSA", is_default=False, created_at=_NOW)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "update_contract_type", fake_update)

    resp = client.patch("/contract-types/ct-1", json={"name": "MSA"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "MSA"


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


def test_update_contract_returns_stored(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_update(_conn: Any, contract_id: str, payload: Any) -> StoredContract:
        captured["payload"] = payload
        return StoredContract(
            id=contract_id,
            client_id="client-1",
            deal_id="deal-1",
            contract_type_id="ct-2",
            name="Renamed Agreement",
            status="under negotiation",
            style_config={},
            created_at=_NOW,
        )

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return _dummy_stored_event(event)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", capture_record)
    monkeypatch.setattr(settings_api.settings_repo, "update_contract", fake_update)

    resp = client.patch(
        "/contracts/contract-1",
        json={
            "name": "Renamed Agreement",
            "status": "under negotiation",
            "contract_type_id": "ct-2",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed Agreement"
    assert body["status"] == "under negotiation"
    assert captured["payload"].model_dump(exclude_unset=True) == {
        "name": "Renamed Agreement",
        "status": "under negotiation",
        "contract_type_id": "ct-2",
    }
    assert captured["event"].event_type == "updated"
    assert captured["event"].entity_type == "contract"


def test_update_contract_not_found(monkeypatch: Any) -> None:
    async def fake_update(_conn: Any, _contract_id: str, _payload: Any) -> None:
        return None

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "update_contract", fake_update)

    resp = client.patch("/contracts/missing", json={"name": "X"})
    assert resp.status_code == 404


def test_update_contract_rejects_bad_status() -> None:
    resp = client.patch("/contracts/contract-1", json={"status": "ratified"})
    assert resp.status_code == 422


def test_delete_contract_no_content_records_cascade_counts(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_delete(_conn: Any, _contract_id: str) -> ContractDeletion:
        return ContractDeletion(nodes=12, issues=3, issue_comments=7)

    async def capture_record(_conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        return _dummy_stored_event(event)

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", capture_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_contract", fake_delete)

    resp = client.delete("/contracts/contract-1")
    assert resp.status_code == 204
    assert resp.content == b""
    event = captured["event"]
    assert event.event_type == "deleted"
    assert event.entity_type == "contract"
    assert event.payload == {"nodes": 12, "issues": 3, "issue_comments": 7}


def test_delete_contract_not_found(monkeypatch: Any) -> None:
    async def fake_delete(_conn: Any, _contract_id: str) -> None:
        return None

    monkeypatch.setattr(settings_api, "acquire", _fake_acquire)
    monkeypatch.setattr(settings_api, "record_event", _noop_record)
    monkeypatch.setattr(settings_api.settings_repo, "delete_contract", fake_delete)

    resp = client.delete("/contracts/missing")
    assert resp.status_code == 404
