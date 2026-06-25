"""Models for Mode B Path-B revision import (F03b — clean-.docx counterparty/legal
revision ingest into the review staging tables).

The route takes a clean counterparty (or legal) .docx, the service parses + matches
it against the `last_shared_with_{party}` baseline snapshot, freezes the incoming
tree as an `as_received` snapshot (advancing the `received` pointer, DD-48), and
records the matcher's buckets into `counterparty_revision_{sessions,changes,hunks}`
as the review workspace (no issues created — SPEC §11 step 5). These models are the
typed request, the typed response (session summary + bucket counts), and the
stored-row mirrors of the three staging tables.

Path A (tracked-changes .docx) is detected and rejected (422) — an explicit
deferred follow-up; this build is clean-diff only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# The import-request source picker (SPEC §6 / DD-47). The DB `source` column uses
# `legal_team`; the request uses the operator-facing `legal`, mapped in the service.
RevisionSource = Literal["counterparty", "legal"]

ParsePath = Literal["tracked_changes", "clean_diff"]
HunkType = Literal["insertion", "deletion", "replacement"]
Significance = Literal["trivial", "substantive"]
ChangeStatus = Literal["pending", "partial", "complete"]


class RevisionImportRequest(BaseModel):
    """What the operator chose for this import: which counterpart's revision it is,
    and the original filename (for the audit trail / session record)."""

    source: RevisionSource
    source_filename: str | None = None


class RevisionImportResponse(BaseModel):
    """Session summary returned on a successful clean-diff import — the receipt the
    cockpit shows before entering F03c review. `version` is the as_received
    snapshot's lineage v-number (DD-70, derived from snapshot order).

    Bucket counts mirror the matcher's `RevisionMatchResult`, projected onto staged
    change rows: `edited_matches` (matched pairs whose bodies differ → a change row),
    `unchanged_matches` (matched, body identical → no row), `new`, `deleted`,
    `abstains` (low-confidence pairs flagged for operator match-confirm)."""

    session_id: str
    contract_id: str
    source: str
    parse_path: ParsePath
    baseline_snapshot_id: str
    as_received_snapshot_id: str
    received_pointer_party: str
    version: int
    status: str
    changes_count: int
    hunk_count: int
    edited_matches: int
    unchanged_matches: int
    new: int
    deleted: int
    abstains: int


class StoredRevisionSession(BaseModel):
    """Mirror of a `counterparty_revision_sessions` row."""

    id: str
    contract_id: str
    baseline_snapshot_id: str
    source: str
    source_filename: str | None
    parse_path: ParsePath
    status: str
    changes_count: int
    changes_reviewed_count: int
    imported_at: datetime


class StoredRevisionChange(BaseModel):
    """Mirror of a `counterparty_revision_changes` row. `node_id` null = a proposed
    NEW node or an ABSTAIN; the two are disambiguated by `proposed_order_index`
    (set for NEW, null for ABSTAIN — whose `proposed_parent_id` carries the
    provisional baseline candidate and `match_confidence` its score)."""

    id: str
    session_id: str
    node_id: str | None
    proposed_parent_id: str | None
    proposed_order_index: int | None
    match_confidence: float | None
    hunk_count: int
    hunks_decided: int
    status: ChangeStatus


class StoredRevisionHunk(BaseModel):
    """Mirror of a `counterparty_revision_hunks` row. `significance` defaults to
    `substantive` at staging time (the safe default — Donna's trivial/substantive
    classification is a deferred follow-up)."""

    id: str
    change_id: str
    hunk_type: HunkType
    significance: Significance
    position_in_body: int | None
    original_text: str | None
    proposed_text: str | None
    verdict: str


class HunkDraft(BaseModel):
    """A staged-but-unpersisted hunk: the deterministic difflib diff output for one
    text edit within a change, before it gets a DB id. `significance` is always
    `substantive` here (the staging default)."""

    hunk_type: HunkType
    position_in_body: int
    original_text: str | None
    proposed_text: str | None
    significance: Significance = "substantive"
