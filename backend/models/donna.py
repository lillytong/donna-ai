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

# F10b context-aware chat treatments. Richer than the persisted `kind` (which stays the
# three F10 values, schema-pinned): `mode` is mapped down to a `kind` on persistence
# (_mode_to_kind in services/donna/advise.py) so a rehydrated thread keeps F10 styling.
DonnaChatMode = Literal["explain", "advise", "draft", "legal_referral", "need_context"]


class DonnaMessage(BaseModel):
    role: DonnaRole
    content: str
    # Persisted on assistant turns only (DD-40 thread rehydration): `kind` is the answer
    # treatment, `citations` the validated node/issue ids. NULL on user turns / pre-migration
    # rows, which then render as plain grounded answers.
    kind: DonnaAnswerKind | None = None
    citations: list[str] | None = None
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


class DonnaContext(BaseModel):
    """The grounded anchor the operator is looking at when they ask (F10b). A POINTER,
    not a snapshot: `node_ids` are the selected clause node(s) and `issue_id` an open
    issue — both resolved LIVE from the DB each turn (never frozen into the window).
    Empty (no nodes, no issue) = no-context mode = read-and-explain (F10 preserved)."""

    node_ids: list[str] = Field(default_factory=list)
    issue_id: str | None = None


class DonnaAskRequest(BaseModel):
    question: str
    # Optional grounded anchor (F10b). When present and non-empty it unlocks COMMERCIAL
    # advice + drafting (never a legal opinion); when absent Donna stays read-and-explain.
    context: DonnaContext | None = None


class DonnaAskResponse(BaseModel):
    answer: str
    citations: list[str]
    deflected: bool
    kind: DonnaAnswerKind


class DonnaChatReply(BaseModel):
    """The model's raw structured chat output (F10b). `mode` routes the boundary
    (advise/draft/explain/legal_referral/need_context); `draft_language` carries clause
    text only on a draft turn; `citations` are the grounded node/issue ids."""

    reply: str
    mode: DonnaChatMode = "explain"
    citations: list[str] = Field(default_factory=list)
    draft_language: str | None = None


class DonnaChatResponse(BaseModel):
    """The /donna/ask API payload (F10b). `reply` is the prose Donna returns; `mode`
    tells the frontend which treatment to render; `draft_language` is present only on a
    draft turn (a transient clause the operator commits via the existing apply paths)."""

    reply: str
    mode: DonnaChatMode
    citations: list[str]
    draft_language: str | None = None


class DonnaThreadResponse(BaseModel):
    conversation_id: str
    running_summary: str | None = None
    messages: list[DonnaMessage]


class DonnaClearResponse(BaseModel):
    cleared: bool


class DonnaSeedBrainstormRequest(BaseModel):
    """Prime the Brainstorm opening turn (F10b) for an issue. The server composes the
    opening from the issue's CURRENT recommendation draft — the operator never authors an
    assistant turn (the content is server-controlled, not client-supplied)."""

    issue_id: str


class DonnaSeedBrainstormResponse(BaseModel):
    """The server-composed Brainstorm opening turn, persisted as one assistant message so a
    reloaded thread shows it. `seeded` is False (and `message` None) when the issue has no
    recommendation draft yet — nothing to restate (the route is a no-op)."""

    seeded: bool
    message: DonnaMessage | None = None
