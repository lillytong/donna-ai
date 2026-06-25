"""Models for the Donna single-contract grounded Q&A surface (F10).

`DonnaStructuredAnswer` is the model's raw structured output (answer + cited ids +
a `kind` flag that drives read-and-explain routing). `DonnaAskResponse` is the API
payload (adds the derived `deflected` bool). `DonnaTurn` is a paired user/assistant
exchange — the unit the DD-40 window counts. Conversation state lives in the existing
`donna_conversations` / `donna_messages` tables (db/schema.sql)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DonnaAnswerKind = Literal["answer", "not_found", "deflected"]
DonnaRole = Literal["user", "assistant"]


class DonnaMessage(BaseModel):
    role: DonnaRole
    content: str
    created_at: datetime | None = None


class DonnaTurn(BaseModel):
    """One paired exchange (the DD-40 windowing unit)."""

    question: str
    answer: str


class StoredConversation(BaseModel):
    id: str
    contract_id: str
    running_summary: str | None = None


class DonnaStructuredAnswer(BaseModel):
    """The model's raw structured answer. `kind` routes behaviour: `answer` =
    grounded explanation, `not_found` = honest miss, `deflected` = advice/position
    request redirected (read-and-explain guardrail, DD-14)."""

    answer: str
    kind: DonnaAnswerKind = "answer"
    citations: list[str] = Field(default_factory=list)


class DonnaAskRequest(BaseModel):
    question: str


class DonnaAskResponse(BaseModel):
    answer: str
    citations: list[str]
    deflected: bool
    kind: DonnaAnswerKind


class DonnaThreadResponse(BaseModel):
    conversation_id: str
    running_summary: str | None = None
    messages: list[DonnaMessage]


class DonnaClearResponse(BaseModel):
    cleared: bool
