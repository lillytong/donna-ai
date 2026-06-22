"""Deterministic content-role classification for the import spine (DD-54).

Pure, keyword/structure-driven role assignment — no LLM in the default path.
Splits a parsed document into three regions and tags two cross-cutting kinds:

  * front-matter  — everything up to *and including* the agreement-statement line
    (title / date / parties / recital / agreement_statement);
  * operative     — the clause tree (`clause`, the only numbered region);
  * back-matter   — `appendix` / `signature_block`;
  * cross-cutting — `drafting_note` (bracketed internal counsel commentary,
    anywhere) and TOC lines (detected and dropped — regenerated on export, §10).

The operative-clause boundary is the **agreement-statement** line, validated on
the real JVA/OA/TLA set (`AGREED AS FOLLOWS` / `NOW, THEREFORE` / `IT IS HEREBY
AGREED` / `WITNESSETH` / `HEREBY AGREE`): everything up to and including it is
front-matter, the operative tree begins at the next block. If no boundary is
found, the document is treated as all-operative and flagged — never mis-filed
wholesale as front-matter.

Ambiguous front-matter the rules can't place is given a neutral best-guess role
and flagged `uncertain` for operator confirmation in F04 (the existing ⚠
mechanism) — never silently mis-filed (DD-54 guard). A Haiku residue pass
(`classify_residue`, DD-35, low-consequence tier) resolves that uncertain
front-matter residue: it runs only on the blocks the deterministic rules could
not place, and only its *confident* answers are taken — a low-confidence or
unparseable response leaves the block `uncertain` for F04 (graceful failure,
never a crash). The deterministic pass above always runs first (free, instant);
the AI touches only the residue.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from backend.models.contract_tree import (
    BlockClassification,
    ExtractedBlock,
    Role,
    RoleSuggestion,
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
_SIGNATURE = re.compile(r"\bIN WITNESS\b|\bSIGNED\b|\bEXECUTED\b|FOR AND ON BEHALF", re.IGNORECASE)
_APPENDIX = re.compile(r"^\s*(?:APPENDIX|SCHEDULE|ANNEX(?:URE)?|EXHIBIT)\b", re.IGNORECASE)
_DRAFTING_NOTE = re.compile(
    r"\[[^\]]*\b(?:NOTE|COMMENT|TBD|TBC|DRAFTING|TO BE (?:CONFIRMED|DISCUSSED|AGREED))\b[^\]]*\]",
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
    """Non-title front-matter. Returns (role, uncertain)."""
    if _RECITAL.search(text):
        return "recital", False
    if _PARTIES.search(text):
        return "parties", False
    if _is_dateline(text):
        return "date", False
    # Rules can't place it — neutral best-guess (front-matter prose) + flag for
    # F04. DD-54: never silently mis-file; `recital` is the catch-all prose bucket.
    return "recital", True


def _classify_operative(text: str) -> Role:
    if _SIGNATURE.search(text):
        return "signature_block"
    if _APPENDIX.match(text):
        return "appendix"
    return "clause"


def classify(blocks: list[ExtractedBlock]) -> dict[int, BlockClassification]:
    """Assign a role + placeholder flag to every block, keyed by block.order.

    TOC blocks are tagged `is_toc=True` (the pipeline drops them); `drafting_note`
    is detected anywhere (DD-54 guard: kept, never silently dropped); the rest is
    split front-matter / agreement_statement / operative around the boundary."""
    boundary = find_boundary(blocks)
    toc = _toc_indices(blocks)
    result: dict[int, BlockClassification] = {}
    title_assigned = False

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

        if boundary is None:
            role = _classify_operative(text)
            result[idx] = BlockClassification(
                role=role, has_placeholder=placeholder, uncertain=(role == "clause")
            )
            continue

        if idx < boundary:
            if not title_assigned:
                title_assigned = True
                role, uncertain = "title", False
            else:
                role, uncertain = _classify_frontmatter(text)
        elif idx == boundary:
            role, uncertain = "agreement_statement", False
        else:
            role, uncertain = _classify_operative(text), False

        result[idx] = BlockClassification(
            role=role, has_placeholder=placeholder, uncertain=uncertain
        )

    return result


async def _suggest_role(block_text: str) -> Role | None:
    """One low-tier (Haiku, DD-35) call for one ambiguous front-matter block.

    Returns the model's role only when it parses *and* the model is confident;
    otherwise None — the caller leaves the block `uncertain` for F04. Tolerates a
    fenced or prose-wrapped response (shared `_extract_json`) and a malformed or
    off-taxonomy answer (validation failure → None). Never raises."""
    prompt = render("classify_role_v1.txt", block_text=block_text)
    raw = await complete(
        tier="low",
        messages=[{"role": "user", "content": prompt}],
        caller="import.classify_role",
    )
    try:
        suggestion = RoleSuggestion.model_validate_json(_extract_json(raw))
    except ValidationError:
        return None
    return suggestion.role if suggestion.confident else None


async def classify_residue(blocks_by_index: dict[int, str]) -> dict[int, Role]:
    """Resolve the deterministic residue with the AI pass (DD-54/DD-35).

    `blocks_by_index` is the ambiguous front-matter the rules could not place
    (keyed by block.order). One low-tier call per block — the residue is small.
    Returns only the blocks the model confidently classified; an absent key means
    the block keeps its deterministic role and `uncertain` flag (graceful
    failure)."""
    resolved: dict[int, Role] = {}
    for idx, text in blocks_by_index.items():
        role = await _suggest_role(text)
        if role is not None:
            resolved[idx] = role
    return resolved
