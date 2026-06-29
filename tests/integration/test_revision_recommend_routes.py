"""F03c per-change revision recommendation: the engine orchestration over a session with an
edited + a new + a deleted change (LLM + DB mocked), and the thin route's response shape +
not-found / rate-limit mappings. TestClient is used without its context manager so the app
lifespan never runs (mirrors test_donna_recommendations_routes.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from backend.api import revision_recommend as recommend_api
from backend.models.deal_brief import DealBrief
from backend.models.defined_terms import StoredDefinedTerm
from backend.models.imports import StoredNode
from backend.models.llm import CompletionResult, TokenUsage
from backend.models.revision_recommend import RevisionRecommendSummary, VerdictTally
from backend.services import deal_brief_repo
from backend.services.donna import revision_recommend as svc
from backend.services.llm import LLMRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Engine orchestration (DB + LLM mocked)                                        #
# --------------------------------------------------------------------------- #


def _change(cid: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=cid,
        node_id=None,
        proposed_parent_id=None,
        proposed_order_index=None,
        match_confidence=None,
        status="pending",
    )
    base.update(kw)
    return base


def _hunk(hid: str, change_id: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        id=hid,
        change_id=change_id,
        hunk_type="replacement",
        significance="substantive",
        position_in_body=0,
        original_text="our text",
        proposed_text="their text",
    )
    base.update(kw)
    return base


class _FakeConn:
    """Serves the session/changes/hunks reads and records advisory UPDATEs with their SQL."""

    def __init__(
        self,
        session_row: dict[str, Any] | None,
        change_rows: list[dict[str, Any]],
        hunk_rows: list[dict[str, Any]],
    ) -> None:
        self._session = session_row
        self._changes = change_rows
        self._hunks = hunk_rows
        self.in_txn = False
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.captured_prompts: list[str] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.in_txn = True
        try:
            yield
        finally:
            self.in_txn = False

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._session

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "counterparty_revision_changes" in sql:
            return self._changes
        if "counterparty_revision_hunks" in sql:
            wanted = set(args[0])
            return [h for h in self._hunks if str(h["change_id"]) in wanted]
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        assert self.in_txn  # advisory writes happen inside the transaction
        self.executes.append((sql, args))
        return "UPDATE 1"


def _wire(
    monkeypatch: Any,
    conn: _FakeConn,
    responses: list[str],
    firm_profile: str = "",
    deal_brief: DealBrief | None = None,
) -> list[str]:
    """Patch the engine's I/O seams: acquire yields `conn`, the contract/nodes/patterns/firm-
    profile/deal-brief lookups are stubbed, and `complete` pops canned JSON (capturing each prompt
    onto `conn.captured_prompts`). Returns the caller log for assertions."""

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    queue = list(responses)
    callers: list[str] = []
    conn.captured_prompts = []

    async def fake_complete(**kwargs: Any) -> CompletionResult:
        callers.append(kwargs["caller"])
        conn.captured_prompts.append(kwargs["messages"][0]["content"])
        return CompletionResult(text=queue.pop(0), usage=TokenUsage())

    async def fake_nodes(_conn: Any, _cid: str) -> list[Any]:
        return []

    async def fake_patterns(_conn: Any, _cid: str) -> list[Any]:
        return []

    async def fake_contract(_conn: Any, _cid: str) -> None:
        return None

    async def fake_firm_profile(_conn: Any) -> str:
        return firm_profile

    async def fake_get_brief(_conn: Any, _cid: str) -> DealBrief | None:
        return deal_brief

    monkeypatch.setattr(svc, "acquire", fake_acquire)
    monkeypatch.setattr(svc, "complete", fake_complete)
    monkeypatch.setattr(svc, "fetch_nodes", fake_nodes)
    monkeypatch.setattr(svc, "fetch_patterns_for_issue", fake_patterns)
    monkeypatch.setattr(svc, "get_contract", fake_contract)
    monkeypatch.setattr(svc, "get_firm_profile", fake_firm_profile)
    monkeypatch.setattr(deal_brief_repo, "get_brief", fake_get_brief)
    return callers


_COUNTER = (
    '{"verdict": "counter", "significance": "substantive",'
    ' "reasoning": "Uncapped is deal-breaking.",'
    ' "counter_language": "Liability shall not exceed the fees paid."}'
)
_ACCEPT = (
    '{"verdict": "accept", "significance": "substantive",'
    ' "reasoning": "A fair addition.", "counter_language": null}'
)
_KEEP = (
    '{"verdict": "keep", "significance": "substantive",'
    ' "reasoning": "We need this clause.", "counter_language": null}'
)


async def test_engine_analyzes_edited_new_deleted_and_skips_decided(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [
        _change("ch-edit", node_id="n1", match_confidence=0.9, status="pending"),
        _change("ch-new", proposed_order_index=2, proposed_parent_id="n0", status="pending"),
        _change("ch-del", node_id="n2", match_confidence=None, status="partial"),
        _change("ch-done", node_id="n3", match_confidence=0.9, status="complete"),
        _change("ch-abstain", proposed_parent_id="n4", match_confidence=0.2, status="pending"),
    ]
    hunks = [
        _hunk("h-edit", "ch-edit", original_text="capped", proposed_text="uncapped"),
        _hunk("h-new", "ch-new", hunk_type="insertion", original_text=None, proposed_text="new"),
        _hunk("h-del", "ch-del", hunk_type="deletion", original_text="old", proposed_text=None),
        _hunk("h-done", "ch-done"),  # decided change → must be skipped
        _hunk("h-abstain", "ch-abstain"),  # unresolved abstain → must be skipped
    ]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER, _ACCEPT, _KEEP])

    summary = await svc.recommend_session("s1")

    assert summary.changes_analyzed == 3  # edited + new + deleted; decided + abstain skipped
    assert summary.hunks_analyzed == 3
    assert summary.by_verdict == VerdictTally(accept=1, counter=1, keep=1)

    written = {args[0]: (sql, args) for sql, args in conn.executes}
    assert set(written) == {"h-edit", "h-new", "h-del"}  # only the analyzable hunks written
    assert "h-done" not in written and "h-abstain" not in written

    for sql, args in conn.executes:
        # advisory columns + significance + rationale ONLY — never the applied
        # verdict/final_text (DD-64)
        assert "donna_verdict" in sql and "donna_counter_text" in sql and "significance" in sql
        assert "donna_rationale" in sql
        assert "final_text" not in sql
        _hid, verdict, counter, significance, rationale = args
        # counter-language present IFF verdict == counter
        assert (counter is not None) == (verdict == "counter")
        assert significance in ("trivial", "substantive")
        # the one-line rationale (Donna's reasoning) is persisted for every analyzed hunk
        assert isinstance(rationale, str) and rationale

    # the counter hunk carries staged language; accept/keep carry none
    assert written["h-edit"][1][1] == "counter" and written["h-edit"][1][2] is not None
    assert written["h-new"][1][1] == "accept" and written["h-new"][1][2] is None
    assert written["h-del"][1][1] == "keep" and written["h-del"][1][2] is None


async def test_engine_raises_when_session_missing(monkeypatch: Any) -> None:
    conn = _FakeConn(None, [], [])
    _wire(monkeypatch, conn, [])
    try:
        await svc.recommend_session("missing")
        raise AssertionError("expected SessionNotFound")
    except svc.SessionNotFound:
        pass
    assert conn.executes == []  # nothing written


async def test_engine_no_pending_changes_writes_nothing(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch-done", node_id="n3", match_confidence=0.9, status="complete")]
    conn = _FakeConn(session, changes, [_hunk("h-done", "ch-done")])
    _wire(monkeypatch, conn, [])

    summary = await svc.recommend_session("s1")

    assert summary.changes_analyzed == 0 and summary.hunks_analyzed == 0
    assert conn.executes == []


# --------------------------------------------------------------------------- #
# Cross-document clustering: judge once, fan to all (DD-89)                      #
# --------------------------------------------------------------------------- #


async def test_cluster_judges_once_and_fans_same_verdict_to_all_members(monkeypatch: Any) -> None:
    # The same defined-term rename recurs in three clauses — bare, parenthesised, and case/ws
    # noisy. All three cluster, so Donna judges ONCE and the one verdict/significance is written
    # to every member (the consistency guarantee).
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [
        _change("ch1", node_id="n1", match_confidence=0.9, status="pending"),
        _change("ch2", node_id="n2", match_confidence=0.9, status="pending"),
        _change("ch3", node_id="n3", match_confidence=0.9, status="pending"),
    ]
    hunks = [
        _hunk("h1", "ch1", original_text="Buyer", proposed_text="Purchaser"),
        _hunk("h2", "ch2", original_text="(Buyer)", proposed_text="(Purchaser)"),
        _hunk("h3", "ch3", original_text=" buyer ", proposed_text="purchaser"),
    ]
    conn = _FakeConn(session, changes, hunks)
    callers = _wire(monkeypatch, conn, [_COUNTER])  # only ONE response is needed

    summary = await svc.recommend_session("s1")

    assert len(callers) == 1  # judged once, not once per hunk
    assert summary.hunks_analyzed == 3
    written = {args[0]: args for _sql, args in conn.executes}
    assert set(written) == {"h1", "h2", "h3"}
    verdicts = {written[h][1] for h in ("h1", "h2", "h3")}
    significances = {written[h][3] for h in ("h1", "h2", "h3")}
    assert verdicts == {"counter"}  # the SAME verdict fanned to all
    assert significances == {"substantive"}


async def test_cluster_finalizes_counter_per_member_span(monkeypatch: Any) -> None:
    # Two members share the same 5%->10% edit (one clause), but their surrounding clause bodies
    # differ (a leading "("). One raw counter is judged once; finalize runs PER MEMBER against
    # that member's OWN proposed clause, so the parenthesised member is NOT handed the other
    # member's post-reduced token (DD-89: never fan an already-reduced counter).
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [
        _change("ch-a", node_id="na", match_confidence=0.9, status="pending"),
        _change("ch-b", node_id="nb", match_confidence=0.9, status="pending"),
    ]
    body_a, body_b = "We pay 5% now.", "(We pay 5% now.)"
    hunks = [
        _hunk(
            "ha",
            "ch-a",
            original_text="5%",
            proposed_text="10%",
            position_in_body=body_a.index("5%"),
        ),
        _hunk(
            "hb",
            "ch-b",
            original_text="5%",
            proposed_text="10%",
            position_in_body=body_b.index("5%"),
        ),
    ]
    conn = _FakeConn(session, changes, hunks)
    raw_counter = (
        '{"verdict": "counter", "significance": "substantive",'
        ' "reasoning": "Meet in the middle.", "counter_language": "We pay 7.5% now."}'
    )
    callers = _wire(monkeypatch, conn, [raw_counter])

    async def fake_nodes_with_bodies(_conn: Any, _cid: str) -> list[StoredNode]:
        return [
            StoredNode(id="na", order_index=0, content_type="paragraph", body=body_a),
            StoredNode(id="nb", order_index=1, content_type="paragraph", body=body_b),
        ]

    monkeypatch.setattr(svc, "fetch_nodes", fake_nodes_with_bodies)

    await svc.recommend_session("s1")

    assert len(callers) == 1  # judged once across the cluster
    written = {args[0]: args for _sql, args in conn.executes}
    # Member A's counter reduces cleanly to its own changed span.
    assert written["ha"][2] == "7.5%"
    # Member B's surrounding span differs; its OWN reduction can't align, so it falls back to the
    # raw counter — it is NOT handed A's post-reduced "7.5%" token.
    assert written["hb"][2] == "We pay 7.5% now."


# --------------------------------------------------------------------------- #
# F36 / DD-93: reference-graph grounding injected, resolved once per root        #
# --------------------------------------------------------------------------- #


async def test_reference_bundle_injected_and_resolved_once_per_root(monkeypatch: Any) -> None:
    # One change (root node n1) with TWO distinct hunks -> two judge buckets, but the reference
    # bundle for n1 must resolve ONCE (cached per grounding-root), not per member/bucket. The
    # resolved defined-term definition must reach the prompt the judge sees.
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch1", node_id="n1", match_confidence=0.9, status="pending")]
    hunks = [
        _hunk("h1", "ch1", original_text="Buyer", proposed_text="Purchaser", position_in_body=0),
        _hunk("h2", "ch1", original_text="5%", proposed_text="10%", position_in_body=20),
    ]
    conn = _FakeConn(session, changes, hunks)

    prompts: list[str] = []

    async def capturing_complete(**kwargs: Any) -> CompletionResult:
        prompts.append(kwargs["messages"][0]["content"])
        return CompletionResult(text=_COUNTER, usage=TokenUsage())

    async def fake_nodes(_conn: Any, _cid: str) -> list[StoredNode]:
        return [StoredNode(id="n1", order_index=0, content_type="paragraph",
                           body="The Buyer pays a 5% Royalty.")]

    async def fake_contract(_conn: Any, _cid: str) -> Any:
        return SimpleNamespace(deal_id="deal1", contract_type_id="ct1")

    async def fake_ctype(_conn: Any, _ctid: Any) -> None:
        return None

    async def fake_terms(_conn: Any, _deal_id: str) -> list[StoredDefinedTerm]:
        return [StoredDefinedTerm(id="t1", deal_id="deal1", term="Royalty",
                                  definition="5% of Net Sales", source_node_id="n1")]

    async def fake_refs(_conn: Any, _cid: str) -> list[Any]:
        return []

    resolve_calls = {"n": 0}
    real_build = svc.build_reference_grounding

    def spy_build(*args: Any, **kwargs: Any) -> str:
        resolve_calls["n"] += 1
        return real_build(*args, **kwargs)

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    monkeypatch.setattr(svc, "acquire", fake_acquire)
    monkeypatch.setattr(svc, "complete", capturing_complete)
    monkeypatch.setattr(svc, "fetch_nodes", fake_nodes)
    monkeypatch.setattr(svc, "fetch_patterns_for_issue", lambda *_a: _empty())
    monkeypatch.setattr(svc, "get_contract", fake_contract)
    monkeypatch.setattr(svc, "get_contract_type", fake_ctype)
    monkeypatch.setattr(svc, "list_terms_for_deal", fake_terms)
    monkeypatch.setattr(svc, "list_cross_references", fake_refs)
    monkeypatch.setattr(svc, "build_reference_grounding", spy_build)
    monkeypatch.setattr(svc, "get_firm_profile", lambda _conn: _empty_str())
    monkeypatch.setattr(deal_brief_repo, "get_brief", lambda *_a: _empty_brief())

    await svc.recommend_session("s1")

    assert resolve_calls["n"] == 1  # resolved once for root n1, NOT per hunk/bucket
    assert len(prompts) == 2  # two distinct hunks -> two judge buckets
    for prompt in prompts:  # but both carry the same resolved definition bundle
        assert '"Royalty" means 5% of Net Sales' in prompt
        assert "DEFINED TERMS USED IN THIS CLAUSE" in prompt


async def _empty() -> list[Any]:
    return []


async def _empty_str() -> str:
    return ""


async def _empty_brief() -> DealBrief | None:
    return None


# --------------------------------------------------------------------------- #
# F32 v1 / DD-90: firm-profile mandate injected once per session                #
# --------------------------------------------------------------------------- #

_MANDATE_MARK = "FIRM PROFILE / MANDATE"
# Synthetic profile — NOT real firm/contract data (public repo).
_PROFILE = "We are a licensing firm. Standing red-line: never accept uncapped liability."


async def test_firm_profile_injected_into_judge_prompt(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch1", node_id="n1", match_confidence=0.9, status="pending")]
    hunks = [_hunk("h1", "ch1", original_text="capped", proposed_text="uncapped")]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER], firm_profile=_PROFILE)

    await svc.recommend_session("s1")

    assert len(conn.captured_prompts) == 1
    prompt = conn.captured_prompts[0]
    assert _MANDATE_MARK in prompt  # the labelled mandate block is present
    assert _PROFILE in prompt  # the operator's profile text reaches the judge


async def test_firm_profile_and_reference_bundle_both_injected(monkeypatch: Any) -> None:
    # The mandate (F32) and the F36 reference bundle compose: both appear in the SAME judge prompt
    # (the profile is a session-level constant; the bundle is the per-clause grounding).
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch1", node_id="n1", match_confidence=0.9, status="pending")]
    hunks = [_hunk("h1", "ch1", original_text="Buyer", proposed_text="Purchaser")]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER], firm_profile=_PROFILE)

    async def fake_nodes(_conn: Any, _cid: str) -> list[StoredNode]:
        return [StoredNode(id="n1", order_index=0, content_type="paragraph",
                           body="The Buyer pays a Royalty.")]

    async def fake_contract(_conn: Any, _cid: str) -> Any:
        return SimpleNamespace(deal_id="deal1", contract_type_id="ct1")

    async def fake_ctype(_conn: Any, _ctid: Any) -> None:
        return None

    async def fake_terms(_conn: Any, _deal_id: str) -> list[StoredDefinedTerm]:
        return [StoredDefinedTerm(id="t1", deal_id="deal1", term="Royalty",
                                  definition="a payment", source_node_id="n1")]

    monkeypatch.setattr(svc, "fetch_nodes", fake_nodes)
    monkeypatch.setattr(svc, "get_contract", fake_contract)
    monkeypatch.setattr(svc, "get_contract_type", fake_ctype)
    monkeypatch.setattr(svc, "list_terms_for_deal", fake_terms)
    monkeypatch.setattr(svc, "list_cross_references", lambda *_a: _empty())

    await svc.recommend_session("s1")

    prompt = conn.captured_prompts[0]
    assert _MANDATE_MARK in prompt and _PROFILE in prompt  # F32 mandate
    assert "DEFINED TERMS USED IN THIS CLAUSE" in prompt  # F36 reference bundle
    assert '"Royalty" means a payment' in prompt


async def test_empty_firm_profile_not_injected(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch1", node_id="n1", match_confidence=0.9, status="pending")]
    hunks = [_hunk("h1", "ch1", original_text="capped", proposed_text="uncapped")]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER], firm_profile="")  # unset profile

    await svc.recommend_session("s1")

    assert _MANDATE_MARK not in conn.captured_prompts[0]  # no-op: nothing injected


# --------------------------------------------------------------------------- #
# F37 / DD-95: per-deal deal brief injected once per session into {deal_context} #
# --------------------------------------------------------------------------- #

_DEAL_BRIEF_MARK = "DEAL BRIEF"
# Synthetic brief — NOT real firm/contract data (public repo).
_BRIEF = DealBrief(
    contract_id="c1",
    content="Parties + Roles: a licensor and a licensee. Economic Spine: a 10% annual fee.",
)


async def test_deal_brief_injected_into_judge_prompt(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch1", node_id="n1", match_confidence=0.9, status="pending")]
    hunks = [_hunk("h1", "ch1", original_text="capped", proposed_text="uncapped")]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER], deal_brief=_BRIEF)

    await svc.recommend_session("s1")

    prompt = conn.captured_prompts[0]
    assert _DEAL_BRIEF_MARK in prompt  # the labelled deal-brief block reaches {deal_context}
    assert "a licensor and a licensee" in prompt  # the brief content reaches the judge
    assert "a 10% annual fee" in prompt


async def test_empty_deal_brief_not_injected(monkeypatch: Any) -> None:
    session = dict(id="s1", contract_id="c1", source="counterparty", status="reviewing")
    changes = [_change("ch1", node_id="n1", match_confidence=0.9, status="pending")]
    hunks = [_hunk("h1", "ch1", original_text="capped", proposed_text="uncapped")]
    conn = _FakeConn(session, changes, hunks)
    _wire(monkeypatch, conn, [_COUNTER], deal_brief=None)  # no brief distilled/edited

    await svc.recommend_session("s1")

    assert _DEAL_BRIEF_MARK not in conn.captured_prompts[0]  # no-op: nothing injected


# --------------------------------------------------------------------------- #
# Route (service mocked)                                                         #
# --------------------------------------------------------------------------- #

app = FastAPI()
app.include_router(recommend_api.router)
client = TestClient(app)
_PATH = "/revisions/sessions/s1/recommend"


def test_route_returns_summary(monkeypatch: Any) -> None:
    async def fake(session_id: str) -> RevisionRecommendSummary:
        assert session_id == "s1"
        return RevisionRecommendSummary(
            session_id="s1",
            changes_analyzed=2,
            hunks_analyzed=3,
            by_verdict=VerdictTally(accept=1, counter=1, keep=1),
        )

    monkeypatch.setattr(recommend_api, "recommend_session", fake)
    resp = client.post(_PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["changes_analyzed"] == 2
    assert body["by_verdict"] == {"accept": 1, "counter": 1, "keep": 1}


def test_route_maps_missing_session_to_404(monkeypatch: Any) -> None:
    async def fake(_session_id: str) -> RevisionRecommendSummary:
        raise svc.SessionNotFound("s1")

    monkeypatch.setattr(recommend_api, "recommend_session", fake)
    assert client.post(_PATH).status_code == 404


def test_route_maps_rate_limit_to_429(monkeypatch: Any) -> None:
    async def fake(_session_id: str) -> RevisionRecommendSummary:
        raise LLMRateLimitError("429 from provider")

    monkeypatch.setattr(recommend_api, "recommend_session", fake)
    assert client.post(_PATH).status_code == 429
