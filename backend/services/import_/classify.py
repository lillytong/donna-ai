"""Deterministic content-role classification for the import spine (DD-54).

Pure, keyword/structure-driven role assignment first; a single whole-region AI
pass resolves front-matter labels. Splits a parsed document into three regions
and tags two cross-cutting kinds:

  * front-matter  — everything up to *and including* the agreement-statement line
    (title / date / parties / recital / agreement_statement);
  * operative     — the clause tree (`clause`, the only numbered region);
  * back-matter   — `appendix` / `signature_block`;
  * cross-cutting — `drafting_note` (bracketed internal counsel commentary,
    anywhere) and TOC lines (detected and dropped — regenerated on export, §10).

The operative region has two symmetric boundaries. The **front** boundary is the
**agreement-statement** line, validated on the real JVA/OA/TLA set (`AGREED AS
FOLLOWS` / `NOW, THEREFORE` / `IT IS HEREBY AGREED` / `WITNESSETH` / `HEREBY
AGREE`): everything up to and including it is front-matter, the operative tree
begins at the next block. If no front boundary is found, the document is treated
as all-operative and flagged — never mis-filed wholesale as front-matter.

The **back** boundary (`_back_matter_start`) CLOSES the operative region: the
first genuine top-level schedule/appendix heading or the first signature-shape
block past the last numbered clause, whichever comes first. Everything from there
to the end of the document is **back-matter** — schedule/appendix content →
`appendix`, execution content → `signature_block`, none of it `clause`, so a
schedule's body paragraphs are never numbered (the "98.27 under the Schedules"
bug). A heading is distinguished from an operative clause that merely opens with
the word "Annexure"/"Schedule" by `_is_appendix_heading` (shape, not keyword
alone). A contract with no schedules and no signature block has no back boundary —
the operative region runs to the end, as before (never force one that isn't there).

Front-matter roles: the deterministic pass places what its keyword rules can
(`WHEREAS`→recital, `BETWEEN`→parties, a date line→date) and flags the rest
`uncertain`; it deliberately does **not** guess a title. A single whole-region AI
pass (`classify_frontmatter_region`, DD-35 low tier) then labels the entire front
matter with the full front matter in view — picking the one real title (a leading
bracketed note is never the title), keeping recital runs `recital`, and grouping
the parties. The operator verifies in F04 (the existing ⚠ mechanism). Only the
model's *parseable* answer is taken; a malformed response leaves the
deterministic roles in place (graceful failure, never a crash).

`signature_block` is detected **structurally**, not by keyword: a block is part
of the signature block only when it sits in the trailing region (after the last
numbered clause, before any schedule/annex) AND matches signature-block shape
(IN WITNESS WHEREOF / SIGNED by / FOR AND ON BEHALF / a rule of underscores). A
mid-document clause that merely says "duly executed" or "may be executed in
counterparts" stays a `clause` (DD-54: never push real operative clauses out of
the tree on a stray keyword).
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from backend.models.contract_tree import (
    BackMatterCategory,
    BackMatterRegion,
    BlockClassification,
    ExtractedBlock,
    FrontMatterRegion,
    Role,
)
from backend.prompts.utils import render
from backend.services.import_.detect import _extract_json
from backend.services.llm import complete

_BOUNDARY = re.compile(
    r"AGREED AS FOLLOWS|NOW,?\s+THEREFORE|IT IS (?:HEREBY )?AGREED|WITNESSETH|HEREBY AGREE",
    re.IGNORECASE,
)
_RECITAL = re.compile(r"\bWHEREAS\b|\bRECITALS?\b|\bWITNESSETH\b", re.IGNORECASE)
_PARTIES = re.compile(r"\bBETWEEN\b|\bAMONG\b|\bPARTIES\b|^\s*\(\d+\)", re.IGNORECASE)
_DATE_KW = re.compile(r"\bDATED\b|\bAS OF\b|\bDATE:", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# Signature-block SHAPE — execution-block phrasing or a rule of signature
# underscores. Deliberately strong: bare "executed"/"signed" is NOT here, so
# operative boilerplate (Counterparts, Entire Agreement, representations) is not
# swept into the signature block. Gated on the trailing region by `classify`.
_SIGNATURE_SHAPE = re.compile(
    r"\bIN WITNESS WHEREOF\b|^\s*SIGNED\s+by\b|FOR AND ON BEHALF|"
    r"\bEXECUTED AS A DEED\b|^\s*_{3,}\s*$",
    re.IGNORECASE,
)
_APPENDIX = re.compile(r"^\s*(?:APPENDIX|SCHEDULE|ANNEX(?:URE)?|EXHIBIT)\b", re.IGNORECASE)
# A true top-level schedule/appendix HEADING — keyword + optional designator
# (roman / letter / number) then a separator or end-of-line. Distinguishes a real
# heading ("ANNEXURE A", "SCHEDULE I: SHAREHOLDING…", "Schedule 2 — Pricing") from
# an operative clause that merely *opens* with the word ("Annexure A may be updated
# from time-to-time …" — a real TLA clause body). The all-uppercase fallback in
# `_is_appendix_heading` additionally catches glued caps headings ("ANNEXURE
# DPROJECT DATA"). Validated on JVA/OA/TLA.
_APPENDIX_HEADING = re.compile(
    r"^\s*(?:APPENDIX|SCHEDULE|ANNEX(?:URE)?|EXHIBIT)\b"
    r"(?:\s+(?:[IVXLCDM]+|[A-Z]|\d+)\b)?"
    r"\s*(?:[:.)\-–—]|$)",
    re.IGNORECASE,
)
_DRAFTING_NOTE = re.compile(
    r"\[[^\]]*\b(?:NOTES?|COMMENTS?|TBD|TBC|DRAFTING|"
    r"TO BE (?:CONFIRMED|DISCUSSED|AGREED))\b[^\]]*\]",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(
    r"\[\s*insert\b|_{3,}|"
    r"\[\s*(?:amount|date|name|number|sum|day|month|year|percentage|%|x{2,}|\.{2,}|…)\s*\]",
    re.IGNORECASE,
)
_TOC_HEADER = re.compile(r"^\s*(?:TABLE OF CONTENTS|CONTENTS|INDEX)\s*$", re.IGNORECASE)
# A dotted leader followed by a page number is an unambiguous TOC entry anywhere.
_DOTTED_LEADER = re.compile(r"\.{4,}\s*\d+\s*$")
# Inside a known TOC region the looser "short line ending in a page number" applies.
_TOC_ENTRY = re.compile(r"\.{2,}\s*\d+\s*$|\s\d{1,3}\s*$")
# Front-matter TOC entries whose page number is glued to the heading (no dotted
# leader, no separating space — e.g. "1.DEFINITIONS & INTERPRETATION5",
# "SCHEDULE I: SHAREHOLDING PATTERN50"). The contiguous-region scan above misses
# these (a stray line like "[To be updated]" breaks the run, and the page number
# has no leading whitespace), so they are matched by shape and dropped only in the
# front matter (before the boundary), where a real operative clause cannot appear.
_TOC_NUMBERED_ENTRY = re.compile(r"^\s*\d+\.\D.*\d\s*$")
_TOC_SCHEDULE_ENTRY = re.compile(
    r"^\s*(?:SCHEDULE|ANNEX(?:URE)?|APPENDIX|EXHIBIT)\b.*\d\s*$", re.IGNORECASE
)

_DATELINE_MAX_LEN = 60


def find_boundary(blocks: list[ExtractedBlock]) -> int | None:
    """Index (block.order) of the agreement-statement line, or None if absent."""
    for b in blocks:
        if b.kind == "paragraph" and _BOUNDARY.search(b.text):
            return b.order
    return None


def _toc_indices(blocks: list[ExtractedBlock]) -> set[int]:
    """Block indices that are table-of-contents lines (dropped on import).

    Two signals: a dotted-leader + page-number line is unambiguous anywhere; the
    looser short-line-ending-in-a-page-number heuristic is confined to the
    contiguous region after an explicit `TABLE OF CONTENTS` header, so real clause
    text ending in a number is never dropped."""
    toc: set[int] = set()
    for b in blocks:
        if _DOTTED_LEADER.search(b.text):
            toc.add(b.order)
    header = next((b.order for b in blocks if _TOC_HEADER.match(b.text)), None)
    if header is not None:
        toc.add(header)
        for b in blocks:
            if b.order <= header:
                continue
            if b.order in toc or _TOC_ENTRY.search(b.text):
                toc.add(b.order)
            else:
                break
    return toc


def _is_dateline(text: str) -> bool:
    return len(text) <= _DATELINE_MAX_LEN and bool(_DATE_KW.search(text) or _YEAR.search(text))


def _classify_frontmatter(text: str) -> tuple[Role, bool]:
    """Deterministic front-matter role by keyword. Returns (role, uncertain).

    Title is deliberately NOT guessed here — the whole-region AI pass owns it
    (DD-54), so a leading bracketed note is never blindly stamped `title`. A block
    no keyword can place gets the neutral `recital` prose bucket + `uncertain`."""
    if _RECITAL.search(text):
        return "recital", False
    if _PARTIES.search(text):
        return "parties", False
    if _is_dateline(text):
        return "date", False
    return "recital", True


def _is_appendix_heading(text: str) -> bool:
    """True iff `text` is a genuine top-level schedule/appendix HEADING — the line
    that CLOSES the operative region — and not an operative clause that merely
    opens with the word "Annexure"/"Schedule". A heading is keyword + optional
    designator then a separator/end, OR an all-uppercase line (glued caps headings
    like "ANNEXURE DPROJECT DATA"). Mixed-case running text after the designator
    ("Annexure A may be updated from time-to-time …") is rejected (DD-54)."""
    if not _APPENDIX.match(text):
        return False
    if _APPENDIX_HEADING.match(text):
        return True
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and not any(c.islower() for c in letters)


def _classify_back_matter(text: str, in_signature: bool) -> tuple[Role, bool]:
    """Role for a block past the operative→back-matter boundary: schedule/appendix
    content vs execution content, never `clause`. A schedule heading is `appendix`
    and ends any signature run; a signature-shape line opens one; otherwise the
    block carries the current state (a schedule body → `appendix`, an execution
    detail line → `signature_block`). DD-54 back-matter is never numbered."""
    if _is_appendix_heading(text):
        return "appendix", False
    if _SIGNATURE_SHAPE.search(text):
        return "signature_block", True
    return ("signature_block" if in_signature else "appendix"), in_signature


def _last_clause_order(blocks: list[ExtractedBlock], op_start: int, toc: set[int]) -> int:
    """Order of the last numbered operative clause before the first schedule/annex
    HEADING — the start of the trailing region where a signature block may appear.
    Falls back to `op_start` when the operative region carries no numbered clause."""
    operative = [b for b in blocks if b.order > op_start and b.order not in toc]
    appendix_orders = [b.order for b in operative if _is_appendix_heading(b.text)]
    first_appendix = min(appendix_orders) if appendix_orders else None
    numbered = [
        b.order
        for b in operative
        if b.has_autonumber and (first_appendix is None or b.order < first_appendix)
    ]
    return max(numbered) if numbered else op_start


def _back_matter_start(
    blocks: list[ExtractedBlock], op_start: int, toc: set[int], last_clause_order: int
) -> int | None:
    """Order at which the operative clause region CLOSES and back-matter begins:
    the first top-level schedule/appendix heading or the first signature-shape
    block past the last numbered clause, whichever comes first (symmetric to the
    front-matter agreement-statement boundary). `None` when the document has
    neither — the operative region then runs to the end (e.g. a contract ending in
    a cost table); never force a boundary that isn't there (DD-54)."""
    operative = [b for b in blocks if b.order > op_start and b.order not in toc]
    headings = [b.order for b in operative if _is_appendix_heading(b.text)]
    signatures = [
        b.order
        for b in operative
        if b.order > last_clause_order and _SIGNATURE_SHAPE.search(b.text)
    ]
    candidates = headings + signatures
    return min(candidates) if candidates else None


