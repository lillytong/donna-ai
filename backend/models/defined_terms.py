"""Defined-terms registry (F16, deal-scoped) — extraction + read models.

`ExtractedTerm` is the in-memory result of the deterministic scan over node text
(before persistence). `StoredDefinedTerm` is a row read back from `defined_terms`.
The registry is DEAL-scoped (DD-10): terms from any contract in the deal share one
namespace, keyed by `(deal_id, term)`.
"""

from __future__ import annotations

from pydantic import BaseModel


class ExtractedTerm(BaseModel):
    """A defined term detected in node text, before it is upserted into the deal
    registry. `definition` is None when only a canonical introduction `("Term")`
    was found (no `means` clause to capture); `source_node_id` is the node it was
    found in."""

    term: str
    definition: str | None = None
    source_node_id: str | None = None


class StoredDefinedTerm(BaseModel):
    """A `defined_terms` row read back from the DB."""

    id: str
    deal_id: str
    term: str
    definition: str | None = None
    source_node_id: str | None = None


class ExtractResponse(BaseModel):
    """POST .../extract response: what the run found for this contract (the rows
    now live in the deal-scoped registry)."""

    contract_id: str
    deal_id: str
    terms_found: int
    terms: list[StoredDefinedTerm]


class DefinedTermsResponse(BaseModel):
    """GET response: the deal's full defined-terms registry (for F05 hover)."""

    deal_id: str
    terms: list[StoredDefinedTerm]
