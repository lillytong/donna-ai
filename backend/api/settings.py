"""Settings CRUD routes (F01/F01b/F02) — thin (CLAUDE.md): validate, call a
service, return. All logic lives in services/settings_repo.py.

Create endpoints insert then read the row back on the same connection so the
response carries server-populated defaults (status, created_at, JSONB). The FK
chain is enforced by the schema; a bad parent id surfaces as a DB error.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, Response

from backend.config.settings import get_settings
from backend.db import acquire
from backend.models.audit import EVENT_CREATED, EVENT_UPDATED, AuditEvent
from backend.models.settings import (
    ClientCreate,
    ClientUpdate,
    ContractCreate,
    ContractTypeCreate,
    ContractTypeUpdate,
    ContractUpdate,
    DealCreate,
    DealUpdate,
    StoredClient,
    StoredContract,
    StoredContractType,
    StoredDeal,
)
from backend.services import settings_repo
from backend.services.audit_repo import record_event

router = APIRouter()

# Delete policy: clients/deals/contract_types are hard-deleted but FK-guarded —
# a referenced row is refused with 409 rather than orphaning children. Contracts
# are the exception: a contract OWNS its content (SPEC §2.3), so its delete
# cascades (issue_comments → issues → nodes → contract) in one transaction. The
# client `status` archive (clients only) is a separate concept, not touched here.
EVENT_DELETED = "deleted"


async def _audit(
    conn: object,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, object] | None = None,
) -> None:
    await record_event(
        conn,
        AuditEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=get_settings().operator_actor,
            payload=payload,
        ),
    )


# --- clients ---------------------------------------------------------------


@router.post("/clients", response_model=StoredClient)
async def create_client(payload: ClientCreate) -> StoredClient:
    async with acquire() as conn:
        new_id = await settings_repo.create_client(conn, payload)
        stored = await settings_repo.get_client(conn, new_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_CREATED,
                entity_type="client",
                entity_id=new_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
    assert stored is not None  # just inserted
    return stored


@router.get("/clients", response_model=list[StoredClient])
async def list_clients() -> list[StoredClient]:
    async with acquire() as conn:
        return await settings_repo.list_clients(conn)


@router.patch("/clients/{client_id}", response_model=StoredClient)
async def update_client(client_id: str, payload: ClientUpdate) -> StoredClient:
    async with acquire() as conn:
        stored = await settings_repo.update_client(conn, client_id, payload)
        if stored is None:
            raise HTTPException(status_code=404, detail="client not found")
        await _audit(conn, EVENT_UPDATED, "client", client_id)
    return stored


@router.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: str) -> Response:
    async with acquire() as conn:
        try:
            deleted = await settings_repo.delete_client(conn, client_id)
        except asyncpg.ForeignKeyViolationError:
            count = await settings_repo.count_deals_for_client(conn, client_id)
            raise HTTPException(
                status_code=409,
                detail=f"Can't delete: {count} {'deal' if count == 1 else 'deals'} "
                "reference this client.",
            ) from None
        if not deleted:
            raise HTTPException(status_code=404, detail="client not found")
        await _audit(conn, EVENT_DELETED, "client", client_id)
    return Response(status_code=204)


# --- deals -----------------------------------------------------------------


@router.post("/deals", response_model=StoredDeal)
async def create_deal(payload: DealCreate) -> StoredDeal:
    async with acquire() as conn:
        new_id = await settings_repo.create_deal(conn, payload)
        stored = await settings_repo.get_deal(conn, new_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_CREATED,
                entity_type="deal",
                entity_id=new_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
    assert stored is not None  # just inserted
    return stored


@router.get("/deals", response_model=list[StoredDeal])
async def list_deals() -> list[StoredDeal]:
    async with acquire() as conn:
        return await settings_repo.list_deals(conn)


@router.patch("/deals/{deal_id}", response_model=StoredDeal)
async def update_deal(deal_id: str, payload: DealUpdate) -> StoredDeal:
    async with acquire() as conn:
        stored = await settings_repo.update_deal(conn, deal_id, payload)
        if stored is None:
            raise HTTPException(status_code=404, detail="deal not found")
        await _audit(conn, EVENT_UPDATED, "deal", deal_id)
    return stored


@router.delete("/deals/{deal_id}", status_code=204)
async def delete_deal(deal_id: str) -> Response:
    async with acquire() as conn:
        try:
            deleted = await settings_repo.delete_deal(conn, deal_id)
        except asyncpg.ForeignKeyViolationError:
            count = await settings_repo.count_contracts_for_deal(conn, deal_id)
            raise HTTPException(
                status_code=409,
                detail=f"Can't delete: {count} {'contract' if count == 1 else 'contracts'} "
                "reference this deal.",
            ) from None
        if not deleted:
            raise HTTPException(status_code=404, detail="deal not found")
        await _audit(conn, EVENT_DELETED, "deal", deal_id)
    return Response(status_code=204)


# --- contract_types --------------------------------------------------------


@router.post("/contract-types", response_model=StoredContractType)
async def create_contract_type(payload: ContractTypeCreate) -> StoredContractType:
    async with acquire() as conn:
        new_id = await settings_repo.create_contract_type(conn, payload)
        stored = await settings_repo.get_contract_type(conn, new_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_CREATED,
                entity_type="contract_type",
                entity_id=new_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
    assert stored is not None  # just inserted
    return stored


@router.get("/contract-types", response_model=list[StoredContractType])
async def list_contract_types() -> list[StoredContractType]:
    async with acquire() as conn:
        return await settings_repo.list_contract_types(conn)


@router.patch("/contract-types/{contract_type_id}", response_model=StoredContractType)
async def update_contract_type(
    contract_type_id: str, payload: ContractTypeUpdate
) -> StoredContractType:
    async with acquire() as conn:
        stored = await settings_repo.update_contract_type(conn, contract_type_id, payload)
        if stored is None:
            raise HTTPException(status_code=404, detail="contract type not found")
        await _audit(conn, EVENT_UPDATED, "contract_type", contract_type_id)
    return stored


@router.delete("/contract-types/{contract_type_id}", status_code=204)
async def delete_contract_type(contract_type_id: str) -> Response:
    async with acquire() as conn:
        try:
            deleted = await settings_repo.delete_contract_type(conn, contract_type_id)
        except asyncpg.ForeignKeyViolationError:
            count = await settings_repo.count_contracts_for_contract_type(conn, contract_type_id)
            raise HTTPException(
                status_code=409,
                detail=f"Can't delete: {count} {'contract' if count == 1 else 'contracts'} "
                "reference this contract type.",
            ) from None
        if not deleted:
            raise HTTPException(status_code=404, detail="contract type not found")
        await _audit(conn, EVENT_DELETED, "contract_type", contract_type_id)
    return Response(status_code=204)


# --- contracts -------------------------------------------------------------


@router.post("/contracts", response_model=StoredContract)
async def create_contract(payload: ContractCreate) -> StoredContract:
    async with acquire() as conn:
        new_id = await settings_repo.create_contract(conn, payload)
        stored = await settings_repo.get_contract(conn, new_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_CREATED,
                entity_type="contract",
                entity_id=new_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
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


@router.patch("/contracts/{contract_id}", response_model=StoredContract)
async def update_contract(contract_id: str, payload: ContractUpdate) -> StoredContract:
    async with acquire() as conn:
        stored = await settings_repo.update_contract(conn, contract_id, payload)
        if stored is None:
            raise HTTPException(status_code=404, detail="contract not found")
        await _audit(conn, EVENT_UPDATED, "contract", contract_id)
    return stored


@router.delete("/contracts/{contract_id}", status_code=204)
async def delete_contract(contract_id: str) -> Response:
    async with acquire() as conn:
        deleted = await settings_repo.delete_contract(conn, contract_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="contract not found")
        await _audit(conn, EVENT_DELETED, "contract", contract_id, payload=deleted.model_dump())
    return Response(status_code=204)