def classify(blocks: list[ExtractedBlock]) -> dict[int, BlockClassification]:
    """Assign a role + placeholder flag to every block, keyed by block.order.

    TOC blocks are tagged `is_toc=True` (the pipeline drops them); `drafting_note`
    is detected anywhere (DD-54 guard: kept, never silently dropped); the rest is
    split front-matter / agreement_statement / operative around the boundary.
    Front-matter titles are left for the whole-region AI pass; signature blocks
    are detected structurally in the trailing region."""
    boundary = find_boundary(blocks)
    toc = _toc_indices(blocks)
    if boundary is not None:
        for b in blocks:
            if b.order < boundary and (
                _TOC_NUMBERED_ENTRY.match(b.text) or _TOC_SCHEDULE_ENTRY.match(b.text)
            ):
                toc.add(b.order)
    op_start = boundary if boundary is not None else -1
    last_clause = _last_clause_order(blocks, op_start, toc)
    back_matter_start = _back_matter_start(blocks, op_start, toc, last_clause)

    result: dict[int, BlockClassification] = {}
    in_signature = False

    for b in blocks:
        idx = b.order
        text = b.text
        placeholder = bool(_PLACEHOLDER.search(text))

        if idx in toc:
            result[idx] = BlockClassification(has_placeholder=placeholder, is_toc=True)
            continue

        if _DRAFTING_NOTE.search(text):
            result[idx] = BlockClassification(role="drafting_note", has_placeholder=placeholder)
            continue

        if boundary is not None and idx < boundary:
            role, uncertain = _classify_frontmatter(text)
        elif boundary is not None and idx == boundary:
            role, uncertain = "agreement_statement", False
        elif back_matter_start is not None and idx >= back_matter_start:
            role, in_signature = _classify_back_matter(text, in_signature)
            uncertain = False
        else:
            role, uncertain = "clause", boundary is None

        result[idx] = BlockClassification(
            role=role, has_placeholder=placeholder, uncertain=uncertain
        )

    return result


