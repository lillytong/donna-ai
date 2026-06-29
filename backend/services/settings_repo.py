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
    ClientUpdate,
    ContractCreate,
    ContractDeletion,
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
from backend.services.lineage import derive_status_for_contracts

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


_CLIENT_RETURNING = "id, name, relationship_type, status, notes, created_at"


async def update_client(conn: Any, client_id: str, payload: ClientUpdate) -> StoredClient | None:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return await get_client(conn, client_id)
    cols = list(fields.keys())
    assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
    sql = f"UPDATE clients SET {assignments} WHERE id = $1 RETURNING {_CLIENT_RETURNING}"
    record = await conn.fetchrow(sql, client_id, *(fields[col] for col in cols))
    return _to_client(record) if record is not None else None


async def delete_client(conn: Any, client_id: str) -> bool:
    deleted = await conn.fetchval("DELETE FROM clients WHERE id = $1 RETURNING id", client_id)
    return deleted is not None


async def count_deals_for_client(conn: Any, client_id: str) -> int:
    count = await conn.fetchval("SELECT count(*) FROM deals WHERE client_id = $1", client_id)
    return int(count)


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


_DEAL_RETURNING = "id, client_id, name, description, status, position, created_at"


async def update_deal(conn: Any, deal_id: str, payload: DealUpdate) -> StoredDeal | None:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return await get_deal(conn, deal_id)
    cols = list(fields.keys())
    assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
    sql = f"UPDATE deals SET {assignments} WHERE id = $1 RETURNING {_DEAL_RETURNING}"
    record = await conn.fetchrow(sql, deal_id, *(fields[col] for col in cols))
    return _to_deal(record) if record is not None else None


async def delete_deal(conn: Any, deal_id: str) -> bool:
    deleted = await conn.fetchval("DELETE FROM deals WHERE id = $1 RETURNING id", deal_id)
    return deleted is not None


async def count_contracts_for_deal(conn: Any, deal_id: str) -> int:
    count = await conn.fetchval("SELECT count(*) FROM contracts WHERE deal_id = $1", deal_id)
    return int(count)


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


_CONTRACT_TYPE_RETURNING = "id, name, is_default, created_at"


async def update_contract_type(
    conn: Any, contract_type_id: str, payload: ContractTypeUpdate
) -> StoredContractType | None:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return await get_contract_type(conn, contract_type_id)
    cols = list(fields.keys())
    assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
    sql = (
        f"UPDATE contract_types SET {assignments} WHERE id = $1 "
        f"RETURNING {_CONTRACT_TYPE_RETURNING}"
    )
    record = await conn.fetchrow(sql, contract_type_id, *(fields[col] for col in cols))
    return _to_contract_type(record) if record is not None else None


async def delete_contract_type(conn: Any, contract_type_id: str) -> bool:
    deleted = await conn.fetchval(
        "DELETE FROM contract_types WHERE id = $1 RETURNING id", contract_type_id
    )
    return deleted is not None


async def count_contracts_for_contract_type(conn: Any, contract_type_id: str) -> int:
    count = await conn.fetchval(
        "SELECT count(*) FROM contracts WHERE contract_type_id = $1", contract_type_id
    )
    return int(count)


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
    contracts = [_to_contract(r) for r in records]
    # F27/DD-74: attach the derived lifecycle badge to every card in ONE set-based
    # query (no N+1) so My Contracts + home can render "where are we" per contract.
    if contracts:
        badges = await derive_status_for_contracts(conn, [c.id for c in contracts])
        for c in contracts:
            c.badge = badges.get(c.id)
    return contracts


async def get_contract(conn: Any, contract_id: str) -> StoredContract | None:
    record = await conn.fetchrow(_GET_CONTRACT, contract_id)
    return _to_contract(record) if record is not None else None


# DD-72: the clean-copy export route stamps this on every export so Mark-as-sent
# can detect "edited since last export" drift (node.updated_at > last_export_at).
async def touch_last_export(conn: Any, contract_id: str) -> None:
    await conn.execute("UPDATE contracts SET last_export_at = now() WHERE id = $1", contract_id)


