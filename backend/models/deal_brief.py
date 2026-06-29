"""The per-contract deal brief (F37 / DD-95) — the global-context tier Donna distils from one
whole-contract read at import and the operator edits. Fills the {deal_context} grounding slot
for Donna's recommendations / chat / brainstorm (Part B wires that).

`content` is the whole free-text brief. `operator_edited` records whether the operator has
overwritten Donna's draft (edits win — an auto re-import re-distil respects it). `model` /
`generated_at` record the last Donna distillation (None until the first distil, or for an
operator-only edit). Used as the GET/PUT/refresh response; `DealBriefEdit` is the PUT body."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DealBrief(BaseModel):
    contract_id: str
    content: str = ""
    operator_edited: bool = False
    model: str | None = None
    generated_at: datetime | None = None
    updated_at: datetime | None = None


class DealBriefEdit(BaseModel):
    """The operator-edit PUT body — only the free-text content the operator authored."""

    content: str
