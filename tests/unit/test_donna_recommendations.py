"""Pure logic for Donna's issue recommendations (F11): structured-output parse + honest
fallback, the citation guard + id scrub (finalize_draft), issue-focus grounding, and the
confirm-copy transaction (draft -> issues.*, DD-68). No LLM, no live DB."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from backend.models.audit import (
    EVENT_RECOMMENDATION_CONFIRMED,
    AuditEvent,
    StoredAuditEvent,
)
from backend.models.issues import StoredIssue
from backend.models.recommendations import RecommendationDraft
from backend.services.donna import recommendation_repo
from backend.services.donna.grounding import build_issue_focus, build_label_map
from backend.services.donna.recommendations import finalize_draft, parse_draft

# --- parse_draft -----------------------------------------------------------


def test_parse_draft_reads_structured_fields() -> None:
    draft = parse_draft(
        '{"rationale": "Cap is favorable-but-fair.", "draft_recommended_position": "Keep 12mo.",'
        ' "draft_counter_language": "Liability shall not exceed...", "citations": ["n-liab"],'
        ' "missing_benchmark": false}'
    )
    assert draft.draft_recommended_position == "Keep 12mo."
    assert draft.draft_counter_language.startswith("Liability")  # type: ignore[union-attr]
    assert draft.citations == ["n-liab"]
    assert draft.missing_benchmark is False


def test_parse_draft_tolerates_surrounding_prose() -> None:
    draft = parse_draft(
        'Here:\n{"rationale": "x", "draft_recommended_position": null,'
        ' "draft_counter_language": null, "citations": [], "missing_benchmark": true}\nthanks'
    )
    assert draft.missing_benchmark is True
    assert draft.draft_recommended_position is None


def test_parse_draft_unparseable_is_honest_fallback() -> None:
    draft = parse_draft("sorry, no json")
    assert draft.draft_recommended_position is None
    assert draft.draft_counter_language is None
    assert draft.citations == []
    assert draft.missing_benchmark is False
    assert draft.rationale  # a non-empty honest message, never fabricated


# --- finalize_draft (citation guard + id scrub) ----------------------------


def test_finalize_drops_hallucinated_citations() -> None:
    draft = RecommendationDraft(rationale="ok", citations=["n-liab", "n-ghost"])
    out = finalize_draft(draft, valid_ids={"n-liab"}, id_labels={})
    assert out.citations == ["n-liab"]


def test_finalize_scrubs_leaked_id_from_every_prose_field() -> None:
    draft = RecommendationDraft(
        rationale="See n-liab for the cap.",
        draft_recommended_position="Anchor on n-liab.",
        draft_counter_language="Per n-liab, liability is capped.",
        citations=["n-liab"],
    )
    out = finalize_draft(
        draft, valid_ids={"n-liab"}, id_labels={"n-liab": "clause 6.1 (Limitation of Liability)"}
    )
    for field in (out.rationale, out.draft_recommended_position, out.draft_counter_language):
        assert "n-liab" not in field  # type: ignore[operator]
        assert "clause 6.1 (Limitation of Liability)" in field  # type: ignore[operator]
    assert out.citations == ["n-liab"]  # the array keeps the real id


def test_finalize_leaves_null_draft_fields_null() -> None:
    out = finalize_draft(RecommendationDraft(rationale="x"), valid_ids=set(), id_labels={})
    assert out.draft_recommended_position is None
    assert out.draft_counter_language is None


# --- issue-focus grounding -------------------------------------------------


def _issue(issue_id: str, **kw: Any) -> StoredIssue:
    base: dict[str, Any] = dict(
        id=issue_id,
        contract_id="c1",
        title="Liability cap level",
        status="open",
        initiator="operator",
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        created_at=datetime(2026, 1, 1),
    )
    base.update(kw)
    return StoredIssue(**base)


def test_issue_focus_uses_label_not_raw_id_and_spells_out_stance() -> None:
    labels = {"n-liab": "clause 6.1 (Limitation of Liability)"}
    block = build_issue_focus(
        _issue("i1", node_id="n-liab", initiator="counterparty", their_position="uncapped"), labels
    )
    assert "clause 6.1 (Limitation of Liability)" in block
    assert "n-liab" not in block
    assert "countering" in block  # counterparty stance -> we counter
    assert "uncapped" in block


def test_issue_focus_marks_free_floating_and_propose_stance() -> None:
    block = build_issue_focus(_issue("i2", node_id=None, initiator="operator"), {})
    assert "contract-level (free-floating)" in block
    assert "proposing" in block


# --- confirm-copy transaction (draft -> issues.*, DD-68) -------------------


def _rec_row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id="r1",
        issue_id="i1",
        rationale="Cap is favorable-but-fair.",
        draft_recommended_position="Keep the twelve-month cap.",
        draft_counter_language="Liability shall not exceed the fees paid.",
        citations='["n-liab"]',  # JSONB comes back as text under asyncpg
        model="claude-opus-4-8",
        generated_at=datetime(2026, 6, 25, tzinfo=UTC),
        confirmed=False,
    )
    base.update(kw)
    return base


class _FakeConn:
    """Returns the draft row before/after confirm and records executes with their txn
    state, so the copy + audit are asserted to run inside the transaction."""

    def __init__(self, rows: list[dict[str, Any] | None]) -> None:
        self._rows = rows
        self._fetches = 0
        self.in_txn = False
        self.executes: list[tuple[str, bool]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.in_txn = True
        try:
            yield
        finally:
            self.in_txn = False

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        row = self._rows[min(self._fetches, len(self._rows) - 1)]
        self._fetches += 1
        return row

    async def execute(self, sql: str, *_args: Any) -> str:
        self.executes.append((sql, self.in_txn))
        return "UPDATE 1"


def _stub_record(captured: dict[str, Any]) -> Any:
    async def _record(conn: Any, event: AuditEvent) -> StoredAuditEvent:
        captured["event"] = event
        captured["in_txn"] = conn.in_txn
        return StoredAuditEvent(
            id="a1",
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            actor=event.actor,
            payload=event.payload,
            created_at=datetime(2026, 6, 25, tzinfo=UTC),
        )

    return _record


async def test_confirm_copies_draft_to_issue_in_one_txn(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(recommendation_repo, "record_event", _stub_record(captured))
    conn = _FakeConn([_rec_row(), _rec_row(confirmed=True)])

    result = await recommendation_repo.confirm(conn, "i1")

    assert result is not None and result.confirmed is True
    assert result.citations == ["n-liab"]  # JSONB text decoded
    # The confirm-flag flip, the draft->issues copy, and the audit all ran in the txn.
    flip = next(s for s, _ in conn.executes if "SET confirmed = true" in s)
    copy = next((s, t) for s, t in conn.executes if "UPDATE issues" in s)
    assert "SET confirmed = true" in flip
    assert "donna_recommendations r" in copy[0] and copy[1] is True  # copy ran inside the txn
    assert all(in_txn for _, in_txn in conn.executes)
    assert captured["event"].event_type == EVENT_RECOMMENDATION_CONFIRMED
    assert captured["event"].entity_id == "i1"
    assert captured["in_txn"] is True


async def test_confirm_returns_none_when_no_draft() -> None:
    conn = _FakeConn([None])
    assert await recommendation_repo.confirm(conn, "i-missing") is None
    assert conn.executes == []  # nothing written when there is no draft


# --- upsert / get round-trip ----------------------------------------------


class _UpsertConn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.last_args: tuple[Any, ...] = ()

    async def fetchrow(self, _sql: str, *args: Any) -> dict[str, Any] | None:
        self.last_args = args
        return self._row


async def test_upsert_serializes_citations_and_returns_parsed() -> None:
    conn = _UpsertConn(_rec_row(citations='["n-liab", "i1"]'))
    draft = RecommendationDraft(rationale="x", citations=["n-liab", "i1"])
    stored = await recommendation_repo.upsert_draft(conn, "i1", draft, "claude-opus-4-8")
    # citations are passed to SQL as a JSON string (the ::jsonb cast), not a Python list.
    assert conn.last_args[4] == '["n-liab", "i1"]'
    assert stored.citations == ["n-liab", "i1"]
    assert stored.confirmed is False


async def test_get_by_issue_returns_none_when_missing() -> None:
    conn = _UpsertConn(None)
    assert await recommendation_repo.get_by_issue(conn, "i-x") is None


def test_build_label_map_smoke() -> None:
    # Guards the eval/service shared dependency on the export numbering path.
    from backend.models.imports import StoredNode

    nodes = [
        StoredNode(
            id="n1",
            parent_id=None,
            order_index=0,
            content_type="prose",
            heading="Liability",
            role="clause",
        )
    ]
    assert build_label_map(nodes)["n1"].startswith("clause 1")
