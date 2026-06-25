"""Models for Donna's per-change revision recommendation (F03c — the counterparty
revision reviewer surface; DD-78).

`RevisionRecommendation` is the model's raw structured output for ONE hunk: a verdict
(accept | counter | keep), a significance (trivial | substantive), one grounded line of
reasoning, and — only when the verdict is `counter` — exact counter-language the operator
may choose to send. Donna's counter-language is ADVISORY only (DD-64): it is written to the
hunk's `donna_counter_text` advisory column, never to its applied `final_text`.

`RevisionRecommendSummary` is the endpoint receipt: how many changes/hunks were analyzed and
the verdict tally across the session.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.models.revision_import import Significance

RevisionVerdict = Literal["accept", "counter", "keep"]


class RevisionRecommendation(BaseModel):
    """The model's raw structured recommendation for one hunk (pre-persistence).

    Invariant enforced in the service's `finalize_recommendation`, not here: `counter_language`
    is non-null iff `verdict == "counter"`, and a `trivial` hunk never carries counter-language
    (the `counterparty_revision_hunks.donna_counter_text` column is null for trivial hunks)."""

    verdict: RevisionVerdict
    significance: Significance
    reasoning: str
    counter_language: str | None = None


class VerdictTally(BaseModel):
    """Per-verdict hunk counts across an analyzed session."""

    accept: int = 0
    counter: int = 0
    keep: int = 0


class RevisionRecommendSummary(BaseModel):
    """Receipt for `POST /revisions/sessions/{session_id}/recommend`: how many not-yet-decided
    changes (and their hunks) Donna analyzed, and the verdict tally. Decided changes
    (status = complete) and unresolved abstains are skipped."""

    session_id: str
    changes_analyzed: int
    hunks_analyzed: int
    by_verdict: VerdictTally
