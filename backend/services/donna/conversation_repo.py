"""Persistence for the per-contract Donna conversation (DD-40) over the existing
`donna_conversations` + `donna_messages` tables — asyncpg, DB integration only.

One conversation per contract (get-or-create). Messages are appended one role at a
time and read back in insert order. Append is intentionally NOT wrapped in a single
transaction with its sibling: each insert runs autocommit so the two messages of a
turn get distinct `created_at` (a shared transaction `now()` would collide). Read order
is `created_at, role DESC` — the role tiebreak keeps a user message before its assistant
reply on the rare same-microsecond tie (`gen_random_uuid` ids are not monotonic)."""

from __future__ import annotations

from typing import Any

from backend.models.donna import DonnaMessage, DonnaRole, StoredConversation

_GET_CONVERSATION = (
    "SELECT id, contract_id, running_summary FROM donna_conversations WHERE contract_id = $1"
)
_INSERT_CONVERSATION = (
    "INSERT INTO donna_conversations (contract_id) VALUES ($1) "
    "RETURNING id, contract_id, running_summary"
)
_UPDATE_SUMMARY = (
    "UPDATE donna_conversations SET running_summary = $2, updated_at = now() WHERE id = $1"
)
_INSERT_MESSAGE = (
    "INSERT INTO donna_messages (conversation_id, role, content) VALUES ($1, $2, $3) RETURNING id"
)
_FETCH_MESSAGES = (
    "SELECT role, content, created_at FROM donna_messages "
    "WHERE conversation_id = $1 ORDER BY created_at, role DESC"
)
_CLEAR_MESSAGES = (
    "DELETE FROM donna_messages WHERE conversation_id IN "
    "(SELECT id FROM donna_conversations WHERE contract_id = $1)"
)
_CLEAR_SUMMARY = (
    "UPDATE donna_conversations SET running_summary = NULL, updated_at = now() "
    "WHERE contract_id = $1"
)


def _to_conversation(record: Any) -> StoredConversation:
    return StoredConversation(
        id=str(record["id"]),
        contract_id=str(record["contract_id"]),
        running_summary=record["running_summary"],
    )


async def get_or_create_conversation(conn: Any, contract_id: str) -> StoredConversation:
    record = await conn.fetchrow(_GET_CONVERSATION, contract_id)
    if record is None:
        record = await conn.fetchrow(_INSERT_CONVERSATION, contract_id)
    return _to_conversation(record)


async def append_message(conn: Any, conversation_id: str, role: DonnaRole, content: str) -> str:
    new_id = await conn.fetchval(_INSERT_MESSAGE, conversation_id, role, content)
    return str(new_id)


async def fetch_messages(conn: Any, conversation_id: str) -> list[DonnaMessage]:
    records = await conn.fetch(_FETCH_MESSAGES, conversation_id)
    return [
        DonnaMessage(role=r["role"], content=r["content"], created_at=r["created_at"])
        for r in records
    ]


async def update_summary(conn: Any, conversation_id: str, summary: str) -> None:
    await conn.execute(_UPDATE_SUMMARY, conversation_id, summary)


async def clear_conversation(conn: Any, contract_id: str) -> None:
    """Wipe the contract's Donna thread (DD-40 conversations never auto-clear): drop its
    messages and reset the rolling summary, so the next `ask`/`thread` starts empty. The
    conversation row is kept (id stable); idempotent — a no-op when none exists."""
    await conn.execute(_CLEAR_MESSAGES, contract_id)
    await conn.execute(_CLEAR_SUMMARY, contract_id)
