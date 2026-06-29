"""Models for the import vertical (Mode A) and the contract-tree read.

`ImportResult` is the orchestrator's return value and the POST /import response.
`ContractTreeResponse` is the nested live node tree served by the GET route —
soft-deleted nodes are excluded upstream (live tree); children are ordered by
`order_index` (gap-based, OQ-07).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.models.contract_tree import NodeImage, NodeRow, Role
from backend.models.extraction import Extraction


class ImportResult(BaseModel):
    contract_id: str
    node_count: int
    root_count: int
    uncertain_count: int
    # Optional AI enrichment (Mode A step 2, DD-10/11/12): node index -> detected
    # entity candidates. None unless detection was requested. Never persisted in
    # the import path — resolved into defined_terms / cross_references only after
    # the operator confirms in the import-review UI.
    entity_candidates: dict[int, Extraction] | None = None


class CandidateNode(BaseModel):
    """A candidate-tree node for the F04 review UI (before commit). Merges the
    persistable NodeRow fields with `depth` and the position-derived `number`
    (DD-02), and carries the `uncertain` flag the operator triages."""

    index: int
    parent_index: int | None
    order_index: int
    depth: int
    number: str
    content_type: str
    heading: str | None = None
    body: str | None = None
    table_data: list[list[str]] | None = None
    plain_text: str | None = None
    uncertain: bool
    # DD-54: structural role + placeholder flag drive the F04 region rendering
    # (non-clause roles render as labeled regions, not numbered rows). Non-clause
    # nodes carry an empty `number`.
    role: Role = "clause"
    has_placeholder: bool = False


class TrackedChangeReport(BaseModel):
    """Clean-document guard surface (DD-46): tracked-change counts in the source.
    `flattened` is True when any were present — extraction already accepted them
    to their final state, so the operator is warned the import was not clean."""

    insertions: int
    deletions: int
    flattened: bool


class PreviewResponse(BaseModel):
    """Parse-preview payload (no persistence): the candidate tree the operator
    reviews and corrects in F04 before committing."""

    nodes: list[CandidateNode]
    node_count: int
    uncertain_count: int
    tracked_changes: TrackedChangeReport


class CommitRequest(BaseModel):
    """The operator-corrected tree submitted back for persistence after review."""

    nodes: list[NodeRow]


class StoredNode(BaseModel):
    """A node as read back from the DB (flat row)."""

    id: str
    parent_id: str | None = None
    order_index: int
    content_type: str
    heading: str | None = None
    body: str | None = None
    table_data: list[list[str]] | None = None
    plain_text: str | None = None
    role: Role = "clause"
    has_placeholder: bool = False


class NodeTreeItem(BaseModel):
    """A node in the nested response tree."""

    id: str
    order_index: int
    content_type: str
    heading: str | None = None
    body: str | None = None
    table_data: list[list[str]] | None = None
    plain_text: str | None = None
    role: Role = "clause"
    has_placeholder: bool = False
    images: list[NodeImage] = Field(default_factory=list)
    children: list[NodeTreeItem] = Field(default_factory=list)


class ContractTreeResponse(BaseModel):
    contract_id: str
    nodes: list[NodeTreeItem]

    @classmethod
    def from_rows(cls, contract_id: str, rows: list[StoredNode]) -> ContractTreeResponse:
        """Nest flat rows into a forest by parent_id; order siblings by order_index.
        A row whose parent_id is absent from the set (or null) becomes a root."""
        items: dict[str, NodeTreeItem] = {
            r.id: NodeTreeItem(
                id=r.id,
                order_index=r.order_index,
                content_type=r.content_type,
                heading=r.heading,
                body=r.body,
                table_data=r.table_data,
                plain_text=r.plain_text,
                role=r.role,
                has_placeholder=r.has_placeholder,
            )
            for r in rows
        }
        roots: list[NodeTreeItem] = []
        for r in rows:
            item = items[r.id]
            if r.parent_id is not None and r.parent_id in items:
                items[r.parent_id].children.append(item)
            else:
                roots.append(item)
        for item in items.values():
            item.children.sort(key=lambda n: n.order_index)
        roots.sort(key=lambda n: n.order_index)
        return cls(contract_id=contract_id, nodes=roots)