async def classify_frontmatter_region(blocks_by_index: dict[int, str]) -> dict[int, Role]:
    """Single whole-region front-matter classification call (DD-54/DD-35).

    `blocks_by_index` is the entire front matter (every block up to and including
    the agreement-statement boundary, TOC excluded), keyed by block.order. One
    low-tier (Haiku) call labels them all together — so the model picks one real
    title with the full front matter in view, keeps recital runs `recital`, and
    groups parties, rather than the per-block guessing that emitted a second title
    and mislabeled recitals `parties`. The at-most-one-`title` invariant is
    enforced here (extra titles dropped). A malformed/off-taxonomy answer fails
    validation → empty dict, and every block keeps its deterministic role
    (graceful failure, never raises)."""
    if not blocks_by_index:
        return {}
    listing = "\n".join(f"{i} :: {blocks_by_index[i]}" for i in sorted(blocks_by_index))
    prompt = render("classify_frontmatter_region_v1.txt", blocks=listing)
    raw = await complete(
        tier="low",
        messages=[{"role": "user", "content": prompt}],
        caller="import.classify_frontmatter_region",
    )
    try:
        region = FrontMatterRegion.model_validate_json(_extract_json(raw))
    except ValidationError:
        return {}

    resolved: dict[int, Role] = {}
    title_taken = False
    for item in region.blocks:
        if item.order not in blocks_by_index:
            continue
        if item.role == "title":
            if title_taken:
                continue
            title_taken = True
        resolved[item.order] = item.role
    return resolved


