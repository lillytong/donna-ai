"""Models for the ephemeral brainstorm overlay (F10b, DD-73; storage shape DD-77).

Brainstorm is a STATELESS surface (DD-77): the client holds the running brainstorm
turns and replays them on each request; the backend persists NOTHING for brainstorm
until close. On close Donna distils ONE compact, operator-facing summary and stores
it on the issue (linked table `brainstorm_summaries`).

`BrainstormSummary` is the model's distilled output (question / conclusion / fallbacks),
parsed from the close-distillation LLM call. `DistillBrainstormResult` is the wrapper the
model returns so an honest empty (`summary: null`) is expressible when the brainstorm was
dismissed without substantive exploration (never manufacture — §2.4). `StoredBrainstormSummary`
mirrors a `brainstorm_summaries` row. The request/response models carry the running
transcript supplied by the client, since the backend keeps none of it."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.models.donna import DonnaTurn


class BrainstormSummary(BaseModel):
    """The distilled brainstorm output (DD-73): what was explored, where it landed, and the
    key fallbacks weighed + why each was passed over. Parsed from the close-distillation call."""

    question: str
    conclusion: str
    fallbacks: str


class DistillBrainstormResult(BaseModel):
    """The model's raw close-distillation output. `summary` is null (the honest, expected
    output) when the brainstorm was dismissed without substantive exploration — nothing to
    distil. Mirrors insights.DistillationResult's empty-is-valid contract."""

    summary: BrainstormSummary | None = None


class StoredBrainstormSummary(BaseModel):
    """A `brainstorm_summaries` row — one distilled pass over an issue's brainstorm."""

    id: str
    issue_id: str
    question: str | None = None
    conclusion: str | None = None
    fallbacks: str | None = None
    created_at: datetime


class BrainstormTurnRequest(BaseModel):
    """One stateless brainstorm turn (DD-77). `issue_id` anchors grounding (its clause + the
    committed ledger); `turns` is the running transcript the client holds (the backend keeps
    none of it); `message` is the new operator message. The backend persists NOTHING here."""

    issue_id: str
    turns: list[DonnaTurn] = Field(default_factory=list)
    message: str


class BrainstormTurnResponse(BaseModel):
    """Donna's next brainstorm reply. Transient — the client appends it to its running
    transcript and replays it next turn. `citations` are the grounded node/issue ids."""

    reply: str
    citations: list[str] = Field(default_factory=list)


class BrainstormCloseRequest(BaseModel):
    """Close the brainstorm (DD-73): distil `turns` (the full transcript the client holds)
    into one stored summary on `issue_id`. The transcript is discarded after distillation."""

    issue_id: str
    turns: list[DonnaTurn] = Field(default_factory=list)


class BrainstormSummariesResponse(BaseModel):
    """An issue's brainstorm history — every distilled pass, newest first (issue-detail)."""

    summaries: list[StoredBrainstormSummary] = Field(default_factory=list)
