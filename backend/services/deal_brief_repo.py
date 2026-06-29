"""Persistence for the per-contract deal brief (F37 / DD-95) — the global-context tier Donna
distils at import and the operator edits. DB integration only, no business logic; raw SQL +
asyncpg per the project convention. Mirrors firm_profile_repo, but keyed by contract (one
brief per contract) rather than a singleton.

Three operations:
  * `get_brief`      — read the current brief for a contract (None when never distilled/edited).
  * `seed_brief`     — Donna-authored upsert (distil). EDITS WIN: unless `force` is set, the
    upsert is SKIPPED for a contract whose brief the operator has edited, so an automatic
    re-import re-distil never clobbers an operator edit. A manual Refresh passes `force=True`,
    which overwrites and resets `operator_edited` back to false.
  * `update_brief`   — operator edit: overwrite `content`, mark `operator_edited = true`, and
    leave the last-distil bookkeeping (`model` / `generated_at`) untouched.
"""

from __future__ import annotations

from typing import Any

from backend.models.deal_brief import DealBrief

_COLUMNS = "contract_id, content, operator_edited, model, generated_at, updated_at"

_GET = f"SELECT {_COLUMNS} FROM contract_deal_brief WHERE contract_id = $1"

# Donna-authored upsert (distil). On a fresh contract the INSERT lands. On a re-distil the
# ON CONFLICT branch fires, but its WHERE guards EDITS WIN: the update only proceeds when the
# brief is not operator-edited OR the caller forced it (manual Refresh). A blocked update
# RETURNS no row (fetchrow -> None), which the service logs as a respected operator edit.
_SEED = f"""
INSERT INTO contract_deal_brief
    (contract_id, content, operator_edited, model, generated_at, updated_at)
VALUES ($1, $2, false, $3, now(), now())
ON CONFLICT (contract_id) DO UPDATE SET
    content = EXCLUDED.content,
    operator_edited = false,
    model = EXCLUDED.model,
    generated_at = now(),
    updated_at = now()
WHERE contract_deal_brief.operator_edited = false OR $4
RETURNING {_COLUMNS}
"""

# Operator edit: edits win. Overwrite content + set operator_edited, but never touch the
# last-distil bookkeeping (model / generated_at stay as the last Donna distil's, if any).
_UPDATE = f"""
INSERT INTO contract_deal_brief (contract_id, content, operator_edited, updated_at)
VALUES ($1, $2, true, now())
ON CONFLICT (contract_id) DO UPDATE SET
    content = EXCLUDED.content,
    operator_edited = true,
    updated_at = now()
RETURNING {_COLUMNS}
"""


def _to_brief(record: Any) -> DealBrief:
    return DealBrief(
        contract_id=str(record["contract_id"]),
        content=record["content"],
        operator_edited=record["operator_edited"],
        model=record["model"],
        generated_at=record["generated_at"],
        updated_at=record["updated_at"],
    )


async def get_brief(conn: Any, contract_id: str) -> DealBrief | None:
    """The current deal brief for the contract, or None when none has been distilled/edited."""
    record = await conn.fetchrow(_GET, contract_id)
    return _to_brief(record) if record is not None else None


async def seed_brief(
    conn: Any, contract_id: str, content: str, model: str, *, force: bool = False
) -> DealBrief | None:
    """Donna-authored upsert (distil). Returns the stored brief, or None when the upsert was
    skipped because the operator had edited it and `force` is False (edits win)."""
    record = await conn.fetchrow(_SEED, contract_id, content, model, force)
    return _to_brief(record) if record is not None else None


async def update_brief(conn: Any, contract_id: str, content: str) -> DealBrief:
    """Operator edit (edits win): overwrite the content and mark the brief operator-edited."""
    record = await conn.fetchrow(_UPDATE, contract_id, content)
    return _to_brief(record)
