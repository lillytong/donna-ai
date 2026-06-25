"""Shared sibling-placement primitives for on-the-fly structural ops (F08b create,
move) — asyncpg.

Both creating a node and moving one land a node under a parent at an anchor-derived
`order_index`: gap-based `after`/`before` midpoints, with the OQ-07 no-gap fallback
that re-spaces siblings to 100,200,300… and reopens a gap. The `IS NOT DISTINCT FROM`
sibling fetch treats NULL (root) as a value, so root-level siblings match too.

Re-spacing order_index never changes derived clause numbers (DD-02): those come from
tree position, which the preserved sibling order keeps stable.
"""

from __future__ import annotations

from typing import Any

# _ORDER_GAP (=100) is reused from the import spine so on-the-fly placement shares the
# same leave-room-between-siblings spacing as imported trees (OQ-07).
from backend.services.import_.tree_builder import _ORDER_GAP

# A bump larger than any plausible final order_index, used to vacate the low range
# before re-spacing so per-row UPDATEs can't transiently break the
# UNIQUE (contract_id, parent_id, order_index) constraint mid-renumber.
_RESPACE_OFFSET = 1_000_000

# parent_id IS NOT DISTINCT FROM $2 treats NULL (root) as a value, so root-level
# siblings are matched alongside nested ones.
FETCH_SIBLINGS = """
SELECT id, order_index
FROM nodes
WHERE contract_id = $1 AND parent_id IS NOT DISTINCT FROM $2 AND is_deleted = false
ORDER BY order_index
"""

_BUMP_SIBLINGS = """
UPDATE nodes SET order_index = order_index + $3, updated_at = now()
WHERE contract_id = $1 AND parent_id IS NOT DISTINCT FROM $2 AND is_deleted = false
"""

_SET_ORDER = "UPDATE nodes SET order_index = $2, updated_at = now() WHERE id = $1"


def norm(value: Any) -> str | None:
    return str(value) if value is not None else None


def position_of(siblings: list[Any], node_id: str | None) -> int:
    return next(i for i, s in enumerate(siblings) if norm(s["id"]) == norm(node_id))


async def respace_siblings(
    conn: Any, contract_id: str, parent_id: str | None, siblings: list[Any]
) -> None:
    """OQ-07 no-gap fallback: vacate the low range, then renumber siblings to
    100,200,300… in stable order so a fresh gap opens around any anchor. Renumbering
    order_index does NOT change derived clause numbers — those come from tree
    position, which the preserved sibling order keeps stable."""
    await conn.execute(_BUMP_SIBLINGS, contract_id, parent_id, _RESPACE_OFFSET)
    for i, sibling in enumerate(siblings):
        await conn.execute(_SET_ORDER, sibling["id"], (i + 1) * _ORDER_GAP)


def compute_order_index(siblings: list[Any], after: Any, before: Any) -> tuple[bool, int]:
    """Gap-based placement among `siblings` (which must EXCLUDE the node being placed).

    Returns (respace, order_index). When respace is True the gap-based midpoint
    collapsed (adjacent integers / no room below a first child): the caller must
    `respace_siblings` then `recompute_after_respace` for the final slot — the int
    returned here is an unused placeholder in that case.
    """
    if before is not None:
        before_idx = before["order_index"]
        lower = [s["order_index"] for s in siblings if s["order_index"] < before_idx]
        if not lower:
            # First child: take the slot below it; no room only if before_idx <= 1.
            midpoint = before_idx // 2
            return (True, _ORDER_GAP) if midpoint == 0 else (False, midpoint)
        prev_idx = max(lower)
        midpoint = (prev_idx + before_idx) // 2
        # Adjacent integers leave no room — fall back to the OQ-07 respace.
        return (True, _ORDER_GAP) if midpoint == prev_idx else (False, midpoint)

    if after is None:
        # Append: one gap past the last sibling, or the first slot if empty.
        if siblings:
            return False, max(s["order_index"] for s in siblings) + _ORDER_GAP
        return False, _ORDER_GAP

    after_idx = after["order_index"]
    higher = [s["order_index"] for s in siblings if s["order_index"] > after_idx]
    if not higher:
        return False, after_idx + _ORDER_GAP
    next_idx = min(higher)
    midpoint = (after_idx + next_idx) // 2
    # Adjacent integers leave no room — fall back to the OQ-07 respace.
    return (True, _ORDER_GAP) if midpoint == after_idx else (False, midpoint)


def recompute_after_respace(
    siblings: list[Any], after_node_id: str | None, before_node_id: str | None
) -> int:
    """After `respace_siblings` renumbered `siblings` to 100,200,300…, compute the
    placed node's slot in the freshly opened gap (only before/after anchors ever
    respace — append always has room past the last sibling)."""
    if before_node_id is not None:
        # Just below the re-spaced before_node ((pos+1)*GAP): midpoint with its new
        # predecessor, or half its slot when it is the first child.
        position = position_of(siblings, before_node_id)
        return (position + 1) * _ORDER_GAP - _ORDER_GAP // 2
    position = position_of(siblings, after_node_id)
    return (position + 1) * _ORDER_GAP + _ORDER_GAP // 2
