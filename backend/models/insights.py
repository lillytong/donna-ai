"""Models for negotiation-pattern distillation (F30, DD-76; amends DD-55/DD-73).

Donna distils compact, operator-global negotiation *patterns* from the COMMITTED issue
ledger when an issue closes (never the raw brainstorm transcript — grounding-safe by
construction). The store is merge-first and self-pruning, converging to ~100-200 records.

`CandidatePattern` is the model's raw extraction unit. It carries ONLY `subject_type`,
`insight`, and an optional pointer to an existing pattern it reinforces or contradicts —
NOT `subject_ref`. The subject reference (client_id / contract_type_id / null) is derived
deterministically from the closed issue's contract context by the service, never taken
from the model, so a hallucinated id can never reach the table.

`StoredPattern` mirrors a `negotiation_patterns` row. `PatternSubjectType` mirrors the
schema CHECK exactly; stored models accept the DB value as a plain str (the DB is canonical).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PatternSubjectType = Literal[
    "operator_style",
    "counterparty_behavior",
    "deal_type_norm",
    "legal_team_tendency",
]


class CandidatePattern(BaseModel):
    """One pattern the model proposes from a closed issue. `subject_ref` is intentionally
    absent — it is derived from the issue's contract context, never from the model.

    `reinforces_id` / `contradicts_id`: when the model is shown the small set of existing
    patterns for the relevant subjects (merge-first folded into the same call), it tags a
    candidate with the id of an existing pattern it reinforces (→ increment + refine) or
    contradicts (→ surface a contradiction flag, never silent overwrite). Both default
    null = a genuinely novel insight → new record."""

    subject_type: PatternSubjectType
    insight: str
    reinforces_id: str | None = None
    contradicts_id: str | None = None


class DistillationResult(BaseModel):
    """The model's structured extraction output: 0-N candidate patterns. An empty list is
    the honest, expected output when a closed issue holds no durable, transferable pattern
    (don't manufacture — DD-76)."""

    patterns: list[CandidatePattern] = Field(default_factory=list)


class StoredPattern(BaseModel):
    """A `negotiation_patterns` row. `subject_ref` is the polymorphic reference (null for
    operator_style/legal_team_tendency, client_id for counterparty_behavior, contract_type_id
    for deal_type_norm)."""

    id: str
    subject_type: str
    subject_ref: str | None = None
    insight: str
    evidence_count: int
    confidence: float
    contradiction_flag: bool
    last_reinforced_at: datetime
    last_reinforced_deal_id: str | None = None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
