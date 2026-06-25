"""Mark-as-sent models (DD-71) — the boundary event decoupled from export.

donna.ai cannot actually send (no email/transport), so the operator exports the
.docx, sends it manually, then records that it went out. "Mark as sent →
{Counterparty | Legal | Both}" is that record: it cuts an immutable snapshot of
the CURRENT working copy (F14 `services/snapshot.py`, unchanged), advances the
matching DD-48 `last_shared_with_X` pointer(s) — one snapshot may carry both —
and mints the next lineage v-number (DD-70, derived from the snapshot count).

`acknowledge_drift` carries the operator's one-click-through past the non-blocking
DD-72 drift warning: a first call with drift unacknowledged returns `marked=False`
(nothing cut) so the UI can show "edited since last export — Mark anyway /
Re-export"; a second call with `acknowledge_drift=True` performs the mark.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MarkSentRecipient = Literal["counterparty", "legal", "both"]


class MarkSentRequest(BaseModel):
    recipient: MarkSentRecipient
    acknowledge_drift: bool = False


class MarkSentResult(BaseModel):
    """The outcome of a Mark-as-sent call.

    `marked=False` (with `drift=True`) is the drift-preview: the working copy was
    edited since the last export and `acknowledge_drift` was not set, so no snapshot
    was cut — the UI shows the non-blocking warning and re-calls to proceed. When
    `marked=True`, `snapshot_id` is the cut snapshot and `pointers` lists the DD-48
    pointer parties advanced to it."""

    marked: bool
    drift: bool
    recipient: MarkSentRecipient
    version: int
    pointers: list[str]
    snapshot_id: str | None = None
    last_export_at: datetime | None = None
