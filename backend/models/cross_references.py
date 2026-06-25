"""Cross-references registry (F17, contract-scoped) — extraction + read models.

`ExtractedCrossReference` is the in-memory result of the deterministic scan over
node text (before persistence): a single reference to another clause in the SAME
contract ("clause 12.3", "Section 5", "Schedule I"). `StoredCrossReference` is a
row read back from `cross_references`.

Scope of THIS slice is intra-contract only (DD-11): the source is always a node in
the contract; the target is a node in the SAME contract when the referenced number
resolves through the shared `_plan` numbering, or NULL when it does not (a
designator with no decimal clause number — schedules/appendices — or a number that
no clause carries). `resolved` is the convenience flag (`target_node_id is not None`)
and `label` is the legible reference text ("clause 12.3"); `label` is only known at
extraction time, so it is None on a plain DB read.
"""

from __future__ import annotations

from pydantic import BaseModel


class ExtractedCrossReference(BaseModel):
    """A cross-reference detected in node text, before it is inserted.

    `kind` is the reference keyword normalised ("clause", "section", "schedule",
    "appendix", …); `designator` is the referenced token ("12.3", "5", "I", "B");
    `label` is the legible "<kind> <designator>" text. `source_node_id` is filled
    when the scan binds the ref to the node it was found in; `target_node_id` is
    the resolved node (None when the designator does not map to a clause number)."""

    kind: str
    designator: str
    label: str
    source_node_id: str | None = None
    target_node_id: str | None = None

    @property
    def resolved(self) -> bool:
        return self.target_node_id is not None


class StoredCrossReference(BaseModel):
    """A `cross_references` row read back from the DB. `label` is populated on the
    extract path (the just-scanned reference text) and None on a plain list read;
    `resolved` mirrors `target_node_id is not None`."""

    id: str
    source_node_id: str
    source_contract_id: str
    target_node_id: str | None = None
    target_contract_id: str | None = None
    label: str | None = None
    resolved: bool = False


class ExtractCrossReferencesResponse(BaseModel):
    """POST .../extract response: what the run found for this contract (the rows now
    live in `cross_references`)."""

    contract_id: str
    references_found: int
    cross_references: list[StoredCrossReference]


class CrossReferencesResponse(BaseModel):
    """GET response: the contract's stored cross-reference links."""

    contract_id: str
    cross_references: list[StoredCrossReference]
