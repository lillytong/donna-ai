"""Node-content models (F08 direct inline edit, F08b new node creation).

The edit request carries only the new text; ids are path parameters. The create
request carries the placement (parent + optional sibling-to-follow), the text,
and the structural role. Both reuse `StoredNode` (the flat node row) as the
response — there is no second node read model.
"""

from __future__ import annotations

from pydantic import BaseModel


class NodeEditRequest(BaseModel):
    text: str


class NodeCreateRequest(BaseModel):
    parent_id: str | None = None
    after_node_id: str | None = None
    text: str
    role: str = "clause"
