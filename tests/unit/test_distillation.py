"""Pure logic for F30 negotiation-pattern distillation (DD-76): structured-output parse +
honest empty fallback, subject_ref derivation (from contract context, never the model), the
existing-patterns prompt block, and the merge-first apply (reinforce real id / insert on a
hallucinated id / flag a contradiction / skip empty). No LLM, no live DB."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.models.insights import CandidatePattern, StoredPattern
from backend.services.donna.distillation import (
    apply_candidates,
    build_existing_block,
    parse_distillation,
    subject_ref_for,
)

# --- parse_distillation ------------------------------------------------------


def test_parse_reads_candidate_patterns() -> None:
    text = (
        '{"patterns": [{"subject_type": "counterparty_behavior", '
        '"insight": "Pushes hard on liability caps.", "reinforces_id": "p1"}]}'
    )
    result = parse_distillation(text)
    assert len(result.patterns) == 1
    assert result.patterns[0].subject_type == "counterparty_behavior"
    assert result.patterns[0].reinforces_id == "p1"


def test_parse_tolerates_surrounding_prose() -> None:
    text = 'Here you go:\n{"patterns": []}\nhope that helps'
    assert parse_distillation(text).patterns == []


def test_parse_unparseable_is_empty() -> None:
    assert parse_distillation("not json at all").patterns == []


# --- subject_ref_for (derived from context, NEVER the model) -----------------


def test_subject_ref_counterparty_keys_on_client() -> None:
    assert subject_ref_for("counterparty_behavior", "client-1", "ct-1") == "client-1"


def test_subject_ref_deal_type_keys_on_contract_type() -> None:
    assert subject_ref_for("deal_type_norm", "client-1", "ct-1") == "ct-1"


def test_subject_ref_operator_and_legal_are_global() -> None:
    assert subject_ref_for("operator_style", "client-1", "ct-1") is None
    assert subject_ref_for("legal_team_tendency", "client-1", "ct-1") is None


# --- build_existing_block ----------------------------------------------------


def test_existing_block_empty() -> None:
    assert "no existing patterns" in build_existing_block([])


def test_existing_block_lists_id_type_insight() -> None:
    p = _pattern(id="p7", subject_type="operator_style", insight="Holds the cap firm.")
    block = build_existing_block([p])
    assert "[p7]" in block and "operator_style" in block and "Holds the cap firm." in block


# --- apply_candidates (merge-first) ------------------------------------------


def _pattern(**kw: Any) -> StoredPattern:
    base: dict[str, Any] = dict(
        id="p1",
        subject_type="counterparty_behavior",
        subject_ref="client-1",
        insight="Pushes on caps.",
        evidence_count=1,
        confidence=0.5,
        contradiction_flag=False,
        last_reinforced_at=datetime(2026, 6, 25, tzinfo=UTC),
        last_reinforced_deal_id=None,
        is_deleted=False,
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, tzinfo=UTC),
    )
    base.update(kw)
    return StoredPattern(**base)


def _row(**kw: Any) -> dict[str, Any]:
    p = _pattern(**kw)
    return p.model_dump()


class _FakeConn:
    """Records (sql, args) for execute + fetchrow; fetchrow echoes a stored row so the
    merge/insert paths are asserted by which SQL ran + with what args."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "UPDATE 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.calls.append((sql, args))
        return _row(id=args[0] if "UPDATE" in sql else "new-id")


async def test_apply_reinforces_a_real_existing_id() -> None:
    conn = _FakeConn()
    existing = [_pattern(id="p1")]
    cand = CandidatePattern(
        subject_type="counterparty_behavior", insight="Refined.", reinforces_id="p1"
    )
    out = await apply_candidates(
        conn, [cand], existing, client_id="client-1", contract_type_id="ct-1", deal_id="d1"
    )
    assert len(out) == 1
    assert any("evidence_count = evidence_count + 1" in sql for sql, _ in conn.calls)  # reinforced
    assert not any("INSERT INTO negotiation_patterns" in sql for sql, _ in conn.calls)


async def test_apply_hallucinated_reinforce_id_falls_through_to_insert() -> None:
    conn = _FakeConn()
    existing = [_pattern(id="p1")]
    cand = CandidatePattern(
        subject_type="deal_type_norm", insight="New norm.", reinforces_id="GHOST"
    )
    out = await apply_candidates(
        conn, [cand], existing, client_id="client-1", contract_type_id="ct-9", deal_id="d1"
    )
    assert len(out) == 1
    insert = next(args for sql, args in conn.calls if "INSERT INTO negotiation_patterns" in sql)
    # subject_ref derived from context (deal_type_norm → contract_type_id), not the model
    assert insert[1] == "ct-9"


async def test_apply_real_contradiction_sets_flag() -> None:
    conn = _FakeConn()
    existing = [_pattern(id="p1")]
    cand = CandidatePattern(
        subject_type="counterparty_behavior", insight="Opposite.", contradicts_id="p1"
    )
    await apply_candidates(
        conn, [cand], existing, client_id="client-1", contract_type_id="ct-1", deal_id="d1"
    )
    assert any("contradiction_flag = true" in sql for sql, _ in conn.calls)


async def test_apply_skips_empty_insight() -> None:
    conn = _FakeConn()
    cand = CandidatePattern(subject_type="operator_style", insight="   ")
    out = await apply_candidates(
        conn, [cand], [], client_id=None, contract_type_id=None, deal_id="d1"
    )
    assert out == [] and conn.calls == []
