"""Models for Donna's issue-recommendation layer (F11) — the advisory surface scoped
to a single issue (DD-14, DD-68).

`RecommendationDraft` is the model's raw structured output: a grounded rationale plus
the two draftable fields (a recommended landing position and/or exact counter-language —
propose vs counter, same engine, different field) and the cited ids. `missing_benchmark`
is Donna's honest flag that a market figure was needed but not available (no fabrication —
F29/live research is out of v1). `StoredRecommendation` mirrors the `donna_recommendations`
row (the DRAFT, held apart from `issues.*` until confirmed — DD-68). `RecommendationConfirmResponse`
reports the copy of draft -> issues on [Use Donna's language].
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RecommendationDraft(BaseModel):
    """The model's raw structured recommendation (pre-persistence). `missing_benchmark`
    flags an honest market-data gap — recommend the structure, never invent a number."""

    rationale: str
    draft_recommended_position: str | None = None
    draft_counter_language: str | None = None
    citations: list[str] = Field(default_factory=list)
    missing_benchmark: bool = False


class StoredRecommendation(BaseModel):
    """A `donna_recommendations` row — the persisted DRAFT (DD-68). Not exported until
    confirmed; `citations` is the validated id list read back from JSONB."""

    id: str
    issue_id: str
    rationale: str
    draft_recommended_position: str | None = None
    draft_counter_language: str | None = None
    citations: list[str] | None = None
    model: str
    generated_at: datetime
    confirmed: bool


class RecommendationConfirmRequest(BaseModel):
    """Optional body on [Use Donna's language] when the operator edited the drafted
    language first ([Edit]). Both fields carry the edited values to confirm into the
    issue's exported fields; a plain confirm (no body) copies the stored draft verbatim
    (DD-68 addendum — operator-edited language is still operator-confirmed language)."""

    edited_recommended_position: str | None = None
    edited_counter_language: str | None = None


class RecommendationConfirmResponse(BaseModel):
    """The result of [Use Donna's language]: the draft was copied into the issue's
    exported fields (DD-68)."""

    issue_id: str
    confirmed: bool
    recommended_position: str | None = None
    donna_counter_language: str | None = None
