"""Cross-reference extraction (F17) — deterministic scan + contract-scoped rebuild.

Scans each node's text for references to OTHER clauses in the SAME contract
("clause 12.3", "Section 5", "as set out in clause 4", "Schedule I", "Appendix B")
and stores a `cross_references` row per detected reference: source = the node the
reference sits in, target = the node the referenced NUMBER resolves to (or NULL when
it cannot be resolved). Rendered dynamically downstream (DD-11); the table is just
the resolved link graph.

PRECISION OVER RECALL (mirrors F16 defined-terms). A reference is only accepted when
it is introduced by an explicit drafting keyword (clause / section / paragraph /
article / schedule / appendix / annex / exhibit) immediately followed by a standalone
designator. This skips bare numbers, dates and amounts ("within 30 days", "5 January
2026", "$5 million") at the cost of missing references written without a keyword.

RESOLUTION reuses the export renderer's single numbering path (`_plan`, DD-02/DD-43)
so a resolved target carries the SAME clause number the clause shows everywhere else.
Only DECIMAL designators ("12.3", "5") resolve — they map onto the decimal-outline
clause numbers. Letter / roman designators (schedules, appendices) have no derived
number in this slice and are stored unresolved (target_node_id NULL). A reference that
resolves to its own source node is dropped (a clause citing its own number is noise).
"""

from __future__ import annotations

import re
from typing import Any

from backend.models.cross_references import ExtractedCrossReference, StoredCrossReference
from backend.models.imports import StoredNode
from backend.services.export.render_docx import _plan

# Reference keywords (precision gate). Plurals allowed so "clauses 4 and 5" is caught.
_KEYWORD = (
    r"sub-?clauses?|clauses?|sections?|paragraphs?|articles?|schedules?"
    r"|appendices|appendix|annexures?|annexes?|annex|exhibits?"
)

# A standalone designator: a decimal-outline number ("12.3"), a multi-letter roman
# numeral ("IV"), or a single letter ("B"). The trailing lookahead keeps it a whole
# token so the keyword's following word ("clause and …") is never read as a letter.
_DESIGNATOR = r"(?:\d+(?:\.\d+)*|[IVXLC]{2,6}|[A-Z])(?![A-Za-z])"

# keyword + first designator + an optional enumerated tail ("and 5", ", 4.2", "to 7").
_REF = re.compile(
    rf"\b(?P<kw>{_KEYWORD})\s+(?P<first>{_DESIGNATOR})"
    rf"(?P<tail>(?:\s*(?:,|&|and|to|through)\s+(?:{_DESIGNATOR}))*)",
    re.IGNORECASE,
)

# Pull individual designators back out of a matched tail. Each tail designator is
# preceded by an enumeration connector (",", "&", "and", "to", "through" — kept in
# sync with _REF's tail). Anchoring on the connector stops the connector word's own
# trailing letter ("an[d]", "t[o]", "throug[h]") from being misread as a single-letter
# designator: under IGNORECASE the bare [A-Z] branch of _DESIGNATOR matches a
# lowercase letter, so a raw re-scan of the tail produced spurious "clause d" refs.
_TAIL_DESIGNATOR = re.compile(rf"(?:,|&|and|to|through)\s+(?P<d>{_DESIGNATOR})", re.IGNORECASE)

_DECIMAL = re.compile(r"\d+(?:\.\d+)*")


class ContractNotFound(Exception):
    """Contract id does not resolve to a contract."""


def _kind_of(keyword: str) -> str:
    """Normalise a matched keyword to a singular lower-case kind label."""
    kw = keyword.lower().replace(" ", "").replace("-", "")
    if kw in {"appendix", "appendices"}:
        return "appendix"
    return kw[:-1] if kw.endswith("s") else kw


def extract_cross_references(text: str) -> list[ExtractedCrossReference]:
    """Pure deterministic scan of one text blob -> the references it contains, with
    `source_node_id`/`target_node_id` left None (bound + resolved by the node scan)."""
    refs: list[ExtractedCrossReference] = []
    for match in _REF.finditer(text):
        kind = _kind_of(match.group("kw"))
        designators = [match.group("first")]
        designators.extend(
            m.group("d") for m in _TAIL_DESIGNATOR.finditer(match.group("tail") or "")
        )
        for designator in designators:
            refs.append(
                ExtractedCrossReference(
                    kind=kind, designator=designator, label=f"{kind} {designator}"
                )
            )
    return refs


