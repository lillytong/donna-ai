"""Persistence for Donna's issue recommendations (F11, DD-68) — asyncpg, DB integration
only. The `donna_recommendations` row is the DRAFT, held apart from `issues.*` until the
operator confirms (DD-68): the auto-draft must never reach the F31-exported issue fields.

Three operations:
  * `upsert_draft` — one draft per issue (UNIQUE issue_id); regenerating REPLACES the
    draft and resets `confirmed` (a fresh draft is unconfirmed again).
  * `get_by_issue` — read the current draft.
  * `confirm` — in ONE transaction: set `confirmed = true`, copy
    `draft_recommended_position -> issues.recommended_position` and
    `draft_counter_language -> issues.donna_counter_language` (the fields F31 exports), and
    record the audit event. This is the only path that writes Donna's language into the
    exported issue fields.

`citations` is JSONB: written via json.dumps + ::jsonb, read back via json.loads when
asyncpg hands it over as a str.
"""

from __future__ import annotations

import json
from typing import Any

from backend.config.settings import get_settings
from backend.models.audit import EVENT_RECOMMENDATION_CONFIRMED, AuditEvent
from backend.models.recommendations import RecommendationDraft, StoredRecommendation
from backend.services.audit_repo import record_event

_SELECT = """
SELECT id, issue_id, rationale, draft_recommended_position, draft_counter_language,
       citations, model, generated_at, confirmed
FROM donna_recommendations
"""
_GET_BY_ISSUE = _SELECT + "WHERE issue_id = $1"

# One draft per issue (UNIQUE issue_id). Regenerate replaces the draft in place and
# resets confirmed -> false: a freshly generated draft is unconfirmed until [Use Donna's].
_UPSERT = """
INSERT INTO donna_recommendations
    (issue_id, rationale, draft_recommended_position, draft_counter_language,
     citations, model)
VALUES ($1, $2, $3, $4, $5::jsonb, $6)
ON CONFLICT (issue_id) DO UPDATE SET
    rationale = EXCLUDED.rationale,
    draft_recommended_position = EXCLUDED.draft_recommended_position,
    draft_counter_language = EXCLUDED.draft_counter_language,
    citations = EXCLUDED.citations,
    model = EXCLUDED.model,
    generated_at = now(),
    confirmed = false
RETURNING id, issue_id, rationale, draft_recommended_position, draft_counter_language,
          citations, model, generated_at, confirmed
"""

_CONFIRM_REC = "UPDATE donna_recommendations SET confirmed = true WHERE issue_id = $1"

# Operator [Edit]: overwrite the draft language with the edited text before the copy, so
# the single _COPY_TO_ISSUE path (which reads from the draft row) exports exactly what the
# operator confirmed (DD-68 addendum). The draft row stays the one source of confirmed truth.
_EDIT_DRAFT = """
UPDATE donna_recommendations
SET draft_recommended_position = $2, draft_counter_language = $3
WHERE issue_id = $1
"""

# The draft -> issues.* copy (DD-68): the only write into the F31-exported issue fields.
_COPY_TO_ISSUE = """
UPDATE issues i
SET recommended_position = r.draft_recommended_position,
    donna_counter_language = r.draft_counter_language
FROM donna_recommendations r
WHERE r.issue_id = i.id AND i.id = $1
"""


def _to_recommendation(record: Any) -> StoredRecommendation:
    citations = record["citations"]
    if isinstance(citations, str):
        citations = json.loads(citations)
    return StoredRecommendation(
        id=str(record["id"]),
        issue_id=str(record["issue_id"]),
        rationale=record["rationale"],
        draft_recommended_position=record["draft_recommended_position"],
        draft_counter_language=record["draft_counter_language"],
        citations=citations,
        model=record["model"],
        generated_at=record["generated_at"],
        confirmed=record["confirmed"],
    )


async def upsert_draft(
    conn: Any, issue_id: str, draft: RecommendationDraft, model: str
) -> StoredRecommendation:
    record = await conn.fetchrow(
        _UPSERT,
        issue_id,
        draft.rationale,
        draft.draft_recommended_position,
        draft.draft_counter_language,
        json.dumps(draft.citations),
        model,
    )
    return _to_recommendation(record)


async def get_by_issue(conn: Any, issue_id: str) -> StoredRecommendation | None:
    record = await conn.fetchrow(_GET_BY_ISSUE, issue_id)
    return _to_recommendation(record) if record is not None else None


async def confirm(
    conn: Any,
    issue_id: str,
    edited: tuple[str | None, str | None] | None = None,
) -> StoredRecommendation | None:
    """Copy the draft into the issue's exported fields (DD-68) in one transaction. Returns
    the now-confirmed recommendation, or None if no draft exists for the issue. When `edited`
    is given (operator [Edit] before confirm), it carries the edited
    (recommended_position, counter_language) — both overwrite the draft row first, so the
    same copy path exports the operator-edited language."""
    existing = await conn.fetchrow(_GET_BY_ISSUE, issue_id)
    if existing is None:
        return None
    async with conn.transaction():
        if edited is not None:
            await conn.execute(_EDIT_DRAFT, issue_id, edited[0], edited[1])
        await conn.execute(_CONFIRM_REC, issue_id)
        await conn.execute(_COPY_TO_ISSUE, issue_id)
        await record_event(
            conn,
            AuditEvent(
                event_type=EVENT_RECOMMENDATION_CONFIRMED,
                entity_type="issue",
                entity_id=issue_id,
                actor=get_settings().operator_actor,
                payload=None,
            ),
        )
    updated = await conn.fetchrow(_GET_BY_ISSUE, issue_id)
    return _to_recommendation(updated)
