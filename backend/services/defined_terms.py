"""Defined-terms extraction (F16) — deterministic scan + deal-scoped upsert (asyncpg).

Scans a contract's node text for defined-term DEFINITIONS and upserts them into the
deal-scoped `defined_terms` registry (DD-10), keyed by `(deal_id, term)`. Built so
F05 can later offer hover-to-define against the registry.

PRECISION OVER RECALL (deliberate tradeoff). Legal prose quotes a great deal of text
that is not a defined term, so the scan only accepts two unambiguous drafting signals:

  1. Definition form:   "<Term>" means …  /  "<Term>" shall mean …
     (also matches the canonical-marker variant `("<Term>") means …`)
  2. Canonical intro:   ("<Term>")  /  (the "<Term>")  /  (each a "<Term>") …
     — the SPEC §6 first-definition syntax; introduces a term with no `means` clause.

Both require the term to be quoted AND to start with an uppercase letter (defined
terms are capitalised). This skips ordinary quoted phrases ("goods", "as is") and
`{Reference}` / `[[Cross-ref]]` markers, at the cost of missing terms defined by
prose convention without either signal. Definition text is captured best-effort:
the run trims the post-`means` text to the first sentence/clause boundary, which a
period inside an abbreviation can cut short — acceptable for a hover snippet.

When both signals hit the same term, the `means` form wins (it carries a definition).
The upsert never regresses a stored definition to NULL (COALESCE), so a later run that
only sees the bare `("Term")` intro keeps an earlier `means` definition.
"""

from __future__ import annotations

import re
from typing import Any

from backend.models.defined_terms import ExtractedTerm, StoredDefinedTerm
from backend.models.imports import StoredNode

# "<Term>" means … / "<Term>" shall mean … — optional `)` covers the `("Term") means`
# canonical-marker variant. The definition body is sliced from after the match (not
# captured) so finditer advances correctly past multiple definitions on one line.
_MEANS = re.compile(
    r'["“]([^"”\n]{1,80})["”]\s*\)?\s*(?:shall\s+mean|means)\b[:,\s]*',
    re.IGNORECASE,
)

# Canonical introduction: ("Term"), (the "Term"), (each a "Term"), (collectively, the
# "Terms"). The optional lower-case lead-in (the/a/an/each/collectively…) is consumed
# but only the quoted term is captured.
_PAREN_INTRO = re.compile(
    r'\(\s*(?:[a-z][a-z, ]*\s+)?["“]([^"”\n]{1,80})["”]\s*\)',
)

# A sentence/clause terminator followed by whitespace or end-of-text.
_SENTENCE_END = re.compile(r"[.;](?:\s|$)")

_MAX_DEFINITION_CHARS = 600


class ContractNotFound(Exception):
    """Contract id does not resolve to a contract (so no deal to scope into)."""


def _is_term_like(term: str) -> bool:
    """Defined terms are capitalised; require a leading uppercase letter. Filters
    ordinary lower-case quoted phrases without rejecting multi-word Title Case."""
    t = term.strip()
    return bool(t) and t[0].isupper()


def _trim_definition(raw: str) -> str | None:
    """Best-effort: trim the post-`means` text to the first sentence/clause boundary
    and cap length. Returns None when nothing usable remains."""
    body = raw.strip()
    if not body:
        return None
    match = _SENTENCE_END.search(body)
    if match is not None:
        body = body[: match.start() + 1].strip()
    return body[:_MAX_DEFINITION_CHARS] or None


def _scan_text(text: str, source_node_id: str | None) -> list[ExtractedTerm]:
    """Deterministic scan of one text blob. `means` matches first (they carry a
    definition); canonical intros add terms not already found."""
    found: dict[str, ExtractedTerm] = {}
    for match in _MEANS.finditer(text):
        term = match.group(1).strip()
        if not _is_term_like(term) or term in found:
            continue
        found[term] = ExtractedTerm(
            term=term,
            definition=_trim_definition(text[match.end() :]),
            source_node_id=source_node_id,
        )
    for match in _PAREN_INTRO.finditer(text):
        term = match.group(1).strip()
        if not _is_term_like(term) or term in found:
            continue
        found[term] = ExtractedTerm(term=term, definition=None, source_node_id=source_node_id)
    return list(found.values())


def extract_terms_from_nodes(nodes: list[StoredNode]) -> list[ExtractedTerm]:
    """Scan every node's heading + body, merged across the contract. First node to
    yield a term wins its `source_node_id`; a later `means` definition upgrades a
    term first seen as a bare intro."""
    merged: dict[str, ExtractedTerm] = {}
    for node in nodes:
        for field_text in (node.heading, node.body):
            if not field_text:
                continue
            for extracted in _scan_text(field_text, node.id):
                existing = merged.get(extracted.term)
                if existing is None:
                    merged[extracted.term] = extracted
                elif existing.definition is None and extracted.definition is not None:
                    merged[extracted.term] = ExtractedTerm(
                        term=extracted.term,
                        definition=extracted.definition,
                        source_node_id=existing.source_node_id,
                    )
    return list(merged.values())


_GET_DEAL_ID = "SELECT deal_id FROM contracts WHERE id = $1"

_UPSERT_TERM = """
INSERT INTO defined_terms (deal_id, term, definition, source_node_id)
VALUES ($1, $2, $3, $4)
ON CONFLICT (deal_id, term) DO UPDATE SET
    definition     = COALESCE(EXCLUDED.definition, defined_terms.definition),
    source_node_id = COALESCE(EXCLUDED.source_node_id, defined_terms.source_node_id)
RETURNING id, deal_id, term, definition, source_node_id
"""

_LIST_TERMS = """
SELECT id, deal_id, term, definition, source_node_id
FROM defined_terms
WHERE deal_id = $1
ORDER BY term
"""


def _to_stored(record: Any) -> StoredDefinedTerm:
    source_node_id = record["source_node_id"]
    return StoredDefinedTerm(
        id=str(record["id"]),
        deal_id=str(record["deal_id"]),
        term=record["term"],
        definition=record["definition"],
        source_node_id=str(source_node_id) if source_node_id is not None else None,
    )


async def extract_and_store(conn: Any, contract_id: str) -> tuple[str, list[StoredDefinedTerm]]:
    """Resolve the contract's deal, scan its nodes, and upsert the terms into the
    deal-scoped registry. Returns (deal_id, the upserted rows). Re-running updates
    in place (UNIQUE(deal_id, term)) rather than duplicating."""
    from backend.services.contract_repo import fetch_nodes

    deal_id = await conn.fetchval(_GET_DEAL_ID, contract_id)
    if deal_id is None:
        raise ContractNotFound(contract_id)
    deal_id = str(deal_id)

    nodes = await fetch_nodes(conn, contract_id)
    extracted = extract_terms_from_nodes(nodes)

    stored: list[StoredDefinedTerm] = []
    async with conn.transaction():
        for term in extracted:
            record = await conn.fetchrow(
                _UPSERT_TERM, deal_id, term.term, term.definition, term.source_node_id
            )
            stored.append(_to_stored(record))
    return deal_id, stored


async def list_terms_for_deal(conn: Any, deal_id: str) -> list[StoredDefinedTerm]:
    records = await conn.fetch(_LIST_TERMS, deal_id)
    return [_to_stored(r) for r in records]


async def resolve_deal_id(conn: Any, contract_id: str) -> str:
    deal_id = await conn.fetchval(_GET_DEAL_ID, contract_id)
    if deal_id is None:
        raise ContractNotFound(contract_id)
    return str(deal_id)
