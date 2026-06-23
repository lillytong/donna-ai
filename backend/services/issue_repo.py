"""Persistence for issues and their comment threads (F06/F07/F08c/F09) — asyncpg.
DB integration only, no business logic.

The FK chain (issues.contract_id, issues.node_id, issue_comments.issue_id) is
enforced by the schema: a create with a non-existent parent id is rejected by
Postgres, not re-checked here. Creates return the generated id as str; the route
reads the row back so server defaults (status, initiator, created_at) are
reflected. JSONB columns (auto_flag, decision, donna_research_citations) come back
as text under asyncpg's default codec and are decoded on read.

On this surface the operator flags who raised the issue (`operator` or
`counterparty`), carried in on the create payload; Donna's analysis columns are
left null at creation. A status that is not 'open' is terminal, so `resolved_at`
is stamped on transition to a terminal status and cleared if an issue is reopened.
"""

from __future__ import annotations

import json
from typing import Any

from backend.models.issues import (
    CommentCreate,
    IssueCreate,
    IssueStatusUpdate,
    StoredComment,
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


# --- comments --------------------------------------------------------------

_INSERT_COMMENT = """
INSERT INTO issue_comments (issue_id, actor, content)
VALUES ($1, $2, $3)
RETURNING id
"""

_SELECT_COMMENT = """
SELECT id, issue_id, actor, content, snapshot_id, created_at
FROM issue_comments
"""

_LIST_COMMENTS = _SELECT_COMMENT + "WHERE issue_id = $1 ORDER BY created_at"


def _to_comment(record: Any) -> StoredComment:
    snapshot_id = record["snapshot_id"]
    return StoredComment(
        id=str(record["id"]),
        issue_id=str(record["issue_id"]),
        actor=record["actor"],
        content=record["content"],
        snapshot_id=str(snapshot_id) if snapshot_id is not None else None,
        created_at=record["created_at"],
    )


async def add_comment(conn: Any, payload: CommentCreate) -> str:
    new_id = await conn.fetchval(
        _INSERT_COMMENT,
        payload.issue_id,
        payload.actor,
        payload.content,
    )
    return str(new_id)


async def list_comments(conn: Any, issue_id: str) -> list[StoredComment]:
    records = await conn.fetch(_LIST_COMMENTS, issue_id)
    return [_to_comment(r) for r in records]
