"use client";

// F03c — Mode B revision-review surface (DD-78). A two-phase review of a staged
// counterparty/legal revision: a structural-foundation pass (6a tree triage — empty
// for now — + 6b abstain match-confirm queue) that must clear before the content
// pass, which is ONE document-ordered stream of type-appropriate cards (edited /
// added / deleted) on the DD-26 inline-tracked-markup + DD-27 four-action pattern.
// On apply, decisions land on the working copy and rejections seed issues.

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./review.module.css";
import {
  ApiError,
  applyRevisionSession,
  confirmMatch,
  decideHunk,
  decideNode,
  getRevisionReview,
  getSnapshotTree,
  type ChangeContextSide,
  type ApplyResult,
  type HunkDecisionAction,
  type NodeDecisionAction,
  type NodeTreeItem,
  type ReviewChange,
  type ReviewHunk,
  type ReviewPayload,
} from "../../../../lib/api";

type Phase = "structure" | "content";

// One node of an inline word-diff. `split(/(\s+)/)` keeps whitespace tokens so the
// rendered markup preserves spacing; the LCS walk classifies each token.
type DiffSeg = { type: "same" | "ins" | "del"; text: string };

function wordDiff(a: string | null, b: string | null): DiffSeg[] {
  const aw = (a ?? "").length ? (a ?? "").split(/(\s+)/) : [];
  const bw = (b ?? "").length ? (b ?? "").split(/(\s+)/) : [];
  const n = aw.length;
  const m = bw.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffSeg[] = [];
  const push = (type: DiffSeg["type"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.type === type) last.text += text;
    else out.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (aw[i] === bw[j]) {
      push("same", aw[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push("del", aw[i]);
      i++;
    } else {
      push("ins", bw[j]);
      j++;
    }
  }
  while (i < n) push("del", aw[i++]);
  while (j < m) push("ins", bw[j++]);
  return out;
}

// A hunk reads as a "large rewrite" when its text is long or it carries many edits —
// the DD-78 escape-hatch affordance is a visual flag only (no separate endpoint).
function isLargeChange(c: ReviewChange): boolean {
  const chars = c.hunks.reduce(
    (sum, h) => sum + (h.original_text?.length ?? 0) + (h.proposed_text?.length ?? 0),
    0,
  );
  return chars > 1400 || c.hunk_count >= 5;
}

function pct(conf: number | null): string {
  return conf == null ? "—" : `${Math.round(conf * 100)}%`;
}

const KIND_LABEL: Record<ReviewChange["change_kind"], string> = {
  edited: "Edit",
  new: "They added",
  deleted: "They removed",
  abstain: "Uncertain match",
};

// Flatten the baseline snapshot tree into a pick-list for the Re-match action.
interface FlatBaseline {
  id: string;
  label: string;
  snippet: string;
}
function flattenBaseline(nodes: NodeTreeItem[], depth = 0): FlatBaseline[] {
  const out: FlatBaseline[] = [];
  for (const n of nodes) {
    const text = n.heading ?? n.body ?? n.plain_text ?? "";
    out.push({
      id: n.id,
      label: "·".repeat(depth),
      snippet: text.slice(0, 140) || "(untitled clause)",
    });
    if (n.children.length) out.push(...flattenBaseline(n.children, depth + 1));
  }
  return out;
}

export default function RevisionReview({
  params,
}: {
  params: Promise<{ id: string; sessionId: string }>;
}) {
  const { id, sessionId } = use(params);

  const [payload, setPayload] = useState<ReviewPayload | null>(null);
  const [state, setState] = useState<{ kind: "loading" | "ready" | "error"; message?: string }>({
    kind: "loading",
  });
  const [phase, setPhase] = useState<Phase>("structure");

  // Per-target action in flight (hunk id, change id, or "apply"), and the last error.
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Single open inline editor, keyed by hunk/change id, with its draft text.
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");

  // Re-match: the abstain whose baseline picker is open, plus the lazily-loaded
  // baseline tree (shared across abstains once fetched).
  const [rematchFor, setRematchFor] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<{
    loading: boolean;
    error: string | null;
    nodes: FlatBaseline[];
  } | null>(null);

  const [applied, setApplied] = useState<ApplyResult | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const p = await getRevisionReview(sessionId);
      setPayload(p);
      setPhase(p.phase1.abstains.length > 0 ? "structure" : "content");
      setState({ kind: "ready" });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Couldn't load review" });
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const abstains = payload?.phase1.abstains ?? [];
  const anomalies = payload?.phase1.tree_anomalies ?? [];
  const stream = payload?.phase2 ?? [];
  const structureCleared = abstains.length === 0;
  const decided = stream.filter((c) => c.status === "complete").length;
  const allDecided = structureCleared && stream.length > 0 && decided === stream.length;
  const nothingToDo = structureCleared && stream.length === 0;

  // Replace one change in the Phase-2 stream after an in-place decision (no churn).
  function patchChange(updated: ReviewChange) {
    setPayload((p) =>
      p ? { ...p, phase2: p.phase2.map((c) => (c.id === updated.id ? updated : c)) } : p,
    );
  }

  function startEdit(key: string, seed: string) {
    setEditKey(key);
    setEditDraft(seed);
    setActionError(null);
  }
  function cancelEdit() {
    setEditKey(null);
    setEditDraft("");
  }

  async function runHunk(hunk: ReviewHunk, verdict: HunkDecisionAction, finalText?: string) {
    setBusy(hunk.id);
    setActionError(null);
    try {
      const updated = await decideHunk(hunk.id, { verdict, final_text: finalText ?? null });
      patchChange(updated);
      cancelEdit();
    } catch (e) {
      setActionError(
        e instanceof ApiError && e.status === 422
          ? "Donna hasn't staged counter-language for this one — edit it instead."
          : e instanceof Error
            ? e.message
            : "Couldn't record that decision.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function runNode(change: ReviewChange, verdict: NodeDecisionAction, finalText?: string) {
    setBusy(change.id);
    setActionError(null);
    try {
      const updated = await decideNode(change.id, { verdict, final_text: finalText ?? null });
      patchChange(updated);
      cancelEdit();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't record that decision.");
    } finally {
      setBusy(null);
    }
  }

  // Abstain resolution can reclassify other changes, so re-fetch the whole payload.
  async function runConfirm(change: ReviewChange, action: "confirm" | "new", baselineNodeId?: string) {
    setBusy(change.id);
    setActionError(null);
    try {
      await confirmMatch(change.id, { action, baseline_node_id: baselineNodeId ?? null });
      setRematchFor(null);
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't update that match.");
    } finally {
      setBusy(null);
    }
  }
  async function runRematch(change: ReviewChange, baselineNodeId: string) {
    setBusy(change.id);
    setActionError(null);
    try {
      await confirmMatch(change.id, { action: "rematch", baseline_node_id: baselineNodeId });
      setRematchFor(null);
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't re-match that clause.");
    } finally {
      setBusy(null);
    }
  }

  async function openRematch(change: ReviewChange) {
    setRematchFor(change.id);
    if (baseline || !payload) return;
    setBaseline({ loading: true, error: null, nodes: [] });
    try {
      const tree = await getSnapshotTree(id, payload.session.baseline_snapshot_id);
      setBaseline({ loading: false, error: null, nodes: flattenBaseline(tree.nodes) });
    } catch (e) {
      setBaseline({
        loading: false,
        error: e instanceof Error ? e.message : "Couldn't load the baseline.",
        nodes: [],
      });
    }
  }

  async function runApply() {
    setBusy("apply");
    setActionError(null);
    try {
      const res = await applyRevisionSession(sessionId);
      setApplied(res);
    } catch (e) {
      setActionError(
        e instanceof ApiError && e.status === 409
          ? "Some changes are still undecided. Clear them, then apply."
          : e instanceof Error
            ? e.message
            : "Couldn't apply to the working copy.",
      );
    } finally {
      setBusy(null);
    }
  }

  function scrollTo(changeId: string) {
    document.getElementById(`change-${changeId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const sourceLabel = useMemo(() => {
    const s = payload?.session.source ?? "";
    return s === "legal" || s === "legal_team" ? "legal" : "counterparty";
  }, [payload]);

  // ---- render ------------------------------------------------------------

  const topbar = (
    <header className={styles.topbar}>
      <div className={styles.identity}>
        <Link href="/" className={styles.brand} aria-label="donna.ai home">
          donna<span className={styles.dot}>.</span>ai
        </Link>
        <span className={styles.crumb}>Revision review</span>
      </div>
      <div className={styles.topMeta}>
        {payload && (
          <span className={styles.sourceTag}>
            From {sourceLabel}
            <span className={styles.sourceCount}>
              {payload.session.changes_count} change{payload.session.changes_count === 1 ? "" : "s"}
            </span>
          </span>
        )}
      </div>
      <div className={styles.topActions}>
        <Link href={`/contracts/${id}`} className={styles.backLink}>
          ← Back to contract
        </Link>
        {!applied && (
          <button
            type="button"
            className={styles.applyBtn}
            disabled={!allDecided || busy === "apply"}
            title={
              allDecided
                ? "Apply every decision to the working copy"
                : "Decide every change first"
            }
            onClick={() => void runApply()}
          >
            {busy === "apply" ? "Applying…" : "Apply to working copy"}
          </button>
        )}
      </div>
    </header>
  );

  if (state.kind === "loading") {
    return (
      <div className={styles.screen}>
        {topbar}
        <div className={styles.center}>
          <div className={styles.progressTrack} role="progressbar" aria-label="Loading review">
            <div className={styles.progressBar} />
          </div>
          <p className={styles.phaseHint}>Loading their changes…</p>
        </div>
      </div>
    );
  }

  if (state.kind === "error" || !payload) {
    return (
      <div className={styles.screen}>
        {topbar}
        <div className={styles.center}>
          <p className={styles.centerTitle}>Couldn&apos;t open this review</p>
          <p className={styles.errorLine}>{state.message}</p>
          <button className={styles.retry} onClick={() => void load()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (applied) {
    return (
      <div className={styles.screen}>
        {topbar}
        <div className={styles.center}>
          <div className={styles.applyCard}>
            <span className={styles.applyMark} aria-hidden>
              ✓
            </span>
            <h1 className={styles.applyTitle}>Applied to your working copy</h1>
            <p className={styles.applyLead}>
              Their version is frozen as a snapshot; your decisions are now the live draft.
            </p>
            <dl className={styles.applyStats}>
              <div className={styles.applyStat}>
                <dt>Edits applied</dt>
                <dd>{applied.edits_applied}</dd>
              </div>
              <div className={styles.applyStat}>
                <dt>Clauses added</dt>
                <dd>{applied.nodes_inserted}</dd>
              </div>
              <div className={styles.applyStat}>
                <dt>Clauses removed</dt>
                <dd>{applied.nodes_deleted}</dd>
              </div>
              <div className={styles.applyStat}>
                <dt>Issues raised</dt>
                <dd>{applied.issues_created}</dd>
              </div>
            </dl>
            {applied.issues_created > 0 && (
              <p className={styles.applyNote}>
                Rejected changes became open issues, waiting in the contract.
              </p>
            )}
            <Link href={`/contracts/${id}`} className={styles.applyBack}>
              Open the contract
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.screen}>
      {topbar}
      <div className={styles.body}>
        <aside className={styles.rail}>
          {/* The phase spine — the required DD-78 order. Content is locked until the
              abstain queue clears, because a content card has no diff to show until
              its match is confirmed. */}
          <nav className={styles.spine} aria-label="Review phases">
            <button
              type="button"
              className={[styles.spineStop, phase === "structure" ? styles.spineOn : ""].join(" ")}
              onClick={() => setPhase("structure")}
            >
              <span className={styles.spineIndex}>1</span>
              <span className={styles.spineLabel}>Structure</span>
              <span className={styles.spineMeta}>
                {structureCleared ? "clear" : `${abstains.length} to confirm`}
              </span>
            </button>
            <div className={[styles.spineLink, structureCleared ? styles.spineLinkDone : ""].join(" ")} />
            <button
              type="button"
              className={[styles.spineStop, phase === "content" ? styles.spineOn : ""].join(" ")}
              disabled={!structureCleared}
              title={structureCleared ? undefined : "Confirm every match first"}
              onClick={() => structureCleared && setPhase("content")}
            >
              <span className={styles.spineIndex}>2</span>
              <span className={styles.spineLabel}>Content</span>
              <span className={styles.spineMeta}>
                {structureCleared ? `${decided}/${stream.length} decided` : "locked"}
              </span>
            </button>
          </nav>

          <div className={styles.railList}>
            {phase === "structure"
              ? abstains.map((c, idx) => (
                  <button
                    key={c.id}
                    type="button"
                    className={styles.railItem}
                    onClick={() => scrollTo(c.id)}
                  >
                    <span className={styles.railTick} data-tone="pending">
                      ○
                    </span>
                    <span className={styles.railText}>Uncertain match {idx + 1}</span>
                    <span className={styles.railConf}>{pct(c.match_confidence)}</span>
                  </button>
                ))
              : stream.map((c, idx) => (
                  <button
                    key={c.id}
                    type="button"
                    className={styles.railItem}
                    onClick={() => scrollTo(c.id)}
                  >
                    <span
                      className={styles.railTick}
                      data-tone={
                        c.status === "complete"
                          ? "done"
                          : c.status === "partial"
                            ? "partial"
                            : "pending"
                      }
                    >
                      {c.status === "complete" ? "✓" : c.status === "partial" ? "◐" : "○"}
                    </span>
                    <span className={styles.railText}>
                      {idx + 1}. {KIND_LABEL[c.change_kind]}
                    </span>
                  </button>
                ))}
          </div>
        </aside>

        <main className={styles.stream}>
          {phase === "structure" ? (
            <>
              {/* 6a — tree-shape triage (no staged source yet → always clear). */}
              <section className={styles.subhead}>
                <h2 className={styles.subheadTitle}>Tree shape</h2>
                <p className={styles.subheadHint}>
                  Donna auto-corrected the hierarchy on import. These are anything she couldn&apos;t.
                </p>
              </section>
              {anomalies.length === 0 ? (
                <div className={styles.cleared}>Structure looks right — nothing to fix.</div>
              ) : (
                anomalies.map((a) => (
                  <div key={a.node_id} className={styles.anomaly}>
                    {a.reason}
                  </div>
                ))
              )}

              {/* 6b — abstain match-confirm queue, most-uncertain first. */}
              <section className={styles.subhead}>
                <h2 className={styles.subheadTitle}>Confirm the matches</h2>
                <p className={styles.subheadHint}>
                  Donna wasn&apos;t sure these clauses line up. Confirm each before reviewing content.
                </p>
              </section>
              {abstains.length === 0 ? (
                <div className={styles.cleared}>
                  Every match confirmed. Move on to{" "}
                  <button type="button" className={styles.inlineLink} onClick={() => setPhase("content")}>
                    content review →
                  </button>
                </div>
              ) : (
                abstains.map((c) => renderAbstain(c))
              )}
            </>
          ) : nothingToDo ? (
            <div className={styles.cleared}>No content changes to review in this revision.</div>
          ) : (
            <>
              <section className={styles.subhead}>
                <h2 className={styles.subheadTitle}>What they changed</h2>
                <p className={styles.subheadHint}>
                  Top-to-bottom in document order. Judge Donna&apos;s read on each.
                </p>
              </section>
              {stream.map((c) => renderContentCard(c))}
            </>
          )}
          {actionError && (
            <p className={styles.streamError} role="alert">
              {actionError}
            </p>
          )}
        </main>
      </div>
    </div>
  );

  // ---- card renderers (closures over handlers/state) ----------------------

  function renderAbstain(c: ReviewChange) {
    const h = c.hunks[0];
    const theirs = h?.proposed_text ?? "";
    const candidate = h?.original_text ?? "";
    const isRematch = rematchFor === c.id;
    return (
      <article key={c.id} id={`change-${c.id}`} className={styles.card}>
        <header className={styles.cardHead}>
          <span className={[styles.kindChip, styles.kindAbstain].join(" ")}>Uncertain match</span>
          <span className={styles.confChip} title="Donna's match confidence">
            {pct(c.match_confidence)} sure
          </span>
        </header>
        <div className={styles.pair}>
          <div className={styles.pairBlock}>
            <span className={styles.pairLabel}>Their clause</span>
            <p className={styles.clause}>{theirs || "(no text)"}</p>
            {renderSideContext(c.context?.their)}
          </div>
          <div className={styles.pairBlock}>
            <span className={styles.pairLabel}>Closest in your draft</span>
            <p className={styles.clause}>{candidate || "(no candidate)"}</p>
            {renderSideContext(c.context?.baseline)}
          </div>
        </div>
        {isRematch ? (
          <div className={styles.rematch}>
            <div className={styles.rematchHead}>
              <span>Pick the clause it matches</span>
              <button type="button" className={styles.linkBtn} onClick={() => setRematchFor(null)}>
                Cancel
              </button>
            </div>
            {baseline?.loading ? (
              <div className={styles.progressTrack} style={{ width: "100%" }}>
                <div className={styles.progressBar} />
              </div>
            ) : baseline?.error ? (
              <p className={styles.errorLine}>{baseline.error}</p>
            ) : (
              <ul className={styles.pickList}>
                {(baseline?.nodes ?? []).map((n) => (
                  <li key={n.id}>
                    <button
                      type="button"
                      className={styles.pickItem}
                      disabled={busy === c.id}
                      onClick={() => void runRematch(c, n.id)}
                    >
                      <span className={styles.pickIndent} aria-hidden>
                        {n.label}
                      </span>
                      <span className={styles.pickSnippet}>{n.snippet}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={busy === c.id}
              onClick={() => void runConfirm(c, "confirm")}
            >
              Confirm match
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === c.id}
              onClick={() => void runConfirm(c, "new")}
            >
              It&apos;s new
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === c.id}
              onClick={() => void openRematch(c)}
            >
              Re-match…
            </button>
          </div>
        )}
      </article>
    );
  }

  function renderContentCard(c: ReviewChange) {
    const large = isLargeChange(c);
    return (
      <article
        key={c.id}
        id={`change-${c.id}`}
        className={[styles.card, c.status === "complete" ? styles.cardDone : "", large ? styles.cardLarge : ""].join(
          " ",
        )}
      >
        <header className={styles.cardHead}>
          <span className={[styles.kindChip, kindClass(c.change_kind)].join(" ")}>
            {KIND_LABEL[c.change_kind]}
          </span>
          {large && <span className={styles.rewriteChip}>Large rewrite</span>}
          <span className={styles.rollup}>
            {c.hunks_decided}/{c.hunk_count} decided
          </span>
        </header>
        {renderCardContext(c)}
        {c.change_kind === "edited" ? (
          <>
            {renderEditedInContext(c)}
            {c.hunks.map((h) => renderHunk(c, h))}
          </>
        ) : (
          renderWholeNode(c)
        )}
      </article>
    );
  }

  function renderHunk(c: ReviewChange, h: ReviewHunk) {
    const editing = editKey === h.id;
    const decided = h.verdict !== "pending";
    return (
      <div key={h.id} className={styles.hunk}>
        <p className={styles.diff}>
          {wordDiff(h.original_text, h.proposed_text).map((seg, i) => (
            <span key={i} className={segClass(seg.type)}>
              {seg.text}
            </span>
          ))}
        </p>
        {(h.donna_verdict || h.donna_counter_text) && (
          <div className={styles.donna}>
            <span className={styles.donnaMark} aria-hidden>
              D
            </span>
            <div className={styles.donnaBody}>
              {h.donna_verdict && <p className={styles.donnaVerdict}>{h.donna_verdict}</p>}
              {h.donna_counter_text && (
                <p className={styles.donnaCounter}>“{h.donna_counter_text}”</p>
              )}
            </div>
          </div>
        )}
        {decided && !editing && (
          <div className={styles.decided}>
            <span className={styles.decidedChip} data-tone={verdictTone(h.verdict)}>
              {VERDICT_LABEL[h.verdict]}
            </span>
            {h.final_text && h.verdict === "modified" && (
              <span className={styles.decidedText}>{h.final_text}</span>
            )}
          </div>
        )}
        {editing ? (
          <div className={styles.editor}>
            <textarea
              className={styles.editorArea}
              value={editDraft}
              autoFocus
              onChange={(e) => setEditDraft(e.target.value)}
              rows={4}
            />
            <div className={styles.editorBar}>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={!editDraft.trim() || busy === h.id}
                onClick={() => void runHunk(h, "edit", editDraft)}
              >
                {busy === h.id ? "Saving…" : "Save this language"}
              </button>
              <button type="button" className={styles.btnText} onClick={cancelEdit}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === h.id}
              onClick={() => void runHunk(h, "accept")}
            >
              Accept theirs
            </button>
            <button
              type="button"
              className={styles.btnDonna}
              disabled={busy === h.id || !h.donna_counter_text}
              title={h.donna_counter_text ? undefined : "Donna hasn't staged counter-language here"}
              onClick={() => void runHunk(h, "counter")}
            >
              Use Donna&apos;s counter
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === h.id}
              onClick={() => startEdit(h.id, h.donna_counter_text ?? h.proposed_text ?? "")}
            >
              Edit
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === h.id}
              onClick={() => void runHunk(h, "keep")}
            >
              Keep original
            </button>
          </div>
        )}
      </div>
    );
  }

  function renderWholeNode(c: ReviewChange) {
    const h = c.hunks[0];
    const added = c.change_kind === "new";
    const text = added ? h?.proposed_text ?? "" : h?.original_text ?? "";
    const editing = editKey === c.id;
    const decided = c.status === "complete";
    const acceptLabel = added ? "Accept addition" : "Accept removal";
    const rejectLabel = added ? "Reject" : "Keep it";
    return (
      <div className={styles.hunk}>
        <p className={styles.diff}>
          <span className={added ? segClass("ins") : segClass("del")}>{text || "(no text)"}</span>
        </p>
        {h && (h.donna_verdict || h.donna_counter_text) && (
          <div className={styles.donna}>
            <span className={styles.donnaMark} aria-hidden>
              D
            </span>
            <div className={styles.donnaBody}>
              {h.donna_verdict && <p className={styles.donnaVerdict}>{h.donna_verdict}</p>}
              {h.donna_counter_text && (
                <p className={styles.donnaCounter}>“{h.donna_counter_text}”</p>
              )}
            </div>
          </div>
        )}
        {decided && h && !editing && (
          <div className={styles.decided}>
            <span className={styles.decidedChip} data-tone={verdictTone(h.verdict)}>
              {VERDICT_LABEL[h.verdict]}
            </span>
            {h.final_text && h.verdict === "modified" && (
              <span className={styles.decidedText}>{h.final_text}</span>
            )}
          </div>
        )}
        {editing ? (
          <div className={styles.editor}>
            <textarea
              className={styles.editorArea}
              value={editDraft}
              autoFocus
              onChange={(e) => setEditDraft(e.target.value)}
              rows={4}
            />
            <div className={styles.editorBar}>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={!editDraft.trim() || busy === c.id}
                onClick={() => void runNode(c, "edit", editDraft)}
              >
                {busy === c.id ? "Saving…" : "Save this language"}
              </button>
              <button type="button" className={styles.btnText} onClick={cancelEdit}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === c.id}
              onClick={() => void runNode(c, "accept")}
            >
              {acceptLabel}
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === c.id}
              onClick={() => void runNode(c, "reject")}
            >
              {rejectLabel}
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === c.id}
              onClick={() => startEdit(c.id, text)}
            >
              Edit
            </button>
          </div>
        )}
      </div>
    );
  }
}

const VERDICT_LABEL: Record<ReviewHunk["verdict"], string> = {
  pending: "Pending",
  accepted: "Accepted theirs",
  rejected: "Kept ours",
  modified: "Countered",
};

function verdictTone(v: ReviewHunk["verdict"]): string {
  if (v === "accepted") return "accept";
  if (v === "rejected") return "reject";
  if (v === "modified") return "modify";
  return "pending";
}

function kindClass(kind: ReviewChange["change_kind"]): string {
  if (kind === "new") return styles.kindNew;
  if (kind === "deleted") return styles.kindDeleted;
  return styles.kindEdited;
}

function segClass(type: DiffSeg["type"]): string {
  if (type === "ins") return styles.diffIns;
  if (type === "del") return styles.diffDel;
  return styles.diffSame;
}

// Clause identity ("4.2 — Payment Terms"); number alone, heading alone, or both.
function clauseIdentity(ctx: ChangeContextSide): string {
  return [ctx.number, ctx.heading].filter(Boolean).join(" — ");
}

// The side that carries a content card's identity: their incoming clause for a new
// node, the baseline clause for an edit/deletion.
function primarySide(c: ReviewChange): ChangeContextSide | undefined {
  if (!c.context) return undefined;
  return c.change_kind === "new" ? c.context.their : c.context.baseline;
}

// Structural context under one side of an abstain card — its identity + ancestor
// breadcrumb ("Services › Performance") and a preview of what sits under it. This is
// what lets the operator judge a bare-heading match. Nothing renders when the side
// has no resolvable context (e.g. the "(no candidate)" baseline side).
function renderSideContext(ctx: ChangeContextSide | null | undefined) {
  if (!ctx || !ctx.found) return null;
  const identity = clauseIdentity(ctx);
  const path = ctx.breadcrumb.join(" › ");
  const hasMeta = Boolean(identity) || Boolean(path);
  if (!hasMeta && ctx.children_preview.length === 0) return null;
  return (
    <div className={styles.context}>
      {hasMeta && (
        <p className={styles.ctxMeta}>
          {identity && <span className={styles.ctxNum}>{identity}</span>}
          {path && <span className={styles.ctxPath}>{path}</span>}
        </p>
      )}
      {ctx.children_preview.length > 0 && (
        <>
          <span className={styles.ctxLabel}>Contains</span>
          <ul className={styles.ctxChildren}>
            {ctx.children_preview.map((child, i) => (
              <li key={i} className={styles.ctxChild}>
                {child}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// A content card's header context (every kind): clause identity + the breadcrumb of
// the section it sits in, plus the flanking-clause note for adds/removals (where the
// change lands). Read-only — decision actions are untouched.
function renderCardContext(c: ReviewChange) {
  const s = primarySide(c);
  if (!s || !s.found) return null;
  const identity = clauseIdentity(s);
  const path = s.breadcrumb.join(" › ");
  const showNeighbours = c.change_kind === "new" || c.change_kind === "deleted";
  if (!identity && !path && !(showNeighbours && (s.prev_label || s.next_label))) return null;
  return (
    <div className={styles.cardCtx}>
      {(identity || path) && (
        <p className={styles.cardCtxLine}>
          {identity && <span className={styles.cardId}>{identity}</span>}
          {path && <span className={styles.cardCrumb}>{path}</span>}
        </p>
      )}
      {showNeighbours && (s.prev_label || s.next_label) && (
        <p className={styles.neighbour}>
          {s.prev_label && (
            <span>
              After: <em>{s.prev_label}</em>
            </span>
          )}
          {s.next_label && (
            <span>
              Before: <em>{s.next_label}</em>
            </span>
          )}
        </p>
      )}
    </div>
  );
}

// Splice an edited clause's hunks back into its full baseline body so the diff reads
// IN PLACE, with the surrounding sentences visible. Hunk `position_in_body` offsets
// index into `body` (the same string F03b diffed); within each hunk a word-level diff
// gives fine ins/del granularity. Long unchanged runs are collapsed so a huge clause
// still shows context around each change without dumping the whole document.
function inContextSegs(body: string, hunks: ReviewHunk[]): DiffSeg[] {
  const sorted = [...hunks].sort(
    (a, b) => (a.position_in_body ?? 0) - (b.position_in_body ?? 0),
  );
  const out: DiffSeg[] = [];
  const push = (type: DiffSeg["type"], text: string) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.type === type) last.text += text;
    else out.push({ type, text });
  };
  let cursor = 0;
  for (const h of sorted) {
    const pos = h.position_in_body ?? 0;
    if (pos > cursor) push("same", body.slice(cursor, pos));
    const orig = h.original_text ?? "";
    for (const seg of wordDiff(orig, h.proposed_text)) push(seg.type, seg.text);
    cursor = Math.max(cursor, pos + orig.length);
  }
  if (cursor < body.length) push("same", body.slice(cursor));
  return out;
}

// Collapse the middle of a long unchanged run, keeping context on each side of edits.
const CTX_EQUAL_KEEP = 140;
function collapseSame(text: string): string {
  if (text.length <= CTX_EQUAL_KEEP * 2 + 5) return text;
  return `${text.slice(0, CTX_EQUAL_KEEP)} … ${text.slice(-CTX_EQUAL_KEEP)}`;
}

function renderEditedInContext(c: ReviewChange) {
  const body = c.context?.baseline?.body;
  if (!body) return null;
  const segs = inContextSegs(body, c.hunks);
  return (
    <div className={styles.inContext}>
      <span className={styles.ctxLabel}>In context</span>
      <p className={styles.inContextBody}>
        {segs.map((seg, i) => (
          <span key={i} className={segClass(seg.type)}>
            {seg.type === "same" ? collapseSame(seg.text) : seg.text}
          </span>
        ))}
      </p>
    </div>
  );
}
