"""Node-content models (F08 direct inline edit, F08b new node creation, move).

The edit request carries only the new text; ids are path parameters. The create
request carries the placement (parent + an optional sibling anchor: `after_node_id`
to land after, or `before_node_id` to land before — mutually exclusive; neither
appends at end), the text, and the structural role. Both reuse `StoredNode` (the
flat node row) as the response — there is no second node read model.

The move request carries a general reposition (drag-and-drop): a destination
`parent_id` (null = root level) plus an optional sibling anchor (`after_node_id` /
`before_node_id`, mutually exclusive; neither appends as last child). The moved
node's whole sub-tree rides along via `parent_id`. The response echoes the moved
node's new parent.
"""

from __future__ import annotations

from pydantic import BaseModel


class NodeEditRequest(BaseModel):
    text: str


class NodeCreateRequest(BaseModel):
    parent_id: str | None = None
    after_node_id: str | None = None
    before_node_id: str | None = None
    text: str
    role: str = "clause"


class NodeDeleteResponse(BaseModel):
    """Ids of the soft-deleted sub-tree (target first, then its descendants)."""

    deleted_ids: list[str]


class NodeMoveRequest(BaseModel):
    parent_id: str | None = None
    after_node_id: str | None = None
    before_node_id: str | None = None


class NodeMoveResponse(BaseModel):
    """`moved` is False when the node is already at exactly that parent+position
    (no write, no audit) — not an error. `parent_id` is the node's new parent."""

    moved: bool
    node_id: str
    parent_id: str | None
