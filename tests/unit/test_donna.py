"""Pure logic for Donna Q&A (F10): windowing (DD-40), grounding assembly, citation
parsing + hallucinated-id guard, and deflection routing. No LLM, no DB."""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.models.donna import DonnaMessage
from backend.models.imports import StoredNode
from backend.models.issues import StoredIssue
from backend.services.donna import grounding, windowing
from backend.services.donna.qa import parse_answer, scrub_leaked_ids

# --- windowing (DD-40) -----------------------------------------------------


def _messages(n_turns: int) -> list[DonnaMessage]:
    base = datetime(2026, 1, 1)
    out: list[DonnaMessage] = []
    for i in range(n_turns):
        out.append(
            DonnaMessage(role="user", content=f"q{i}", created_at=base + timedelta(seconds=2 * i))
        )
        out.append(
            DonnaMessage(
                role="assistant", content=f"a{i}", created_at=base + timedelta(seconds=2 * i + 1)
            )
        )
    return out


def test_to_turns_pairs_user_then_assistant() -> None:
    turns = windowing.to_turns(_messages(3))
    assert [(t.question, t.answer) for t in turns] == [("q0", "a0"), ("q1", "a1"), ("q2", "a2")]


def test_to_turns_ignores_trailing_unanswered_user() -> None:
    msgs = [*_messages(1), DonnaMessage(role="user", content="dangling")]
    assert [t.question for t in windowing.to_turns(msgs)] == ["q0"]


def test_window_keeps_only_last_n_turns() -> None:
    turns = windowing.to_turns(_messages(windowing.WINDOW_TURNS + 4))
    win = windowing.window(turns)
    assert len(win) == windowing.WINDOW_TURNS
    assert win[-1].question == f"q{windowing.WINDOW_TURNS + 3}"
    assert win[0].question == "q4"


def test_evicted_turn_none_within_window() -> None:
    turns = windowing.to_turns(_messages(windowing.WINDOW_TURNS))
    assert windowing.evicted_turn(turns) is None


def test_evicted_turn_is_the_one_just_pushed_out() -> None:
    # One turn past the window -> turn 0 is the single eviction.
    turns = windowing.to_turns(_messages(windowing.WINDOW_TURNS + 1))
    evicted = windowing.evicted_turn(turns)
    assert evicted is not None and evicted.question == "q0"
    # Two past -> turn 1 is the most recent eviction (incremental, one per new turn).
    turns2 = windowing.to_turns(_messages(windowing.WINDOW_TURNS + 2))
    assert windowing.evicted_turn(turns2).question == "q1"  # type: ignore[union-attr]


# --- grounding assembly ----------------------------------------------------


def _node(node_id: str, **kw: object) -> StoredNode:
    base: dict[str, object] = dict(id=node_id, parent_id=None, order_index=0, content_type="prose")
    base.update(kw)
    return StoredNode(**base)


def test_clause_grounding_tags_matched_subtree_with_ids() -> None:
    nodes = [
        _node("h1", heading="Confidentiality", order_index=0),
        _node(
            "b1", parent_id="h1", body="Each party keeps the other's info secret.", order_index=1
        ),
        _node("h2", heading="Term", order_index=2),
    ]
    labels = grounding.build_label_map(nodes)
    block = grounding.build_clause_grounding(nodes, "h1", labels)
    assert "[h1]" in block and "Confidentiality" in block
    assert "[b1]" in block and "secret" in block
    # An unrelated clause is not pulled in.
    assert "[h2]" not in block


def test_clause_grounding_carries_legible_label_after_id() -> None:
    nodes = [_node("h1", heading="Confidentiality", role="clause", order_index=0)]
    labels = grounding.build_label_map(nodes)
    block = grounding.build_clause_grounding(nodes, "h1", labels)
    # The derived clause number (h1 is the first clause -> "1") and the heading appear
    # as the line's label, formatted apart from the bracketed id.
    assert "[h1] clause 1 (Confidentiality) —" in block


def test_label_map_labels_non_clause_by_content_type() -> None:
    nodes = [
        _node("r1", heading="Recitals", role="recital", order_index=0),
        _node("a1", body="Schedule body text.", role="appendix", order_index=1),
        _node("a2", heading="Annex II", role="appendix_title", order_index=2),
    ]
    labels = grounding.build_label_map(nodes)
    assert labels["r1"] == "Recital (Recitals)"
    assert labels["a1"] == "Appendix body"
    assert labels["a2"] == "Appendix title (Annex II)"


