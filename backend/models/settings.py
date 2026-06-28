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

from backend.models.lineage import ContractBadge

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
ContractOrigin = Literal["us", "our_legal", "counterparty"]


class OperatorOrganization(BaseModel):
    """F25: the operator's org identity (DD-44). `organization_name` is a DB-backed,
    editable override over the DONNA_OPERATOR_ORG_NAME config value (`editable` is True).
    `export_author` is the resolved redline/export author — explicit DONNA_REDLINE_AUTHOR
    if set, else the org name, else the neutral default; never blank, never "Donna"."""

    organization_name: str
    export_author: str
    editable: bool


class OperatorOrganizationUpdate(BaseModel):
    """PUT /organization body — the editable org-name override (F25, DD-44)."""

    organization_name: str


class ClientCreate(BaseModel):
    name: str
    relationship_type: RelationshipType = "counterparty"
    status: ClientStatus = "active"
    notes: str | None = None


class ClientUpdate(BaseModel):
    name: str | None = None
    relationship_type: RelationshipType | None = None
    status: ClientStatus | None = None
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


class DealUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: DealStatus | None = None
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


class ContractTypeUpdate(BaseModel):
    name: str | None = None
    is_default: bool | None = None


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
    origin: ContractOrigin | None = None


class ContractUpdate(BaseModel):
    name: str | None = None
    contract_type_id: str | None = None
    status: ContractStatus | None = None
    origin: ContractOrigin | None = None


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
    origin: str | None = None
    created_at: datetime
    # Derived lifecycle badge (F27/DD-75) — populated on the LIST read (My Contracts
    # + home) via the set-based resolver so every card shows its "where are we"
    # state; None on single-contract reads (the cockpit uses GET …/lineage instead).
    badge: ContractBadge | None = None


# A contract owns its content, so deleting it cascades through the rows beneath
# it; these are the per-table counts of what was removed, recorded in the audit
# payload (the delete itself is one atomic transaction). Per DD-63 the contract's
# OWN subtree is cascaded, while deal-shared rows are preserved-and-nulled rather
# than deleted: `cross_references_deleted` are this contract's own outgoing refs
# (deleted), `cross_references_nulled` are sibling contracts' refs that pointed
# INTO a deleted node (target SET NULL, clause kept), and `defined_terms_nulled`
# are deal-scoped terms whose source_node_id pointed at a deleted node (term
# survives, source SET NULL).
class ContractDeletion(BaseModel):
    nodes: int
    issues: int
    footnotes: int
    node_versions: int
    cross_references_deleted: int
    cross_references_nulled: int
    defined_terms_nulled: int
