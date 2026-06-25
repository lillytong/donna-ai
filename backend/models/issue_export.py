"""Issue-list export rows (F31, DD-60).

The .docx table is a projection of the unresolved issues, not a new persisted
entity: each `IssueRow` is one rendered table line with only the eight
counterparty-safe columns (§9). `IssueListExport` splits the rows into
clause-anchored and contract-level (free-floating) groups so the renderer can
order anchored issues first and drop the free-floating group last under a
separator (DD-60). No DB ids, comment threads, internal flags, or Donna
attribution are carried — those columns intentionally do not exist on the model.
"""

from __future__ import annotations

from pydantic import BaseModel


class IssueRow(BaseModel):
    number: str
    clause: str
    issue: str
    status: str
    raised_by: str
    our_position: str
    their_position: str
    proposed_resolution: str


class IssueListExport(BaseModel):
    anchored: list[IssueRow]
    floating: list[IssueRow]