def test_clause_grounding_empty_on_no_match() -> None:
    nodes = [_node("h1", heading="X")]
    labels = grounding.build_label_map(nodes)
    assert grounding.build_clause_grounding(nodes, None, labels) == ""
    assert grounding.build_clause_grounding(nodes, "missing", labels) == ""


def _issue(issue_id: str, **kw: object) -> StoredIssue:
    base: dict[str, object] = dict(
        id=issue_id,
        contract_id="c1",
        title="Liability cap",
        status="open",
        initiator="operator",
        authority="within-operator-authority",
        needs_legal_review=False,
        category="commercial",
        created_at=datetime(2026, 1, 1),
    )
    base.update(kw)
    return StoredIssue(**base)


def test_issue_ledger_tags_id_status_and_positions() -> None:
    labels = {"h1": "clause 6.1 (Confidentiality)"}
    block = grounding.build_issue_ledger(
        [
            _issue(
                "i1",
                node_id="h1",
                our_position="cap at 12mo",
                their_position="uncapped",
                status="open",
            )
        ],
        labels,
    )
    assert "[i1]" in block
    assert "status: open" in block
    # The clause reference is a bare parenthetical of the legible label — never the raw
    # node id, never the old "anchored to" phrasing.
    assert "(clause 6.1 (Confidentiality))" in block
    assert "anchored to" not in block
    assert "h1" not in block
    assert "cap at 12mo" in block and "uncapped" in block


def test_issue_ledger_marks_free_floating_issue() -> None:
    assert "contract-level" in grounding.build_issue_ledger([_issue("i9", node_id=None)], {})


def test_scrub_replaces_leaked_id_with_label() -> None:
    answer = "The cap is set in 6161c90b-04bd-401a-a421-cc3e1c87ef5d which limits liability."
    scrubbed = scrub_leaked_ids(
        answer, {"6161c90b-04bd-401a-a421-cc3e1c87ef5d": "clause 6.1 (Confidentiality)"}
    )
    assert "6161c90b-04bd-401a-a421-cc3e1c87ef5d" not in scrubbed
    assert "clause 6.1 (Confidentiality)" in scrubbed


def test_scrub_leaves_clean_answer_untouched() -> None:
    answer = "The cap is in clause 6.1 (Confidentiality)."
    assert scrub_leaked_ids(answer, {"h1": "clause 6.1 (Confidentiality)"}) == answer


# --- citation parsing + deflection routing ---------------------------------


def test_parse_answer_reads_structured_fields() -> None:
    parsed = parse_answer('{"answer": "It is in 11.2", "kind": "answer", "citations": ["h11"]}')
    assert parsed.kind == "answer"
    assert parsed.citations == ["h11"]


def test_parse_answer_reads_deflection() -> None:
    parsed = parse_answer('{"answer": "Ask a lawyer", "kind": "deflected", "citations": []}')
    assert parsed.kind == "deflected"


def test_parse_answer_tolerates_surrounding_prose() -> None:
    parsed = parse_answer(
        'Here you go:\n{"answer": "x", "kind": "not_found", "citations": []}\nthanks'
    )
    assert parsed.kind == "not_found"


def test_parse_answer_unparseable_is_honest_miss() -> None:
    parsed = parse_answer("sorry, no json here")
    assert parsed.kind == "not_found"
    assert parsed.citations == []


# --- clear conversation (DD-40 thread wipe) --------------------------------


class _RecordingConn:
    """Captures executed SQL + args so clear_conversation's wipe can be asserted offline."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        return "OK"


def test_clear_conversation_deletes_messages_and_nulls_summary() -> None:
    import asyncio

    from backend.services.donna.conversation_repo import clear_conversation

    conn = _RecordingConn()
    asyncio.run(clear_conversation(conn, "c1"))

    assert len(conn.calls) == 2
    delete_sql, delete_args = conn.calls[0]
    update_sql, update_args = conn.calls[1]
    assert "DELETE FROM donna_messages" in delete_sql
    assert delete_args == ("c1",)
    assert "running_summary = NULL" in update_sql
    assert update_args == ("c1",)