_CONTRACT_RETURNING = (
    "id, client_id, deal_id, contract_type_id, name, status, "
    "current_version_label, style_template_id, style_config, origin, created_at"
)


async def update_contract(
    conn: Any, contract_id: str, payload: ContractUpdate
) -> StoredContract | None:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return await get_contract(conn, contract_id)
    cols = list(fields.keys())
    assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
    sql = f"UPDATE contracts SET {assignments} WHERE id = $1 RETURNING {_CONTRACT_RETURNING}"
    record = await conn.fetchrow(sql, contract_id, *(fields[col] for col in cols))
    return _to_contract(record) if record is not None else None


async def _exec_count(conn: Any, sql: str, *args: Any) -> int:
    # Parses the trailing row count from an asyncpg command tag, e.g. "DELETE 5"
    # or "UPDATE 2" — works for both the DELETE cascades and the SET NULL updates.
    status = await conn.execute(sql, *args)
    return int(status.split()[-1])


# Hard delete of a contract scoped to its OWN subtree (DD-63). The contract owns
# its content (SPEC §2.3), so its rows are cascaded manually in FK order (children
# before parents) inside ONE transaction — a partial wipe can never commit. But
# two entity types are DEAL-scoped, not contract-scoped, and must survive so the
# deal's sibling contracts stay valid:
#   * cross_references — a sibling contract's clause may reference one of this
#     contract's nodes. source_node_id/source_contract_id are NOT NULL, so a ref
#     whose SOURCE is in this contract belongs to it and is DELETED; a ref in a
#     sibling whose TARGET points INTO this contract is SET NULL (target_node_id +
#     target_contract_id), keeping the sibling clause with a dangling-link marker.
#   * defined_terms — deal-scoped, shared across the deal's contracts; NEVER
#     deleted. Only source_node_id is SET NULL where it pointed at a deleted node,
#     so the term survives without a dangling FK.
# Children are removed before the contract regardless of whether it exists; a
# 0-row contract delete means not-found (returns None) and the empty child wipes
# roll up harmlessly. (Comments were removed in DD-67, so no comment cascade.)
async def delete_contract(conn: Any, contract_id: str) -> ContractDeletion | None:
    # Rows hanging off this contract's nodes (FK node_id) are scoped via the node
    # subquery so they clear before the nodes themselves FK-violate.
    nodes_subq = "(SELECT id FROM nodes WHERE contract_id = $1)"
    async with conn.transaction():
        # 1. Donna recommendations + brainstorm summaries both hang off this contract's
        #    issues (FK issue_id) — clear before the issues they reference (DD-63/DD-77).
        await _exec_count(
            conn,
            "DELETE FROM donna_recommendations WHERE issue_id IN "
            "(SELECT id FROM issues WHERE contract_id = $1)",
            contract_id,
        )
        await _exec_count(
            conn,
            "DELETE FROM brainstorm_summaries WHERE issue_id IN "
            "(SELECT id FROM issues WHERE contract_id = $1)",
            contract_id,
        )
        # 2. Issues hold FKs into nodes + snapshots, so delete them before either.
        #    Issues also FK counterparty_revision_session_id, so they MUST precede the
        #    revision-session wipe below (a session can't drop while an issue references it).
        issues = await _exec_count(conn, "DELETE FROM issues WHERE contract_id = $1", contract_id)
        # 2b. F03b Mode B revision staging (DD-28/DD-64): hunks → changes → sessions.
        #     Ordered AFTER issues (which reference sessions) and BEFORE nodes +
        #     contract_snapshots (changes FK nodes; sessions FK baseline_snapshot_id).
        await _exec_count(
            conn,
            "DELETE FROM counterparty_revision_hunks WHERE change_id IN "
            "(SELECT id FROM counterparty_revision_changes WHERE session_id IN "
            "(SELECT id FROM counterparty_revision_sessions WHERE contract_id = $1))",
            contract_id,
        )
        await _exec_count(
            conn,
            "DELETE FROM counterparty_revision_changes WHERE session_id IN "
            "(SELECT id FROM counterparty_revision_sessions WHERE contract_id = $1)",
            contract_id,
        )
        # Mode B Phase-1 role overrides FK sessions, so clear before the session wipe.
        await _exec_count(
            conn,
            "DELETE FROM counterparty_revision_node_overrides WHERE session_id IN "
            "(SELECT id FROM counterparty_revision_sessions WHERE contract_id = $1)",
            contract_id,
        )
        await _exec_count(
            conn, "DELETE FROM counterparty_revision_sessions WHERE contract_id = $1", contract_id
        )
        # 3. Donna conversation state (messages FK conversation_id → conversations).
        await _exec_count(
            conn,
            "DELETE FROM donna_messages WHERE conversation_id IN "
            "(SELECT id FROM donna_conversations WHERE contract_id = $1)",
            contract_id,
        )
        await _exec_count(
            conn, "DELETE FROM donna_conversations WHERE contract_id = $1", contract_id
        )
        # 4. Node-scoped children (FK node_id) — clear before the nodes.
        #    node_images (0018) has ON DELETE CASCADE from nodes, so no explicit
        #    delete is needed here — images are wiped automatically when nodes go.
        await _exec_count(
            conn, f"DELETE FROM node_embeddings WHERE node_id IN {nodes_subq}", contract_id
        )
        await _exec_count(
            conn, f"DELETE FROM parameter_references WHERE node_id IN {nodes_subq}", contract_id
        )
        footnotes = await _exec_count(
            conn, f"DELETE FROM footnotes WHERE node_id IN {nodes_subq}", contract_id
        )
        node_versions = await _exec_count(
            conn, f"DELETE FROM node_versions WHERE node_id IN {nodes_subq}", contract_id
        )
        # 5. cross_references (DD-63): DELETE this contract's own (source) refs;
        #    SET NULL the target of any sibling ref that pointed INTO this contract.
        #    Both must precede the node/contract deletes to avoid FK violations.
        cross_references_deleted = await _exec_count(
            conn, "DELETE FROM cross_references WHERE source_contract_id = $1", contract_id
        )
        cross_references_nulled = await _exec_count(
            conn,
            "UPDATE cross_references SET target_node_id = NULL, target_contract_id = NULL "
            "WHERE target_contract_id = $1",
            contract_id,
        )
        # 6. defined_terms (DD-63): NEVER deleted (deal-scoped); only null a
        #    source_node_id that pointed at a node about to be deleted.
        defined_terms_nulled = await _exec_count(
            conn,
            f"UPDATE defined_terms SET source_node_id = NULL WHERE source_node_id IN {nodes_subq}",
            contract_id,
        )
        # 7. Now the nodes, then the contract's own snapshots/pointers, then the row.
        nodes = await _exec_count(conn, "DELETE FROM nodes WHERE contract_id = $1", contract_id)
        await _exec_count(conn, "DELETE FROM snapshot_pointers WHERE contract_id = $1", contract_id)
        await _exec_count(
            conn, "DELETE FROM contract_snapshots WHERE contract_id = $1", contract_id
        )
        # 8. contract_deal_brief (F37/DD-95) — FK contract_id REFERENCES contracts(id),
        #    no cascade; must be removed before the contract row itself (DD-63).
        await _exec_count(
            conn, "DELETE FROM contract_deal_brief WHERE contract_id = $1", contract_id
        )
        contracts = await _exec_count(conn, "DELETE FROM contracts WHERE id = $1", contract_id)
    if contracts == 0:
        return None
    return ContractDeletion(
        nodes=nodes,
        issues=issues,
        footnotes=footnotes,
        node_versions=node_versions,
        cross_references_deleted=cross_references_deleted,
        cross_references_nulled=cross_references_nulled,
        defined_terms_nulled=defined_terms_nulled,
    )
