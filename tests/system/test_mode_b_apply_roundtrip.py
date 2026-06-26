"""F03c Mode B review -> decide -> apply, end-to-end against a LIVE Postgres.

Unlike the faked-DB integration tests (`tests/integration/test_revision_review_routes.py`),
this drives the REAL F03b->F03c spine through real services and a real database: a
Mode-A baseline import, mark-as-sent (cuts the `last_shared_with_counterparty`
snapshot), a Mode-B clean-diff import (stages the review session), then the full
review/decision/apply loop. Every test runs inside an OUTER transaction that is
ROLLED BACK in a `finally`, so the dev DB is never mutated (the services' own
`conn.transaction()` blocks nest as savepoints).

Two oracles:
  * The synthetic pair (always-runs, privacy-safe, built in-test with python-docx)
    is the EXACT oracle: full-accept faithfulness (matched-edited node body ==
    incoming body) + exact ApplyResult counts + reject-all seeds one issue per
    rejection and mutates nothing.
  * The gitignored real spike pair (`spikes/mode_b_matching/real_pair/`) is the
    de-risked real-data smoke: payload well-formedness, abstain resolution, a mixed
    accept/keep/edit pass, and the apply spine's structural invariants + terminality
    on a genuine document. Skips cleanly when the pair is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest
from backend.config.settings import get_settings
from backend.models.mark_sent import MarkSentRequest
from backend.models.revision_import import RevisionImportRequest
from backend.models.revision_review import (
    ConfirmMatchRequest,
    HunkDecideRequest,
    NodeDecideRequest,
    ReviewChange,
    ReviewPayload,
)
from backend.services.contract_repo import fetch_nodes, insert_nodes
from backend.services.import_ import revision_review as review
from backend.services.import_.docx_reader import read_docx
from backend.services.import_.persist import tree_to_node_rows
from backend.services.import_.pipeline import import_docx
from backend.services.import_.revision_import import (
    RevisionImportError,
    import_revision,
)
from backend.services.import_.revision_review import _apply
from backend.services.import_.tree_builder import build_tree
from backend.services.mark_sent import mark_sent
from docx import Document

_REAL_DIR = Path(__file__).resolve().parents[2] / "spikes" / "mode_b_matching" / "real_pair"
_REAL_BASELINE = _REAL_DIR / "original_working.docx"
_REAL_AFTER = _REAL_DIR / "mode_b_after.docx"


# --------------------------------------------------------------------------- #
# Live-DB harness: connect or skip; every test body runs in a rolled-back tx    #
# --------------------------------------------------------------------------- #


async def _connect_or_skip() -> Any:
    try:
        return await asyncpg.connect(get_settings().database_url)
    except (OSError, asyncpg.PostgresError) as exc:  # no live DB in this env
        pytest.skip(f"no live Postgres reachable: {exc}")


async def _make_contract(conn: Any) -> str:
    """Insert the minimal client -> deal -> contract_type -> contract FK chain and
    return the new contract id. Everything is rolled back by the caller."""
    client_id = await conn.fetchval(
        "INSERT INTO clients (name) VALUES ($1) RETURNING id", "Test Client"
    )
    deal_id = await conn.fetchval(
        "INSERT INTO deals (client_id, name, position) VALUES ($1, $2, 'licensor') RETURNING id",
        client_id,
        "Test Deal",
    )
    ct_id = await conn.fetchval(
        "INSERT INTO contract_types (name) VALUES ($1) RETURNING id", "Test Type"
    )
    contract_id = await conn.fetchval(
        """INSERT INTO contracts (client_id, deal_id, contract_type_id, name, status, origin)
           VALUES ($1, $2, $3, $4, 'drafting', 'us') RETURNING id""",
        client_id,
        deal_id,
        ct_id,
        "Test Contract",
    )
    return str(contract_id)


# --------------------------------------------------------------------------- #
# Synthetic .docx builders (privacy-safe; distinct vocab => unique matches)      #
# --------------------------------------------------------------------------- #

_HEADING = "Master Services Terms"
_C1_ORIG = (
    "The vendor shall deliver the fermentation hardware to the licensee within "
    "thirty days of the effective date."
)
_C1_REVISED = (
    "The vendor shall deliver the fermentation hardware to the licensee within "
    "forty five days of the effective date."
)
_C2 = (
    "The licensee agrees to pay a royalty equal to ten percent of net collected "
    "revenue on a quarterly basis."
)
_C3 = (
    "Each party must keep all proprietary biological materials strictly "
    "confidential throughout the entire term of this agreement."
)
_C4 = (
    "Either party may terminate this arrangement for material breach after "
    "providing sixty days written cure notice."
)
_C5 = (
    "Any dispute arising under these provisions shall be resolved by binding "
    "arbitration seated in Zurich Switzerland."
)
_NEW_CLAUSE = (
    "The parties shall each maintain commercial general liability insurance of no "
    "less than five million dollars."
)


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))


async def _seed_baseline_from_docx(conn: Any, contract_id: str, docx_path: Path) -> None:
    """Persist the parsed `docx_path` as the live node tree (the working copy)."""
    rows = tree_to_node_rows(build_tree(read_docx(docx_path)))
    await insert_nodes(conn, contract_id, rows)


# --------------------------------------------------------------------------- #
# Shared assertions on a review payload                                         #
# --------------------------------------------------------------------------- #


def _assert_payload_well_formed(payload: ReviewPayload) -> None:
    # Phase 1: every abstain is genuinely an abstain, ranked ascending confidence.
    confs = [
        a.match_confidence if a.match_confidence is not None else 1.0
        for a in payload.phase1.abstains
    ]
    assert confs == sorted(confs), "Phase-1 abstains not ranked ascending by confidence"
    for a in payload.phase1.abstains:
        assert a.change_kind == "abstain"
    assert payload.phase1.tree_anomalies == []  # F03b stages none (DD-78)
    # Phase 2: settled changes only, each a valid kind, each carrying >= 1 hunk.
    for c in payload.phase2:
        assert c.change_kind in ("edited", "new", "deleted")
        assert c.hunk_count == len(c.hunks)
        assert c.hunks, f"settled change {c.id} has no hunk"


async def _decide_change_fully(conn: Any, change: ReviewChange, *, accept: bool) -> None:
    """Drive one settled change to status='complete'. `accept` flips the whole-node
    verdict and the lead hunk verdict; for edited changes, accept the first hunk and
    keep(reject) the rest so a multi-hunk change exercises both branches."""
    if change.change_kind in ("new", "deleted"):
        verdict = "accept" if accept else "reject"
        await review.decide_node(conn, change.id, NodeDecideRequest(verdict=verdict))
        return
    for i, hunk in enumerate(change.hunks):
        if accept:
            hv = "accept" if i == 0 else "keep"
        else:
            hv = "keep"
        await review.decide_hunk(conn, hunk.id, HunkDecideRequest(verdict=hv))


def _node_text(node: Any) -> str:
    return (node.body if node.body is not None else node.heading) or ""


# --------------------------------------------------------------------------- #
# Synthetic oracle 1 — full-accept faithfulness + exact ApplyResult counts      #
# --------------------------------------------------------------------------- #


async def test_full_accept_is_faithful_and_counts_reconcile(tmp_path: Path) -> None:
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)

        original = tmp_path / "original.docx"
        revised = tmp_path / "revised.docx"
        _write_docx(original, [_HEADING, _C1_ORIG, _C2, _C3, _C4, _C5])
        _write_docx(revised, [_HEADING, _C1_REVISED, _C2, _C3, _C5, _NEW_CLAUSE])

        await _seed_baseline_from_docx(conn, contract_id, original)
        sent = await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        assert sent.marked

        imp = await import_revision(
            conn, contract_id, str(revised), RevisionImportRequest(source="counterparty")
        )
        # The deterministic synthetic edit/delete/add => one of each, no abstains.
        assert imp.edited_matches == 1, imp
        assert imp.new == 1 and imp.deleted == 1 and imp.abstains == 0, imp

        payload = await review.get_review_payload(conn, imp.session_id)
        _assert_payload_well_formed(payload)
        assert payload.phase1.abstains == []
        kinds = sorted(c.change_kind for c in payload.phase2)
        assert kinds == ["deleted", "edited", "new"], kinds

        edited = next(c for c in payload.phase2 if c.change_kind == "edited")

        for change in payload.phase2:
            await _decide_change_fully(conn, change, accept=True)

        result = await review.apply_session(conn, imp.session_id)
        assert result.status == "completed"
        assert result.edits_applied == 1, result
        assert result.nodes_inserted == 1, result
        assert result.nodes_deleted == 1, result
        assert result.issues_created == 0 and result.issue_ids == [], result

        nodes = await fetch_nodes(conn, contract_id)
        bodies = [_node_text(n) for n in nodes]
        # Faithfulness: the matched-edited clause now carries the counterparty's text.
        assert _C1_REVISED in bodies, "accepted edit not reflected in the live tree"
        assert _C1_ORIG not in bodies, "stale baseline text still present after edit"
        # Accepted addition inserted; accepted deletion soft-deleted (gone from live tree).
        assert _NEW_CLAUSE in bodies, "accepted new clause not inserted"
        assert _C4 not in bodies, "accepted deletion not removed from the live tree"
        # Untouched clauses are unchanged.
        assert _C2 in bodies and _C3 in bodies and _C5 in bodies

        # The edited node's id is the matched baseline id; its body is exactly _apply.
        edited_node = next(n for n in nodes if n.id == edited.node_id)
        assert _node_text(edited_node) == _C1_REVISED
    finally:
        await tx.rollback()
        await conn.close()


# --------------------------------------------------------------------------- #
# Synthetic oracle 2 — reject-all seeds one issue per rejection, mutates nothing #
# --------------------------------------------------------------------------- #


async def test_reject_all_seeds_issues_and_leaves_tree_untouched(tmp_path: Path) -> None:
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)

        original = tmp_path / "original.docx"
        revised = tmp_path / "revised.docx"
        _write_docx(original, [_HEADING, _C1_ORIG, _C2, _C3, _C4, _C5])
        _write_docx(revised, [_HEADING, _C1_REVISED, _C2, _C3, _C5, _NEW_CLAUSE])

        await _seed_baseline_from_docx(conn, contract_id, original)
        await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        imp = await import_revision(
            conn, contract_id, str(revised), RevisionImportRequest(source="counterparty")
        )

        payload = await review.get_review_payload(conn, imp.session_id)
        edited = next(c for c in payload.phase2 if c.change_kind == "edited")
        expected_issues = len(edited.hunks) + 1 + 1  # kept hunks + rejected new + reinstated delete

        for change in payload.phase2:
            await _decide_change_fully(conn, change, accept=False)

        result = await review.apply_session(conn, imp.session_id)
        assert result.status == "completed"
        assert result.edits_applied == 0, result
        assert result.nodes_inserted == 0 and result.nodes_deleted == 0, result
        assert result.issues_created == expected_issues, result
        assert len(result.issue_ids) == expected_issues

        # Nothing in the tree moved: original kept, addition absent, deletion reinstated.
        bodies = [_node_text(n) for n in await fetch_nodes(conn, contract_id)]
        assert _C1_ORIG in bodies and _C1_REVISED not in bodies
        assert _NEW_CLAUSE not in bodies
        assert _C4 in bodies  # rejected deletion -> reinstated (still live)

        # Every seeded issue is a counterparty_proposed_edit linked to this session.
        rows = await conn.fetch(
            """SELECT initiator, category, counterparty_revision_session_id
               FROM issues WHERE id = ANY($1::uuid[])""",
            result.issue_ids,
        )
        assert len(rows) == expected_issues
        for r in rows:
            assert r["initiator"] == "counterparty"
            assert r["category"] == "counterparty_proposed_edit"
            assert str(r["counterparty_revision_session_id"]) == imp.session_id

        # Apply is terminal: a second apply is rejected, the session stays completed.
        with pytest.raises(review.SessionAlreadyApplied):
            await review.apply_session(conn, imp.session_id)
        after = await review.get_review_payload(conn, imp.session_id)
        assert after.session.status == "completed"
    finally:
        await tx.rollback()
        await conn.close()


# --------------------------------------------------------------------------- #
# Real-pair smoke — mixed verdicts + structural invariants on genuine data       #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (_REAL_BASELINE.exists() and _REAL_AFTER.exists()),
    reason="gitignored real spike pair absent",
)
async def test_real_pair_review_decide_apply_roundtrip() -> None:
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)

        # Mode-A baseline import (no LLM — ai=False keeps it offline + deterministic).
        await import_docx(conn, contract_id, _REAL_BASELINE, ai=False)
        await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )

        try:
            imp = await import_revision(
                conn, contract_id, str(_REAL_AFTER), RevisionImportRequest(source="counterparty")
            )
        except RevisionImportError as exc:  # e.g. tracked-changes fixture (Path A)
            pytest.skip(f"real revision import not on the clean-diff path: {exc.detail}")

        payload = await review.get_review_payload(conn, imp.session_id)
        _assert_payload_well_formed(payload)
        assert payload.phase2, "real revision produced no settled changes"

        # --- Phase 1: drain the abstain queue (mix confirm / new) ---------------
        for i, ab in enumerate(payload.phase1.abstains):
            if ab.proposed_parent_id is not None and i % 2 == 0:
                req = ConfirmMatchRequest(action="confirm")
            else:
                req = ConfirmMatchRequest(action="new")
            resolved = await review.confirm_match(conn, ab.id, req)
            assert resolved.change_kind in ("edited", "new")

        payload = await review.get_review_payload(conn, imp.session_id)
        assert payload.phase1.abstains == [], "abstain queue did not drain after confirm-match"

        # --- pick one prose edited change for the exact round-trip oracle -------
        oracle_node_id: str | None = None
        oracle_expected: str | None = None
        for c in payload.phase2:
            if c.change_kind != "edited" or c.node_id is None:
                continue
            row = await conn.fetchrow(
                "SELECT content_type, body, heading FROM nodes "
                "WHERE id = $1 AND is_deleted = false",
                c.node_id,
            )
            if row is None or row["content_type"] != "prose":
                continue
            pre = (row["body"] if row["body"] is not None else row["heading"]) or ""
            oracle_node_id = c.node_id
            oracle_expected = _apply(pre, c.hunks, lambda h: h.proposed_text or "")
            # Accept ALL hunks of the oracle change so the service reconstructs `_apply`.
            for hunk in c.hunks:
                await review.decide_hunk(conn, hunk.id, HunkDecideRequest(verdict="accept"))
            break

        # --- Phase 2: decide every remaining change with a verdict mix ----------
        decided_oracle = oracle_node_id is not None
        accepted_new = rejected_new = 0
        accepted_del = rejected_del = 0
        edited_changed_text = 0
        kept_hunk_total = 0
        for idx, c in enumerate(payload.phase2):
            fresh = await review._change_view(conn, c.id)
            if fresh.status == "complete":
                continue  # the oracle change, already fully accepted
            if c.change_kind == "edited":
                changed = False
                for j, hunk in enumerate(c.hunks):
                    verdict = ("accept", "keep", "edit")[j % 3]
                    if verdict == "edit":
                        await review.decide_hunk(
                            conn, hunk.id, HunkDecideRequest(verdict="edit", final_text="X")
                        )
                        changed = True
                    elif verdict == "accept":
                        await review.decide_hunk(conn, hunk.id, HunkDecideRequest(verdict="accept"))
                        changed = True
                    else:
                        await review.decide_hunk(conn, hunk.id, HunkDecideRequest(verdict="keep"))
                        kept_hunk_total += 1
                if changed:
                    edited_changed_text += 1
            elif c.change_kind == "new":
                if idx % 2 == 0:
                    await review.decide_node(conn, c.id, NodeDecideRequest(verdict="accept"))
                    accepted_new += 1
                else:
                    await review.decide_node(conn, c.id, NodeDecideRequest(verdict="reject"))
                    rejected_new += 1
            else:  # deleted
                if idx % 2 == 0:
                    await review.decide_node(conn, c.id, NodeDecideRequest(verdict="accept"))
                    accepted_del += 1
                else:
                    await review.decide_node(conn, c.id, NodeDecideRequest(verdict="reject"))
                    rejected_del += 1

        result = await review.apply_session(conn, imp.session_id)

        # --- the apply spine's invariants on real data --------------------------
        assert result.status == "completed"
        assert result.issues_created == len(result.issue_ids)
        assert result.nodes_inserted == accepted_new, result
        assert result.nodes_deleted == accepted_del, result
        # edits_applied counts text-changing edited changes that were prose-editable;
        # table/non-editable nodes divert to an issue, so this is an upper bound.
        assert result.edits_applied <= edited_changed_text + (1 if decided_oracle else 0)
        # Rejections seed issues: kept hunks + rejected additions + reinstated deletions
        # are a lower bound (non-editable accepted edits may add more).
        assert result.issues_created >= kept_hunk_total + rejected_new + rejected_del

        # Every seeded issue is the right shape and linked to this session.
        if result.issue_ids:
            rows = await conn.fetch(
                """SELECT initiator, category, counterparty_revision_session_id
                   FROM issues WHERE id = ANY($1::uuid[])""",
                result.issue_ids,
            )
            assert len(rows) == len(result.issue_ids)
            for r in rows:
                assert r["initiator"] == "counterparty"
                assert r["category"] == "counterparty_proposed_edit"
                assert str(r["counterparty_revision_session_id"]) == imp.session_id

        # Exact round-trip oracle on the chosen prose edit.
        if decided_oracle:
            row = await conn.fetchrow(
                "SELECT body, heading FROM nodes WHERE id = $1 AND is_deleted = false",
                oracle_node_id,
            )
            assert row is not None, "oracle node vanished after apply"
            live = (row["body"] if row["body"] is not None else row["heading"]) or ""
            assert live == oracle_expected, "accepted edit did not match _apply reconstruction"

        # No orphans: every live non-deleted node still resolves its parent.
        live_nodes = await fetch_nodes(conn, contract_id)
        live_ids = {n.id for n in live_nodes}
        for n in live_nodes:
            assert n.parent_id is None or n.parent_id in live_ids, f"orphan node {n.id}"

        # Apply is terminal.
        with pytest.raises(review.SessionAlreadyApplied):
            await review.apply_session(conn, imp.session_id)
    finally:
        await tx.rollback()
        await conn.close()


# --------------------------------------------------------------------------- #
# Resume affordance — derived `pending_changes` count on the listed session     #
# --------------------------------------------------------------------------- #


async def test_pending_changes_tracks_undecided(tmp_path: Path) -> None:
    """The cockpit resume affordance reads `pending_changes` (changes whose status is
    not 'complete') off the listed session. It must equal the count of still-to-decide
    changes and drop as changes are settled — proving the correlated subquery counts
    the right rows in ONE query (no N+1)."""
    conn = await _connect_or_skip()
    tx = conn.transaction()
    await tx.start()
    try:
        contract_id = await _make_contract(conn)

        original = tmp_path / "original.docx"
        revised = tmp_path / "revised.docx"
        _write_docx(original, [_HEADING, _C1_ORIG, _C2, _C3, _C4, _C5])
        _write_docx(revised, [_HEADING, _C1_REVISED, _C2, _C3, _C5, _NEW_CLAUSE])

        await _seed_baseline_from_docx(conn, contract_id, original)
        await mark_sent(
            conn, contract_id, MarkSentRequest(recipient="counterparty", acknowledge_drift=True)
        )
        imp = await import_revision(
            conn, contract_id, str(revised), RevisionImportRequest(source="counterparty")
        )

        payload = await review.get_review_payload(conn, imp.session_id)
        total = len(payload.phase2) + len(payload.phase1.abstains)
        assert total > 1, "fixture must produce >1 change to exercise the partial count"

        # Nothing decided yet → every change is pending.
        listed = await review.list_sessions(conn, contract_id)
        assert len(listed) == 1
        assert listed[0].status == "reviewing"
        assert listed[0].pending_changes == total

        # Decide exactly one settled change fully → pending drops by one.
        await _decide_change_fully(conn, payload.phase2[0], accept=True)
        after = await review.list_sessions(conn, contract_id)
        assert after[0].pending_changes == total - 1
        # The single-session read carries the same derived count.
        reread = await review.get_review_payload(conn, imp.session_id)
        assert reread.session.pending_changes == total - 1
    finally:
        await tx.rollback()
        await conn.close()
