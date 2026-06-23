"""Issues entities (F06/F07/F08c/F09) — negotiation points and their append-only
comment threads.

Each entity has a create-input model (validated request body) and a stored-output
model (read back from the DB, with server defaults populated). Enum fields mirror
the CHECK constraints in db/schema.sql exactly. Stored models accept the DB value
as a plain str rather than re-validating the enum on read (the DB is canonical).

F06 semantic (SPEC §9): at creation the operator writes their own title and
positions only. Donna's analysis fields (recommended_position,
donna_counter_language, auto_flag, donna_research_citations) are NOT set here —
they are populated later by Phase-2 surfaces (F11/F28). On this surface the
operator flags whether WE raised the issue (`operator`, the default) or the
COUNTERPARTY raised it (`counterparty`); `donna` is reserved for the F28 auto-flag
path and is intentionally not accepted here. `node_id` null = a free-floating,
contract-level issue (F08c); it is mutable and can be anchored to a node later.

`IssueCreate.contract_id` and `CommentCreate.issue_id` default to None because the
parent id is carried in the URL path; the route overrides them from the path so
the URL is authoritative.

`decision` and `auto_flag` are open JSONB blobs whose shapes are documented in
SPEC §6; here they are passthrough dicts (full models belong with the Phase-2
features that populate them).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

IssueStatus = Literal["open", "agreed", "deferred", "kicked", "dismissed"]
IssueInitiator = Literal["operator", "counterparty", "donna"]
IssueCreateInitiator = Literal["operator", "counterparty"]
IssueAuthority = Literal["within-operator-authority", "needs-principal"]
IssueCategory = Literal["commercial", "legal", "operational", "counterparty_proposed_edit"]
CommentActor = Literal["user", "ai", "principal"]


class IssueCreate(BaseModel):
    contract_id: str | None = None
    node_id: str | None = None
    title: str
    our_position: str | None = None
    their_position: str | None = None
    options_on_table: str | None = None
    category: IssueCategory = "commercial"
    authority: IssueAuthority = "within-operator-authority"
    needs_legal_review: bool = False
    initiator: IssueCreateInitiator = "operator"


class StoredIssue(BaseModel):
    id: str
    contract_id: str
    node_id: str | None = None
    title: str
    our_position: str | None = None
    their_position: str | None = None
    options_on_table: str | None = None
    recommended_position: str | None = None
    donna_counter_language: str | None = None
    status: str
    initiator: str
    auto_flag: dict[str, Any] | None = None
    authority: str
    needs_legal_review: bool
    category: str
    counterparty_revision_session_id: str | None = None
    opened_in_snapshot_id: str | None = None
    resolved_in_snapshot_id: str | None = None
    decision: dict[str, Any] | None = None
    donna_research_citations: Any | None = None
    impact: str | None = None
    priority: int | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class IssueStatusUpdate(BaseModel):
    status: IssueStatus
    decision: dict[str, Any] | None = None


class CommentCreate(BaseModel):
    issue_id: str | None = None
    actor: CommentActor
    content: str


class StoredComment(BaseModel):
    id: str
    issue_id: str
    actor: str
    content: str
    snapshot_id: str | None = None
    created_at: datetime
