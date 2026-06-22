"""Persistence for the settings entities (asyncpg) — clients, deals,
contract_types, contracts (F01/F01b/F02). DB integration only, no business logic.

The FK chain is enforced by the schema (deals.client_id, contracts.client_id /
deal_id / contract_type_id): a create with a non-existent parent id is rejected by
Postgres, not re-checked here. Creates return the generated id as str; the route
reads the row back so server defaults (status, created_at, JSONB) are reflected.
"""

from __future__ import annotations

import json
from typing import Any

from backend.models.settings import (
    ClientCreate,
    ContractCreate,
    ContractTypeCreate,
    DealCreate,
    StoredClient,
    StoredContract,
    StoredContractType,
    StoredDeal,
)

# --- clients ---------------------------------------------------------------

_INSERT_CLIENT = """
INSERT INTO clients (name, relationship_type, status, notes)
VALUES ($1, $2, $3, $4)
RETURNING id
"""

_SELECT_CLIENT = """
SELECT id, name, relationship_type, status, notes, created_at
FROM clients
"""

_LIST_CLIENTS = _SELECT_CLIENT + "ORDER BY created_at"
_GET_CLIENT = _SELECT_CLIENT + "WHERE id = $1"


def _to_client(record: Any) -> StoredClient:
    return StoredClient(
        id=str(record["id"]),
        name=record["name"],
        relationship_type=record["relationship_type"],
        status=record["status"],
        notes=record["notes"],
        created_at=record["created_at"],
    )


async def create_client(conn: Any, payload: ClientCreate) -> str:
    new_id = await conn.fetchval(
        _INSERT_CLIENT,
        payload.name,
        payload.relationship_type,
        payload.status,
        payload.notes,
    )
    return str(new_id)


async def list_clients(conn: Any) -> list[StoredClient]:
    records = await conn.fetch(_LIST_CLIENTS)
    return [_to_client(r) for r in records]


async def get_client(conn: Any, client_id: str) -> StoredClient | None:
    record = await conn.fetchrow(_GET_CLIENT, client_id)
    return _to_client(record) if record is not None else None


# --- deals -----------------------------------------------------------------

_INSERT_DEAL = """
INSERT INTO deals (client_id, name, description, status, position)
VALUES ($1, $2, $3, $4, $5)
RETURNING id
"""

_SELECT_DEAL = """
SELECT id, client_id, name, description, status, position, created_at
FROM deals
"""

_LIST_DEALS = _SELECT_DEAL + "ORDER BY created_at"
_GET_DEAL = _SELECT_DEAL + "WHERE id = $1"


def _to_deal(record: Any) -> StoredDeal:
    return StoredDeal(
        id=str(record["id"]),
        client_id=str(record["client_id"]),
        name=record["name"],
        description=record["description"],
        status=record["status"],
        position=record["position"],
        created_at=record["created_at"],
    )


async def create_deal(conn: Any, payload: DealCreate) -> str:
    new_id = await conn.fetchval(
        _INSERT_DEAL,
        payload.client_id,
        payload.name,
        payload.description,
        payload.status,
        payload.position,
    )
    return str(new_id)


async def list_deals(conn: Any) -> list[StoredDeal]:
    records = await conn.fetch(_LIST_DEALS)
    return [_to_deal(r) for r in records]


async def get_deal(conn: Any, deal_id: str) -> StoredDeal | None:
    record = await conn.fetchrow(_GET_DEAL, deal_id)
    return _to_deal(record) if record is not None else None


# --- contract_types --------------------------------------------------------

_INSERT_CONTRACT_TYPE = """
INSERT INTO contract_types (name, is_default)
VALUES ($1, $2)
RETURNING id
"""

_SELECT_CONTRACT_TYPE = """
SELECT id, name, is_default, created_at
FROM contract_types
"""

_LIST_CONTRACT_TYPES = _SELECT_CONTRACT_TYPE + "ORDER BY created_at"
_GET_CONTRACT_TYPE = _SELECT_CONTRACT_TYPE + "WHERE id = $1"


def _to_contract_type(record: Any) -> StoredContractType:
    return StoredContractType(
        id=str(record["id"]),
        name=record["name"],
        is_default=record["is_default"],
        created_at=record["created_at"],
    )


async def create_contract_type(conn: Any, payload: ContractTypeCreate) -> str:
    new_id = await conn.fetchval(_INSERT_CONTRACT_TYPE, payload.name, payload.is_default)
    return str(new_id)


async def list_contract_types(conn: Any) -> list[StoredContractType]:
    records = await conn.fetch(_LIST_CONTRACT_TYPES)
    return [_to_contract_type(r) for r in records]


async def get_contract_type(conn: Any, contract_type_id: str) -> StoredContractType | None:
    record = await conn.fetchrow(_GET_CONTRACT_TYPE, contract_type_id)
    return _to_contract_type(record) if record is not None else None


# --- contracts -------------------------------------------------------------

_INSERT_CONTRACT = """
INSERT INTO contracts
    (client_id, deal_id, contract_type_id, name, status,
     current_version_label, style_template_id, style_config, origin)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
RETURNING id
"""

_SELECT_CONTRACT = """
SELECT id, client_id, deal_id, contract_type_id, name, status,
       current_version_label, style_template_id, style_config, origin, created_at
FROM contracts
"""

_LIST_CONTRACTS = _SELECT_CONTRACT + "ORDER BY created_at"
_GET_CONTRACT = _SELECT_CONTRACT + "WHERE id = $1"


def _to_contract(record: Any) -> StoredContract:
    style_config = record["style_config"]
    if isinstance(style_config, str):
        style_config = json.loads(style_config)
    style_template_id = record["style_template_id"]
    return StoredContract(
        id=str(record["id"]),
        client_id=str(record["client_id"]),
        deal_id=str(record["deal_id"]),
        contract_type_id=str(record["contract_type_id"]),
        name=record["name"],
        status=record["status"],
        current_version_label=record["current_version_label"],
        style_template_id=str(style_template_id) if style_template_id is not None else None,
        style_config=style_config,
        origin=record["origin"],
        created_at=record["created_at"],
    )


async def create_contract(conn: Any, payload: ContractCreate) -> str:
    new_id = await conn.fetchval(
        _INSERT_CONTRACT,
        payload.client_id,
        payload.deal_id,
        payload.contract_type_id,
        payload.name,
        payload.status,
        payload.current_version_label,
        payload.style_template_id,
        json.dumps(payload.style_config),
        payload.origin,
    )
    return str(new_id)


async def list_contracts(conn: Any) -> list[StoredContract]:
    records = await conn.fetch(_LIST_CONTRACTS)
    return [_to_contract(r) for r in records]


async def get_contract(conn: Any, contract_id: str) -> StoredContract | None:
    record = await conn.fetchrow(_GET_CONTRACT, contract_id)
    return _to_contract(record) if record is not None else None
