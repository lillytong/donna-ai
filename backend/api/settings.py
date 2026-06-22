"""Settings CRUD routes (F01/F01b/F02) — thin (CLAUDE.md): validate, call a
service, return. All logic lives in services/settings_repo.py.

Create endpoints insert then read the row back on the same connection so the
response carries server-populated defaults (status, created_at, JSONB). The FK
chain is enforced by the schema; a bad parent id surfaces as a DB error.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
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
from backend.services import settings_repo

router = APIRouter()


# --- clients ---------------------------------------------------------------


@router.post("/clients", response_model=StoredClient)
async def create_client(payload: ClientCreate) -> StoredClient:
    async with acquire() as conn:
        new_id = await settings_repo.create_client(conn, payload)
        stored = await settings_repo.get_client(conn, new_id)
    assert stored is not None  # just inserted
    return stored


@router.get("/clients", response_model=list[StoredClient])
async def list_clients() -> list[StoredClient]:
    async with acquire() as conn:
        return await settings_repo.list_clients(conn)


# --- deals -----------------------------------------------------------------


@router.post("/deals", response_model=StoredDeal)
async def create_deal(payload: DealCreate) -> StoredDeal:
    async with acquire() as conn:
        new_id = await settings_repo.create_deal(conn, payload)
        stored = await settings_repo.get_deal(conn, new_id)
    assert stored is not None  # just inserted
    return stored


@router.get("/deals", response_model=list[StoredDeal])
async def list_deals() -> list[StoredDeal]:
    async with acquire() as conn:
        return await settings_repo.list_deals(conn)


# --- contract_types --------------------------------------------------------


@router.post("/contract-types", response_model=StoredContractType)
async def create_contract_type(payload: ContractTypeCreate) -> StoredContractType:
    async with acquire() as conn:
        new_id = await settings_repo.create_contract_type(conn, payload)
        stored = await settings_repo.get_contract_type(conn, new_id)
    assert stored is not None  # just inserted
    return stored


@router.get("/contract-types", response_model=list[StoredContractType])
async def list_contract_types() -> list[StoredContractType]:
    async with acquire() as conn:
        return await settings_repo.list_contract_types(conn)


# --- contracts -------------------------------------------------------------


@router.post("/contracts", response_model=StoredContract)
async def create_contract(payload: ContractCreate) -> StoredContract:
    async with acquire() as conn:
        new_id = await settings_repo.create_contract(conn, payload)
        stored = await settings_repo.get_contract(conn, new_id)
    assert stored is not None  # just inserted
    return stored


@router.get("/contracts", response_model=list[StoredContract])
async def list_contracts() -> list[StoredContract]:
    async with acquire() as conn:
        return await settings_repo.list_contracts(conn)


@router.get("/contracts/{contract_id}", response_model=StoredContract)
async def get_contract(contract_id: str) -> StoredContract:
    async with acquire() as conn:
        stored = await settings_repo.get_contract(conn, contract_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="contract not found")
    return stored
