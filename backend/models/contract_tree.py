"""Parsed-document models for the import spine (Phase 0).

Increment 1 is faithful *extraction* — ordered content blocks with numbering
metadata, content-control-inclusive (DD-45). Hierarchy assembly (the node tree,
DD-36) consumes these blocks in a later increment.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel

# The structural-role taxonomy (DD-54). Only `clause` is numbered; everything
# else is preserved but excluded from the clause tree and clause numbering.
Role = Literal[
    "title",
    "date",
    "parties",
    "recital",
    "agreement_statement",
    "clause",
    "appendix",
    "signature_block",
    "drafting_note",
]

# The front-matter subset of the taxonomy (DD-54). The deterministic classifier
# only ever flags front-matter blocks `uncertain`, so the AI residue pass (DD-35)
# is scoped to these roles — never the operative tree.
FrontMatterRole = Literal["title", "date", "parties", "recital", "agreement_statement"]
FRONT_MATTER_ROLES: frozenset[str] = frozenset(get_args(FrontMatterRole))


class RoleSuggestion(BaseModel):
    """Structured output of the Haiku residue pass for one ambiguous front-matter
    block (DD-54/DD-35). The role is constrained to the front-matter subset, so a
    model that returns any other value fails validation and is treated as a parse
    failure (the block keeps its `uncertain` flag). `confident=False` likewise
    leaves the block for operator confirmation in F04 — the model never silently
    overrides a low-confidence call."""

    role: FrontMatterRole
    confident: bool


class BlockClassification(BaseModel):
    """The deterministic role decision for one extracted block (DD-54). `is_toc`
    marks a table-of-contents line, dropped on import (never stored — regenerated
    on export, §10). `uncertain` flags a low-confidence placement for operator
    confirmation in F04 (the existing ⚠ mechanism)."""

    role: Role = "clause"
    has_placeholder: bool = False
    uncertain: bool = False
    is_toc: bool = False


class ExtractedBlock(BaseModel):
    """One content block in document order, as read from the .docx."""

    order: int
    kind: Literal["paragraph", "table"]
    text: str = ""  # paragraph: accept-all-changes text
    rows: list[list[str]] | None = None  # table: structured cells, never flattened
    has_autonumber: bool = False  # carries Word list numbering (w:numPr)
    list_level: int | None = None  # w:ilvl value when auto-numbered
    # w:numId — which numbering scheme; ilvl is a depth only *within* one num_id.
    num_id: int | None = None
    literal_prefix: str | None = None  # typed "3.1"/"(a)" prefix, if any
    in_content_control: bool = False  # block came from inside a w:sdt (DD-45)


class ParsedDocument(BaseModel):
    """Extraction result plus the coverage check against the document's text ceiling."""

    blocks: list[ExtractedBlock]
    extracted_chars: int
    ceiling_chars: int

    @property
    def coverage_pct(self) -> float:
        return 100.0 * self.extracted_chars / self.ceiling_chars if self.ceiling_chars else 0.0

    @property
    def is_lossless(self) -> bool:
        # Content integrity (§2.1): extraction must reach the document's text ceiling.
        return self.coverage_pct >= 99.95


class TreeNode(BaseModel):
    """A node in the assembled hierarchy. `index` is its position in the flat
    block list and doubles as a stable id; `parent_index` points at another
    node's index (None = root). `uncertain` marks a low-confidence placement the
    operator should review in the import-review UI (F04 / DD-36)."""

    index: int
    parent_index: int | None
    depth: int
    order_index: int  # gap-based sibling order (OQ-07)
    kind: Literal["prose", "table"]
    text: str = ""
    rows: list[list[str]] | None = None
    numbered: bool = False
    uncertain: bool = False
    role: Role = "clause"  # DD-54; set by the classifier, default is operative
    has_placeholder: bool = False


class ParsedTree(BaseModel):
    nodes: list[TreeNode]

    def children_of(self, index: int | None) -> list[TreeNode]:
        return [n for n in self.nodes if n.parent_index == index]

    @property
    def uncertain_count(self) -> int:
        return sum(1 for n in self.nodes if n.uncertain)


class NodeRow(BaseModel):
    """A node ready to persist. `parent_index` references another row's `index`;
    the repository resolves indices to generated DB ids on insert (parents first,
    guaranteed because a node's parent always has a lower index)."""

    index: int
    parent_index: int | None
    order_index: int
    content_type: Literal["prose", "table"]
    heading: str | None = None
    body: str | None = None
    table_data: list[list[str]] | None = None
    plain_text: str | None = None
    uncertain: bool = False
    role: Role = "clause"  # DD-54
    has_placeholder: bool = False
