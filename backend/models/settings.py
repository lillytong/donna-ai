"""Settings entities (F01/F01b/F02) — clients, deals, contract_types, contracts.

Each entity has a create-input model (validated request body) and a stored-output
model (read back from the DB, with server defaults populated). Enum fields mirror
the CHECK constraints in db/schema.sql exactly. Stored models accept the DB value
as a plain str rather than re-validating the enum on read (the DB is canonical).

`contracts.style_config` is an open JSONB blob whose shape is locked in DD-37; the
full StyleConfig Pydantic model belongs with F01c (style templates, Phase 1), so
here it is a passthrough dict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RelationshipType = Literal["counterparty", "partner", "licensee", "other"]
ClientStatus = Literal["active", "archived"]
DealStatus = Literal["active", "signed", "closed"]
DealPosition = Literal[
    "customer",
    "vendor",
    "buyer",
    "seller",
    "licensor",
    "licensee",
    "receiving_party",
    "disclosing_party",
]
ContractStatus = Literal["drafting", "under negotiation", "signed"]


class ClientCreate(BaseModel):
    name: str
    relationship_type: RelationshipType = "counterparty"
    status: ClientStatus = "active"
    notes: str | None = None


class StoredClient(BaseModel):
    id: str
    name: str
    relationship_type: str
    status: str
    notes: str | None = None
    created_at: datetime


class DealCreate(BaseModel):
    client_id: str
    name: str
    description: str | None = None
    status: DealStatus = "active"
    position: DealPosition | None = None


class StoredDeal(BaseModel):
    id: str
    client_id: str
    name: str
    description: str | None = None
    status: str
    position: str | None = None
    created_at: datetime


class ContractTypeCreate(BaseModel):
    name: str
    is_default: bool = False


class StoredContractType(BaseModel):
    id: str
    name: str
    is_default: bool
    created_at: datetime


class ContractCreate(BaseModel):
    client_id: str
    deal_id: str
    contract_type_id: str
    name: str
    status: ContractStatus = "drafting"
    current_version_label: str | None = None
    style_template_id: str | None = None
    style_config: dict[str, Any] = Field(default_factory=dict)


class StoredContract(BaseModel):
    id: str
    client_id: str
    deal_id: str
    contract_type_id: str
    name: str
    status: str
    current_version_label: str | None = None
    style_template_id: str | None = None
    style_config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
