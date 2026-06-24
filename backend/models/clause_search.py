"""Request/response models for the conceptual clause-search surface.

The operator describes a clause in their own words; Donna returns the single
best conceptually-matching node (or nulls when nothing is a reasonable match).
`ClauseMatch` is the model's raw structured answer; `ClauseSearchResult` is the
API payload, adding the matched node's text."""

from __future__ import annotations

from pydantic import BaseModel


class ClauseSearchRequest(BaseModel):
    query: str


class ClauseMatch(BaseModel):
    """The model's structured answer: the chosen node id, or null for no match."""

    node_id: str | None = None


class ClauseSearchResult(BaseModel):
    node_id: str | None = None
    matched_text: str | None = None
