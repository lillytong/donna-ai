"""Persistence for issues (F06/F07/F08c/F09) — asyncpg. DB integration only, no
business logic.

The FK chain (issues.contract_id, issues.node_id) is enforced by the schema: a
create with a non-existent parent id is rejected by Postgres, not re-checked here.
Creates return the generated id as str; the route reads the row back so server
defaults (status, initiator, created_at) are reflected. JSONB columns (auto_flag,
decision, donna_research_citations) come back as text under asyncpg's default
codec and are decoded on read.

On this surface the operator flags who raised the issue (`operator` or
`counterparty`), carried in on the create payload; Donna's analysis columns are
left null at creation. A status that is not 'open' is terminal, so `resolved_at`
is stamped on transition to a terminal status and cleared if an issue is reopened.
The description (title + position text) is editable post-creation (DD-67).
"""

from __future__ import annotations

import json
from typing import Any

from backend.models.issues import (
    IssueCreate,
    IssueEditRequest,
    IssueStatusUpdate,
    StoredIssue,
)

# --- issues ----------------------------------------------------------------

_INSERT_ISSUE = """
INSERT INTO issues
    (contract_id, node_id, title, our_position, their_position,
     options_on_table, category, authority, needs_legal_review, initiator)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
RETURNING id
"""

_SELECT_ISSUE = """
SELECT id, contract_id, node_id, title, our_position, their_position,
       options_on_table, recommended_position, donna_counter_language, status,
       initiator, auto_flag, authority, needs_legal_review, category,
       counterparty_revision_session_id, opened_in_snapshot_id,
       resolved_in_snapshot_id, decision, donna_research_citations, impact,
       priority, created_at, resolved_at
FROM issues
"""

_GET_ISSUE = _SELECT_ISSUE + "WHERE id = $1"
_LIST_ISSUES = _SELECT_ISSUE + "WHERE contract_id = $1 ORDER BY created_at"
_LIST_ISSUES_BY_STATUS = (
    _SELECT_ISSUE + "WHERE contract_id = $1 AND status = $2 ORDER BY created_at"
)

_UPDATE_ISSUE_STATUS = """
UPDATE issues
SET status = $2,
    decision = $3::jsonb,
    resolved_at = CASE WHEN $2 = 'open' THEN NULL ELSE now() END
WHERE id = $1
RETURNING id
"""


def _maybe_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _to_issue(record: Any) -> StoredIssue:
    node_id = record["node_id"]
    crsi = record["counterparty_revision_session_id"]
    opened = record["opened_in_snapshot_id"]
    resolved = record["resolved_in_snapshot_id"]
    return StoredIssue(
        id=str(record["id"]),
        contract_id=str(record["contract_id"]),
        node_id=str(node_id) if node_id is not None else None,
        title=record["title"],
        our_position=record["our_position"],
        their_position=record["their_position"],
        options_on_table=record["options_on_table"],
        recommended_position=record["recommended_position"],
        donna_counter_language=record["donna_counter_language"],
        status=record["status"],
        initiator=record["initiator"],
        auto_flag=_maybe_json(record["auto_flag"]),
        authority=record["authority"],
        needs_legal_review=record["needs_legal_review"],
        category=record["category"],
        counterparty_revision_session_id=str(crsi) if crsi is not None else None,
        opened_in_snapshot_id=str(opened) if opened is not None else None,
        resolved_in_snapshot_id=str(resolved) if resolved is not None else None,
        decision=_maybe_json(record["decision"]),
        donna_research_citations=_maybe_json(record["donna_research_citations"]),
        impact=record["impact"],
        priority=record["priority"],
        created_at=record["created_at"],
        resolved_at=record["resolved_at"],
    )


async def create_issue(conn: Any, payload: IssueCreate) -> str:
    new_id = await conn.fetchval(
        _INSERT_ISSUE,
        payload.contract_id,
        payload.node_id,
        payload.title,
        payload.our_position,
        payload.their_position,
        payload.options_on_table,
        payload.category,
        payload.authority,
        payload.needs_legal_review,
        payload.initiator,
    )
    return str(new_id)


async def list_issues(conn: Any, contract_id: str, status: str | None = None) -> list[StoredIssue]:
    if status is None:
        records = await conn.fetch(_LIST_ISSUES, contract_id)
    else:
        records = await conn.fetch(_LIST_ISSUES_BY_STATUS, contract_id, status)
    return [_to_issue(r) for r in records]


async def get_issue(conn: Any, issue_id: str) -> StoredIssue | None:
    record = await conn.fetchrow(_GET_ISSUE, issue_id)
    return _to_issue(record) if record is not None else None


async def update_issue_status(conn: Any, issue_id: str, payload: IssueStatusUpdate) -> str | None:
    decision = json.dumps(payload.decision) if payload.decision is not None else None
    updated_id = await conn.fetchval(_UPDATE_ISSUE_STATUS, issue_id, payload.status, decision)
    return str(updated_id) if updated_id is not None else None


# Edit the description (title + position text) post-creation (DD-67). Only fields
# present on the request are written (model_dump(exclude_unset=...)): an omitted
# field is left untouched, an explicit null clears a position. Column names come
# from the fixed Pydantic model, never user input, so the dynamic SET is safe. An
# empty patch is a no-op that still confirms the row exists (None == not found).
async def update_issue(conn: Any, issue_id: str, payload: IssueEditRequest) -> str | None:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        existing = await conn.fetchval("SELECT id FROM issues WHERE id = $1", issue_id)
        return str(existing) if existing is not None else None
    cols = list(fields.keys())
    assignments = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(cols))
    sql = f"UPDATE issues SET {assignments} WHERE id = $1 RETURNING id"
    updated_id = await conn.fetchval(sql, issue_id, *(fields[col] for col in cols))
    return str(updated_id) if updated_id is not None else None