async def classify_backmatter_region(
    blocks_by_index: dict[int, str],
) -> dict[int, BackMatterCategory]:
    """Single whole-region back-matter classification call (DD-56/DD-35).

    `blocks_by_index` is the entire back matter (every block past the operative
    region — schedules/annexures/exhibits + the signature block, TOC excluded),
    keyed by block.order. One low-tier (Haiku) call labels each block as
    title / heading / body / signature, deciding section dividers ("Annexure A",
    "Exhibit 2", "Table 3") SEMANTICALLY rather than by a hardcoded keyword list,
    so a never-before-seen designator is still recognized. A malformed/off-taxonomy
    answer fails validation → empty dict, and every block keeps its deterministic
    role (graceful failure, never raises)."""
    if not blocks_by_index:
        return {}
    listing = "\n".join(f"{i} :: {blocks_by_index[i]}" for i in sorted(blocks_by_index))
    prompt = render("classify_backmatter_region_v1.txt", blocks=listing)
    raw = await complete(
        tier="low",
        messages=[{"role": "user", "content": prompt}],
        caller="import.classify_backmatter_region",
    )
    try:
        region = BackMatterRegion.model_validate_json(_extract_json(raw))
    except ValidationError:
        return {}
    return {
        item.order: item.category
        for item in region.blocks
        if item.order in blocks_by_index
    }
