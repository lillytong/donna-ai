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


# --- message meta persistence (DD-40 rehydration) --------------------------


class _InsertCapturingConn:
    """Captures the INSERT args fetchval receives, so append_message's kind/citations
    serialization can be asserted offline (no DB)."""

    def __init__(self) -> None:
        self.insert_args: tuple[object, ...] | None = None

    async def fetchval(self, _sql: str, *args: object) -> str:
        self.insert_args = args
        return "new-id"


def test_append_message_serializes_kind_and_citations() -> None:
    import asyncio
    import json

    from backend.services.donna.conversation_repo import append_message

    conn = _InsertCapturingConn()
    asyncio.run(
        append_message(
            conn, "conv1", "assistant", "In 11.2.", kind="answer", citations=["n1", "i2"]
        )
    )
    # args: (conversation_id, role, content, kind, citations-json)
    assert conn.insert_args is not None
    assert conn.insert_args[3] == "answer"
    assert json.loads(conn.insert_args[4]) == ["n1", "i2"]  # type: ignore[arg-type]


def test_append_message_user_turn_leaves_kind_and_citations_null() -> None:
    import asyncio

    from backend.services.donna.conversation_repo import append_message

    conn = _InsertCapturingConn()
    asyncio.run(append_message(conn, "conv1", "user", "Where's the cap?"))
    assert conn.insert_args is not None
    assert conn.insert_args[3] is None  # kind
    assert conn.insert_args[4] is None  # citations (None stays SQL NULL, not "null")


