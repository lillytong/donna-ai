"""Issue + comment model validation (pure logic): defaults, enums, the F06
creation contract that Donna's analysis fields are never set at creation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.models.issues import (
    CommentCreate,
    IssueCreate,
    IssueStatusUpdate,
    StoredIssue,
)
from pydantic import ValidationError

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def test_issue_create_defaults() -> None:
    issue = IssueCreate(contract_id="c1", title="Royalty rate")
    assert issue.node_id is None  # free-floating until anchored (F08c)
    assert issue.category == "commercial"
    assert issue.authority == "within-operator-authority"
    assert issue.needs_legal_review is False
    assert issue.our_position is None
    assert issue.their_position is None
    assert issue.initiator == "operator"  # F06: we raised it, by default


def test_issue_create_accepts_counterparty_initiator() -> None:
    # F06: operator flags that the COUNTERPARTY raised the issue.
    issue = IssueCreate(contract_id="c1", title="x", initiator="counterparty")
    assert issue.initiator == "counterparty"


def test_issue_create_rejects_donna_initiator() -> None:
    # `donna` is reserved for the F28 auto-flag path, not this create surface.
    with pytest.raises(ValidationError):
        IssueCreate(contract_id="c1", title="x", initiator="donna")  # type: ignore[arg-type]


def test_issue_create_has_no_donna_analysis_fields() -> None:
    # F06: Donna's analysis fields are not part of the creation surface.
    fields = set(IssueCreate.model_fields)
    assert "recommended_position" not in fields
    assert "donna_counter_language" not in fields
    assert "auto_flag" not in fields
    assert "donna_research_citations" not in fields


def test_issue_create_rejects_bad_category() -> None:
    with pytest.raises(ValidationError):
        IssueCreate(contract_id="c1", title="x", category="bogus")  # type: ignore[arg-type]


def test_issue_create_rejects_bad_authority() -> None:
    with pytest.raises(ValidationError):
        IssueCreate(contract_id="c1", title="x", authority="anything")  # type: ignore[arg-type]


def test_status_update_rejects_bad_status() -> None:
    with pytest.raises(ValidationError):
        IssueStatusUpdate(status="closed")  # type: ignore[arg-type]


def test_status_update_accepts_decision_passthrough() -> None:
    update = IssueStatusUpdate(
        status="agreed",
        decision={"verdict": "accept_theirs", "actor": "user"},
    )
    assert update.decision == {"verdict": "accept_theirs", "actor": "user"}


def test_comment_rejects_bad_actor() -> None:
    with pytest.raises(ValidationError):
        CommentCreate(issue_id="i1", actor="robot", content="hi")  # type: ignore[arg-type]


def test_comment_issue_id_optional_for_path_override() -> None:
    comment = CommentCreate(actor="user", content="note")
    assert comment.issue_id is None


def test_stored_issue_carries_jsonb_passthrough() -> None:
    stored = StoredIssue(
        id="i1",
        contract_id="c1",
        title="Royalty rate",
        status="open",
        initiator="operator",
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        auto_flag=None,
        decision=None,
        created_at=_NOW,
    )
    assert stored.node_id is None
    assert stored.resolved_at is None
    assert stored.priority is None
