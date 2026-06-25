"""Models for Donna-assisted clause drafting (F08d) — the "Draft with Donna" path in the
cockpit ⋮ insert menu (DD-13). The operator describes a missing clause; Donna drafts the
complete language grounded in deal type + the surrounding clauses. The draft is **transient**
— it pre-fills the insert editor and the operator reviews/edits before committing it through
the normal F08b create path (it is never persisted by the drafting call itself).

`ClauseDraft` is the model's raw structured output: an optional short `heading`, the clause
`body`, and the ids of the context clauses it grounded on (validated against the real node set,
hallucinated ids dropped — as F11). An empty `body` is the honest "couldn't draft" signal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InsertMode = Literal["below", "sub", "above"]


class ClauseDraftRequest(BaseModel):
    """What the operator asked for, plus where the new clause will land so Donna can match
    the level/voice. `anchor_node_id` is the clause the insert is relative to (None when
    inserting with no selection); `mode` mirrors the ⋮ menu (below / sub / above)."""

    description: str
    anchor_node_id: str | None = None
    mode: InsertMode = "below"


class ClauseDraft(BaseModel):
    """Donna's drafted clause (pre-persistence). `heading` is an optional short title (null
    for a plain operative clause); `body` is the clause language the editor pre-fills;
    `citations` are the surrounding-clause ids it drew on. Empty `body` = couldn't draft."""

    heading: str | None = None
    body: str = ""
    citations: list[str] = Field(default_factory=list)