class _FetchConn:
    """Returns canned rows so fetch_messages's kind/citations read-back can be asserted."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    async def fetch(self, _sql: str, *_args: object) -> list[dict[str, object]]:
        return self.rows


def test_fetch_messages_round_trips_kind_and_citations() -> None:
    import asyncio

    from backend.services.donna.conversation_repo import fetch_messages

    base = datetime(2026, 1, 1)
    rows: list[dict[str, object]] = [
        {"role": "user", "content": "Where's the cap?", "kind": None, "citations": None,
         "created_at": base},
        # asyncpg may hand JSONB back as a str -> json.loads path.
        {"role": "assistant", "content": "In 11.2.", "kind": "answer",
         "citations": '["n1", "i2"]', "created_at": base + timedelta(seconds=1)},
    ]
    msgs = asyncio.run(fetch_messages(_FetchConn(rows), "conv1"))
    assert msgs[0].kind is None and msgs[0].citations is None
    assert msgs[1].kind == "answer"
    assert msgs[1].citations == ["n1", "i2"]


# --- qa.ask deflection persistence (F10b) ----------------------------------
# The context-aware chat passes a softer acquire-context wording; a DEFLECTED turn must
# then be PERSISTED with that text (and no citations) so a reloaded thread matches the live
# reply. Without the override (the F10 direct path) the model's own prose is kept.


class _AppendCapturingConn:
    """Captures append_message calls so the persisted deflection text can be asserted."""

    def __init__(self) -> None:
        self.appended: list[tuple[object, ...]] = []

    async def execute(self, *_a: object) -> str:
        return "OK"


def _run_ask(  # type: ignore[no-untyped-def]
    monkeypatch: object,
    model_text: str,
    firm_profile: str = "",
    deal_brief: object = None,
    **ask_kwargs: object,
):
    import asyncio
    import contextlib

    from backend.models.clause_search import ClauseSearchResult
    from backend.models.donna import StoredConversation
    from backend.services import deal_brief_repo
    from backend.services.donna import qa

    captured: list[dict[str, object]] = []
    prompts: list[str] = []
    conn = _AppendCapturingConn()

    @contextlib.asynccontextmanager
    async def fake_acquire():  # type: ignore[no-untyped-def]
        yield conn

    async def fake_search(_cid: str, _q: str) -> ClauseSearchResult:
        return ClauseSearchResult(node_id=None)

    async def fake_get_conv(_conn: object, _cid: str) -> StoredConversation:
        return StoredConversation(id="conv1", contract_id="c1", running_summary=None)

    async def fake_fetch_messages(*_a: object) -> list[object]:
        return []

    async def fake_list_issues(*_a: object) -> list[object]:
        return []

    async def fake_fetch_nodes(*_a: object) -> list[object]:
        return []

    async def fake_append(
        _conn: object, _conv: str, role: str, content: str,
        kind: object = None, citations: object = None,
    ) -> str:
        captured.append({"role": role, "content": content, "kind": kind, "citations": citations})
        return "mid"

    async def fake_update_summary(*_a: object, **_k: object) -> None:
        return None

    async def fake_get_firm_profile(_conn: object) -> str:
        return firm_profile

    async def fake_get_brief(_conn: object, _cid: str) -> object:
        return deal_brief

    class _Result:
        text = model_text

    async def fake_complete(**kwargs: object) -> object:
        prompts.append(kwargs["messages"][0]["content"])  # type: ignore[index]
        return _Result()

    monkeypatch.setattr(qa, "acquire", fake_acquire)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "search_clause", fake_search)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "get_or_create_conversation", fake_get_conv)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "fetch_messages", fake_fetch_messages)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "list_issues", fake_list_issues)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "fetch_nodes", fake_fetch_nodes)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "get_firm_profile", fake_get_firm_profile)  # type: ignore[attr-defined]
    monkeypatch.setattr(deal_brief_repo, "get_brief", fake_get_brief)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "append_message", fake_append)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "_update_rolling_summary", fake_update_summary)  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "render", lambda *_a, **_k: "prompt")  # type: ignore[attr-defined]
    monkeypatch.setattr(qa, "complete", fake_complete)  # type: ignore[attr-defined]

    result = asyncio.run(qa.ask("c1", "Should we accept this?", **ask_kwargs))  # type: ignore[arg-type]
    assistant = next(c for c in captured if c["role"] == "assistant")
    return result, assistant, prompts[0]


def test_ask_persists_softer_deflection_text_when_overridden(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from backend.services.donna.advise import _ACQUIRE_CONTEXT

    deflected = '{"answer": "Raise an issue / get a lawyer.", "kind": "deflected", "citations": []}'
    result, assistant, _prompt = _run_ask(monkeypatch, deflected, deflection_text=_ACQUIRE_CONTEXT)
    # Persisted text + the returned answer both carry the softer wording, citations dropped.
    assert assistant["content"] == _ACQUIRE_CONTEXT
    assert assistant["citations"] == []
    assert result.answer == _ACQUIRE_CONTEXT
    assert result.kind == "deflected"


def test_ask_keeps_model_deflection_prose_without_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    deflected = '{"answer": "Raise an issue / get a lawyer.", "kind": "deflected", "citations": []}'
    _result, assistant, _prompt = _run_ask(monkeypatch, deflected)
    # F10 direct path (no override): the model's own deflection prose is persisted unchanged.
    assert assistant["content"] == "Raise an issue / get a lawyer."


# --- qa.ask firm-profile mandate grounding (F32 v1 / DD-90) -----------------
# The global operator-authored firm profile is appended to the Q&A judge/answer prompt as the
# firm's standing MANDATE so Donna grounds every answer in the firm's identity + red-lines.
# Synthetic profile — NOT real firm/contract data (public repo).

_MANDATE_MARK = "FIRM PROFILE / MANDATE"
_PROFILE = "We are a licensing firm. Standing red-line: never accept uncapped liability."


def test_ask_injects_firm_profile_mandate_into_prompt(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    answer = '{"answer": "The cap is in 11.2.", "kind": "answer", "citations": []}'
    _result, _assistant, prompt = _run_ask(monkeypatch, answer, firm_profile=_PROFILE)
    assert _MANDATE_MARK in prompt  # the labelled mandate block is present
    assert _PROFILE in prompt  # the operator's profile text reaches the model


def test_ask_empty_firm_profile_not_injected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    answer = '{"answer": "The cap is in 11.2.", "kind": "answer", "citations": []}'
    _result, _assistant, prompt = _run_ask(monkeypatch, answer, firm_profile="")
    assert _MANDATE_MARK not in prompt  # unset profile -> no-op


# --- qa.ask per-deal deal-brief grounding (F37 / DD-95) ---------------------
# Donna's whole-deal brief is appended to the Q&A answer prompt as the per-deal GLOBAL context
# alongside the firm mandate. Synthetic brief — NOT real firm/contract data (public repo).

_DEAL_BRIEF_MARK = "DEAL BRIEF"


def test_ask_injects_deal_brief_into_prompt(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from backend.models.deal_brief import DealBrief

    answer = '{"answer": "The cap is in 11.2.", "kind": "answer", "citations": []}'
    brief = DealBrief(contract_id="c1", content="Parties: a licensor and a licensee.")
    _result, _assistant, prompt = _run_ask(monkeypatch, answer, deal_brief=brief)
    assert _DEAL_BRIEF_MARK in prompt  # the labelled deal-brief block is present
    assert "a licensor and a licensee" in prompt  # the brief content reaches the model


def test_ask_no_deal_brief_not_injected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    answer = '{"answer": "The cap is in 11.2.", "kind": "answer", "citations": []}'
    _result, _assistant, prompt = _run_ask(monkeypatch, answer, deal_brief=None)
    assert _DEAL_BRIEF_MARK not in prompt  # no brief -> no-op
