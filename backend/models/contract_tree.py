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
    "appendix_title",  # a schedule/annex/exhibit divider title — back-matter level 0 (DD-56)
    "signature_block",
    "drafting_note",
]

# The front-matter subset of the taxonomy (DD-54). The deterministic classifier
# only ever flags front-matter blocks `uncertain`, so the AI region pass (DD-35)
# is scoped to these roles — never the operative tree.
FrontMatterRole = Literal["title", "date", "parties", "recital", "agreement_statement"]
FRONT_MATTER_ROLES: frozenset[str] = frozenset(get_args(FrontMatterRole))

# The role set the whole-region front-matter classifier may return: the
# front-matter roles plus `drafting_note` (bracketed internal counsel notes that
# sit inside the front matter, e.g. a "[CAM Notes: …]" run before the title). A
# model answer outside this set fails validation and the block keeps its
# deterministic role (DD-54 graceful failure).
RegionRole = Literal["title", "date", "parties", "recital", "agreement_statement", "drafting_note"]

# The semantic category the whole-region BACK-matter pass assigns each block
# (DD-56). `title` = a schedule/annex/exhibit/table divider, identified by meaning
# not keyword; `heading` = a sub-heading inside a schedule; `body` = schedule
# content; `signature` = execution/signature content. Mapped to (role, force_kind)
# by the pipeline. An off-taxonomy answer fails validation → deterministic roles
# stay (graceful failure).
BackMatterCategory = Literal["title", "heading", "body", "signature"]


class FrontMatterBlockRole(BaseModel):
    """One (block order, role) pair from the whole-region front-matter pass."""

    order: int
    role: RegionRole


class BackMatterBlockRole(BaseModel):
    """One (block order, category) pair from the whole-region back-matter pass."""

    order: int
    category: BackMatterCategory


class BackMatterRegion(BaseModel):
    """Structured output of the single whole-region back-matter classification call
    (DD-56): a semantic category for every back-matter block, decided with the
    whole back matter in view. Mirrors `FrontMatterRegion`. A malformed/off-taxonomy
    answer fails validation and the deterministic roles are kept."""

    blocks: list[BackMatterBlockRole]


class FrontMatterRegion(BaseModel):
    """Structured output of the single whole-region front-matter classification
    call (DD-54/DD-35): a role for every front-matter block, decided with the full
    front matter in view (not block-by-block). The caller enforces the
    at-most-one-`title` invariant and never overrides a deterministic
    `drafting_note` (the §12 export-exclusion guard). An off-taxonomy or malformed
    answer fails validation and leaves the deterministic roles in place."""

    blocks: list[FrontMatterBlockRole]


class BlockClassification(BaseModel):
    """The deterministic role decision for one extracted block (DD-54). `is_toc`
    marks a table-of-contents line, dropped on import (never stored — regenerated
    on export, §10). `uncertain` flags a low-confidence placement for operator
    confirmation in F04 (the existing ⚠ mechanism)."""

    role: Role = "clause"
    has_placeholder: bool = False
    uncertain: bool = False
    is_toc: bool = False
    # When set (by the back-matter AI pass, DD-56), forces the heading/body split
    # at persist time instead of the shape heuristic — so an AI-categorized
    # appendix heading/body lands in the right field regardless of its wording.
    force_kind: Literal["heading", "body"] | None = None


class ExtractedBlock(BaseModel):
    """One content block in document order, as read from the .docx."""

    order: int
    kind: Literal["paragraph", "table"]
    text: str = ""  # paragraph: accept-all-changes text
    rows: list[list[str]] | None = None  # table: structured cells, never flattened
    has_autonumber: bool = False  # carries Word list numbering (direct or style w:numPr)
    list_level: int | None = None  # w:ilvl value when auto-numbered (direct or style)
    # w:numId — the numbering *instance*; ilvl is a depth only *within* one num_id.
    num_id: int | None = None
    # w:abstractNumId — the numbering *definition* numId resolves to. Word splits one
    # multilevel outline across many numIds sharing an abstractNumId, so this is the
    # correct backbone-grouping key (DD-36); None when num_id is None / unresolvable.
    abstract_num_id: int | None = None
    # w:outlineLvl (0-8) resolved from the paragraph style; 9 ("body text") -> None.
    # Section-heading depth signal carried via w:pStyle, invisible to direct numPr.
    outline_level: int | None = None
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
    force_kind: Literal["heading", "body"] | None = None  # DD-56 (back-matter AI split)


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