def build_number_map(nodes: list[StoredNode]) -> dict[str, str]:
    """Derived clause number string -> node id, via the shared export numbering
    (`_plan`, DD-43) so a resolved target matches the number shown app-wide."""
    return {
        number: node.id
        for node, number in _plan(nodes)
        if number is not None and node.role == "clause"
    }


def resolve_designator(designator: str, number_map: dict[str, str]) -> str | None:
    """A decimal designator ("12.3", "5") resolves to the clause carrying that number;
    a letter / roman designator (schedule, appendix) has no derived number -> None."""
    if _DECIMAL.fullmatch(designator):
        return number_map.get(designator)
    return None


def extract_cross_references_from_nodes(
    nodes: list[StoredNode],
) -> list[ExtractedCrossReference]:
    """Scan every node's heading + body, bind each reference to its source node and
    resolve its target through the shared numbering. Dropped: a reference that
    resolves to its own source node. Deduped per (source_node_id, designator)."""
    number_map = build_number_map(nodes)
    out: list[ExtractedCrossReference] = []
    for node in nodes:
        seen: set[str] = set()
        for field_text in (node.heading, node.body):
            if not field_text:
                continue
            for ref in extract_cross_references(field_text):
                if ref.designator in seen:
                    continue
                seen.add(ref.designator)
                target = resolve_designator(ref.designator, number_map)
                if target == node.id:
                    continue
                out.append(
                    ExtractedCrossReference(
                        kind=ref.kind,
                        designator=ref.designator,
                        label=ref.label,
                        source_node_id=node.id,
                        target_node_id=target,
                    )
                )
    return out


_GET_CONTRACT_ID = "SELECT id FROM contracts WHERE id = $1"

_DELETE_FOR_CONTRACT = "DELETE FROM cross_references WHERE source_contract_id = $1"

_INSERT_REF = """
INSERT INTO cross_references
    (source_node_id, source_contract_id, target_node_id, target_contract_id)
VALUES ($1, $2, $3, $4)
RETURNING id, source_node_id, source_contract_id, target_node_id, target_contract_id
"""

_LIST_REFS = """
SELECT id, source_node_id, source_contract_id, target_node_id, target_contract_id
FROM cross_references
WHERE source_contract_id = $1
ORDER BY source_node_id
"""


def _to_stored(record: Any, label: str | None = None) -> StoredCrossReference:
    target_node_id = record["target_node_id"]
    target_contract_id = record["target_contract_id"]
    return StoredCrossReference(
        id=str(record["id"]),
        source_node_id=str(record["source_node_id"]),
        source_contract_id=str(record["source_contract_id"]),
        target_node_id=str(target_node_id) if target_node_id is not None else None,
        target_contract_id=str(target_contract_id) if target_contract_id is not None else None,
        label=label,
        resolved=target_node_id is not None,
    )


async def persist_cross_references(
    conn: Any, contract_id: str, nodes: list[StoredNode]
) -> list[StoredCrossReference]:
    """Rebuild this contract's cross-reference rows from its nodes: clear the existing
    rows then insert the freshly scanned set (idempotent — re-running converges rather
    than duplicating). A resolved target sets both target columns to the same contract;
    an unresolved reference leaves both NULL."""
    refs = extract_cross_references_from_nodes(nodes)
    stored: list[StoredCrossReference] = []
    async with conn.transaction():
        await conn.execute(_DELETE_FOR_CONTRACT, contract_id)
        for ref in refs:
            target_contract_id = contract_id if ref.target_node_id is not None else None
            record = await conn.fetchrow(
                _INSERT_REF,
                ref.source_node_id,
                contract_id,
                ref.target_node_id,
                target_contract_id,
            )
            stored.append(_to_stored(record, label=ref.label))
    return stored


async def list_cross_references(conn: Any, contract_id: str) -> list[StoredCrossReference]:
    records = await conn.fetch(_LIST_REFS, contract_id)
    return [_to_stored(r) for r in records]


async def extract_and_store(
    conn: Any, contract_id: str
) -> tuple[str, list[StoredCrossReference]]:
    """Confirm the contract exists, fetch its nodes, and rebuild its cross-reference
    rows. Returns (contract_id, the stored rows)."""
    from backend.services.contract_repo import fetch_nodes

    exists = await conn.fetchval(_GET_CONTRACT_ID, contract_id)
    if exists is None:
        raise ContractNotFound(contract_id)

    nodes = await fetch_nodes(conn, contract_id)
    stored = await persist_cross_references(conn, contract_id, nodes)
    return contract_id, stored
