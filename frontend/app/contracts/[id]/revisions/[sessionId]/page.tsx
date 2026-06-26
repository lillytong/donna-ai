"use client";

// F03c — Mode B revision review, reworked as a TWO-PANE DOCUMENT VIEW (DD-78). The
// counterparty/legal revision is read in document context, not as a stream of floating
// cards: a far-left tracked-changes list that is BOTH a navigator and a to-do tracker,
// and a main reading pane that switches by phase.
//
//   Phase 1 (structure): a BEFORE / AFTER of the two documents side by side, each
//   abstained clause + Donna's proposed match highlighted in both, with confirm /
//   not-a-match / re-match controls (the existing confirm-match endpoint) and bulk
//   confirm/new actions (a client-side loop over the same endpoint). Clears before
//   Phase 2 (the existing gate).
//
//   Phase 2 (content): the document in reading order (the baseline spine reconstructed
//   as the revised reading — see note below), changed clauses neutrally highlighted.
//   Clicking a changed clause expands it inline to its redline + Donna's verdict and
//   adoptable counter-language + Accept theirs / Use Donna's / Edit / Keep (the
//   existing decide endpoints), with a "Brainstorm with Donna" escalation.
//
// Data wiring REUSED: getRevisionReview (hunk redline + decisions, both phases) +
// getRevisionDocument (the light baseline/revised node lists + abstain pairs). The
// change overlay joins on BASELINE node ids (the payload carries no change->revised-node
// linkage), so Phase 2 renders the baseline spine with the changes overlaid in place —
// a deletion strikes where it was, an edit highlights its clause, an addition inserts
// under its proposed parent. This is robust under renumbering (ids, not numbers).

import Link from "next/link";
import { memo, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./review.module.css";
import bs from "../../../cockpit.module.css";
import {
  ApiError,
  applyRevisionSession,
  brainstormTurn,
  closeBrainstorm,
  confirmMatch,
  decideHunk,
  decideNode,
  donnaErrorMessage,
  getRevisionDocument,
  getRevisionReview,
  getSnapshotTree,
  type AbstainMatch,
  type ApplyResult,
  type BrainstormTurn,
  type ChangeContextSide,
  type DocumentChange,
  type DocumentChangeKind,
  type DocumentNode,
  type HunkDecisionAction,
  type NodeDecisionAction,
  type NodeTreeItem,
  type ReviewChange,
  type ReviewHunk,
  type ReviewPayload,
  type RevisionDocumentView,
  type Role,
} from "../../../../lib/api";

type Phase = "structure" | "content";

// A staged issue is required by the brainstorm endpoints, but an undecided revision
// change has none yet (issues are seeded only on apply, for rejects). The nil UUID
// passes the backend's uuid cast and makes get_issue return null → Donna brainstorms
// grounded on the contract + the seeded transcript, with no issue anchor; close then
// distils nothing to persist (no issue to attach to). See the report's follow-up.
const NIL_ISSUE_ID = "00000000-0000-0000-0000-000000000000";

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

function pct(conf: number | null): string {
  return conf == null ? "—" : `${Math.round(conf * 100)}%`;
}

// DD-54 structural roles → the role-based fallback anchor label used when a change's
// clause has neither a derived number nor a heading (e.g. a recital, an appendix title).
const ROLE_LABEL: Record<Role, string> = {
  title: "Title",
  date: "Date",
  parties: "Parties",
  recital: "Recital",
  agreement_statement: "Agreement statement",
  clause: "New clause",
  appendix: "Appendix",
  appendix_title: "Appendix title",
  signature_block: "Signature block",
  drafting_note: "Draft note",
};

const TAG: Record<Exclude<DocumentChangeKind, "shifted">, { label: string; cls: string }> = {
  added: { label: "Added", cls: "tagAdded" },
  deleted: { label: "Deleted", cls: "tagDeleted" },
  modified: { label: "Modified", cls: "tagModified" },
};

// Clause identity ("4.2 — Payment Terms"); number alone, heading alone, or both.
function clauseIdentity(ctx: ChangeContextSide | undefined): string {
  if (!ctx) return "";
  return [ctx.number, ctx.heading].filter(Boolean).join(" — ");
}

// The side that carries a content change's identity: their incoming clause for a new
// node, the baseline clause for an edit/deletion.
function primarySide(c: ReviewChange): ChangeContextSide | undefined {
  if (!c.context) return undefined;
  return c.change_kind === "new" ? c.context.their : c.context.baseline;
}

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

// ---- a memoized document row (perf: only changed/toggled rows re-render) -------------

interface RowProps {
  node: DocumentNode;
  added: boolean;
  kinds: DocumentChangeKind[];
  changeId: string | null;
  active: boolean;
  decided: boolean;
  onSelect: (changeId: string) => void;
  registerRef: (changeId: string, el: HTMLDivElement | null) => void;
  children: React.ReactNode; // the inline expand panel, null unless expanded
}

const DocRow = memo(function DocRow({
  node,
  added,
  kinds,
  changeId,
  active,
  decided,
  onSelect,
  registerRef,
  children,
}: RowProps) {
  const changed = changeId !== null;
  const deleted = kinds.includes("deleted");
  const indent = 10 + node.depth * 18 + (added ? 18 : 0);
  const cls = [
    styles.docRow,
    changed ? styles.docRowChanged : "",
    added ? styles.docRowAdded : "",
    deleted ? styles.docRowDeleted : "",
    active ? styles.docRowActive : "",
    decided ? styles.docRowDecided : "",
  ].join(" ");
  return (
    <div
      ref={changeId ? (el) => registerRef(changeId, el) : undefined}
      className={cls}
      style={{ paddingLeft: indent }}
      onClick={changed ? () => onSelect(changeId as string) : undefined}
      role={changed ? "button" : undefined}
      tabIndex={changed ? 0 : undefined}
      onKeyDown={
        changed
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(changeId as string);
              }
            }
          : undefined
      }
    >
      <div className={styles.docRowLine}>
        {node.clause_number && <span className={styles.docNum}>{node.clause_number}</span>}
        <span className={styles.docText}>{node.text ?? "(empty clause)"}</span>
        {changed && (
          <span className={styles.docTags} aria-hidden>
            {kinds
              .filter((k): k is Exclude<DocumentChangeKind, "shifted"> => k !== "shifted")
              .map((k) => (
                <span key={k} className={[styles.tag, styles[TAG[k].cls]].join(" ")}>
                  {TAG[k].label}
                </span>
              ))}
          </span>
        )}
      </div>
      {children}
    </div>
  );
});

// ---- the brainstorm overlay (reuses the existing endpoints + the cockpit overlay
//      skin via cockpit.module.css; ephemeral by construction — nothing persists) -----

type BsMsg = { role: "user" | "donna"; content: string };

function transcriptToTurns(transcript: BsMsg[]): BrainstormTurn[] {
  const turns: BrainstormTurn[] = [];
  let pending: string | null = null;
  for (const m of transcript) {
    if (m.role === "user") pending = m.content;
    else {
      turns.push({ question: pending ?? "", answer: m.content });
      pending = null;
    }
  }
  return turns;
}

const BrainstormOverlay = memo(function BrainstormOverlay({
  contractId,
  anchor,
  seed,
  onClose,
}: {
  contractId: string;
  anchor: string;
  seed: string;
  onClose: () => void;
}) {
  const [transcript, setTranscript] = useState<BsMsg[]>([{ role: "donna", content: seed }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript, busy]);

  async function send(raw: string) {
    const message = raw.trim();
    if (!message || busy || closing) return;
    const turns = transcriptToTurns(transcript);
    setInput("");
    setError(null);
    setTranscript((t) => [...t, { role: "user", content: message }]);
    setBusy(true);
    try {
      const res = await brainstormTurn(contractId, { issue_id: NIL_ISSUE_ID, turns, message });
      setTranscript((t) => [...t, { role: "donna", content: res.reply }]);
    } catch (e) {
      setError(donnaErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (closing) return;
    setClosing(true);
    setError(null);
    try {
      await closeBrainstorm(contractId, {
        issue_id: NIL_ISSUE_ID,
        turns: transcriptToTurns(transcript),
      });
    } catch {
      // close is best-effort here (no issue to persist onto) — never block the operator.
    } finally {
      onClose();
    }
  }

  return (
    <div className={bs.bsScrim}>
      <section className={bs.bsPanel} role="dialog" aria-modal="true" aria-label="Brainstorm with Donna">
        <header className={bs.bsHead}>
          <div className={bs.bsHeadTitle}>
            <span className={bs.bsHeadMark} aria-hidden>
              ✦
            </span>
            <span>
              Brainstorm
              <span className={bs.bsHeadIssue}>{anchor}</span>
            </span>
          </div>
          <button
            type="button"
            className={bs.bsHeadClose}
            aria-label="Close brainstorm"
            disabled={closing}
            onClick={() => void close()}
          >
            ×
          </button>
        </header>

        <div className={bs.bsScroll} ref={scrollRef}>
          {transcript.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className={[bs.msg, bs.msgUser].join(" ")}>
                <div className={bs.bubbleUser}>{m.content}</div>
              </div>
            ) : (
              <div key={i} className={[bs.msg, bs.msgDonna].join(" ")}>
                <span className={bs.donnaEyebrow}>Donna</span>
                <div className={bs.bubble}>
                  <div className={bs.bubbleText} style={{ whiteSpace: "pre-wrap" }}>
                    {m.content}
                  </div>
                </div>
              </div>
            ),
          )}
          {busy && (
            <div className={[bs.msg, bs.msgDonna].join(" ")}>
              <span className={bs.donnaEyebrow}>Donna</span>
              <div className={[bs.bubble, bs.bubbleThinking].join(" ")}>
                <span className={bs.thinkingDots} aria-hidden>
                  <i />
                  <i />
                  <i />
                </span>
                <span className={bs.thinkingLabel}>Donna&apos;s thinking it through…</span>
              </div>
            </div>
          )}
          {error && <p className={bs.askError}>{error}</p>}
        </div>

        <form
          className={bs.composer}
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <input
            ref={inputRef}
            className={bs.composerInput}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Push back, ask for a sharper line, try another angle…"
            aria-label="Brainstorm with Donna"
            disabled={closing}
          />
          <button
            type="submit"
            className={bs.composerSend}
            aria-label="Send"
            disabled={!input.trim() || busy || closing}
          >
            ↵
          </button>
        </form>

        <div className={bs.bsFoot}>
          {closing ? (
            <div className={bs.bsClosing}>
              <div className={styles.progressTrack} role="progressbar" aria-label="Closing">
                <div className={styles.progressBar} />
              </div>
              <span className={bs.bsClosingLabel}>Closing…</span>
            </div>
          ) : (
            <>
              <button type="button" className={bs.bsCloseBtn} onClick={() => void close()}>
                Close brainstorm
              </button>
              <p className={bs.bsFootNote}>
                This conversation is scratch — it isn&apos;t saved. Apply your decision below to
                carry it forward.
              </p>
            </>
          )}
        </div>
      </section>
    </div>
  );
});

// ---- the inline editor (perf: local draft state so typing never re-renders the doc) --

const InlineEditor = memo(function InlineEditor({
  seed,
  busy,
  onSave,
  onCancel,
}: {
  seed: string;
  busy: boolean;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(seed);
  return (
    <div className={styles.editor}>
      <textarea
        className={styles.editorArea}
        value={draft}
        autoFocus
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
      />
      <div className={styles.editorBar}>
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={!draft.trim() || busy}
          onClick={() => onSave(draft)}
        >
          {busy ? "Saving…" : "Save this language"}
        </button>
        <button type="button" className={styles.btnText} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
});

export default function RevisionReview({
  params,
}: {
  params: Promise<{ id: string; sessionId: string }>;
}) {
  const { id, sessionId } = use(params);

  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [doc, setDoc] = useState<RevisionDocumentView | null>(null);
  const [state, setState] = useState<{ kind: "loading" | "ready" | "error"; message?: string }>({
    kind: "loading",
  });
  const [phase, setPhase] = useState<Phase>("structure");

  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // The selected change (highlighted in both panes) and the inline editor target.
  const [activeChangeId, setActiveChangeId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editKey, setEditKey] = useState<string | null>(null);

  // Re-match picker (Phase 1) — lazily-loaded baseline tree, shared across abstains.
  const [baseline, setBaseline] = useState<{
    loading: boolean;
    error: string | null;
    nodes: FlatBaseline[];
  } | null>(null);
  const [rematchOpen, setRematchOpen] = useState(false);

  const [applied, setApplied] = useState<ApplyResult | null>(null);
  const [brainstormSeed, setBrainstormSeed] = useState<{ anchor: string; seed: string } | null>(null);

  // change-id → row element, for the rail-click scroll + the scroll spy.
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const docPaneRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const [r, d] = await Promise.all([
        getRevisionReview(sessionId),
        getRevisionDocument(id, sessionId),
      ]);
      setReview(r);
      setDoc(d);
      setPhase(r.phase1.abstains.length > 0 ? "structure" : "content");
      setState({ kind: "ready" });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Couldn't load review" });
    }
  }, [id, sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const abstains = review?.phase1.abstains ?? [];
  const stream = review?.phase2 ?? [];
  const structureCleared = abstains.length === 0;
  const pending = stream.filter((c) => c.status !== "complete").length;
  const allDecided = structureCleared && stream.length > 0 && pending === 0;
  const nothingToDo = structureCleared && stream.length === 0;

  const changeById = useMemo(() => {
    const m = new Map<string, ReviewChange>();
    for (const c of stream) m.set(c.id, c);
    for (const c of abstains) m.set(c.id, c);
    return m;
  }, [stream, abstains]);

  const docChangeById = useMemo(() => {
    const m = new Map<string, DocumentChange>();
    for (const c of doc?.changes ?? []) m.set(c.change_id, c);
    return m;
  }, [doc]);

  const baselineNodeById = useMemo(() => {
    const m = new Map<string, DocumentNode>();
    for (const n of doc?.baseline ?? []) m.set(n.node_id, n);
    return m;
  }, [doc]);

  // The Phase-2 document spine: baseline nodes in reading order, each carrying its
  // edited/deleted change; added changes inserted right after their proposed parent.
  type RenderItem =
    | { key: string; kind: "node"; node: DocumentNode; change: ReviewChange | null }
    | { key: string; kind: "added"; node: DocumentNode; change: ReviewChange };

  const renderItems = useMemo<RenderItem[]>(() => {
    if (!doc) return [];
    const editedDeleted = new Map<string, ReviewChange>();
    const addedByParent = new Map<string, ReviewChange[]>();
    for (const c of stream) {
      if (c.change_kind === "new") {
        const key = c.proposed_parent_id ?? "__root__";
        (addedByParent.get(key) ?? addedByParent.set(key, []).get(key)!).push(c);
      } else if (c.node_id) {
        editedDeleted.set(c.node_id, c);
      }
    }
    for (const arr of addedByParent.values()) {
      arr.sort((a, b) => (a.proposed_order_index ?? 0) - (b.proposed_order_index ?? 0));
    }
    // Synthesize a DocumentNode for an added change from its incoming-side context.
    const addedNode = (c: ReviewChange, depth: number): DocumentNode => ({
      node_id: `added-${c.id}`,
      clause_number: c.context?.their?.number ?? null,
      role: "clause",
      depth,
      text: c.context?.their?.body ?? c.hunks[0]?.proposed_text ?? null,
    });

    const items: RenderItem[] = [];
    for (const c of addedByParent.get("__root__") ?? []) {
      items.push({ key: `added-${c.id}`, kind: "added", node: addedNode(c, 0), change: c });
    }
    for (const node of doc.baseline) {
      items.push({
        key: node.node_id,
        kind: "node",
        node,
        change: editedDeleted.get(node.node_id) ?? null,
      });
      for (const c of addedByParent.get(node.node_id) ?? []) {
        items.push({
          key: `added-${c.id}`,
          kind: "added",
          node: addedNode(c, node.depth + 1),
          change: c,
        });
      }
    }
    // Any added change whose parent isn't in the baseline (defensive — shouldn't happen).
    const placed = new Set(items.filter((i) => i.kind === "added").map((i) => i.change.id));
    for (const c of stream) {
      if (c.change_kind === "new" && !placed.has(c.id)) {
        items.push({ key: `added-${c.id}`, kind: "added", node: addedNode(c, 0), change: c });
      }
    }
    return items;
  }, [doc, stream]);

  // Document-order change list for the far-left rail (review.phase2 is already ordered).
  const railChanges = stream;

  const registerRef = useCallback((changeId: string, el: HTMLDivElement | null) => {
    if (el) rowRefs.current.set(changeId, el);
    else rowRefs.current.delete(changeId);
  }, []);

  // Scroll spy: keep the rail's active row in sync as the operator scrolls the document.
  useEffect(() => {
    if (phase !== "content") return;
    const root = docPaneRef.current;
    if (!root) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const top = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) {
          for (const [cid, el] of rowRefs.current) {
            if (el === top.target) {
              setActiveChangeId(cid);
              break;
            }
          }
        }
      },
      { root, rootMargin: "-10% 0px -75% 0px", threshold: 0 },
    );
    for (const el of rowRefs.current.values()) obs.observe(el);
    return () => obs.disconnect();
  }, [phase, renderItems]);

  function selectChange(changeId: string) {
    setActiveChangeId(changeId);
    setExpandedId(changeId);
    setEditKey(null);
    rowRefs.current.get(changeId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Phase 1: selecting an abstain scrolls BOTH columns to its proposed pair.
  function selectAbstain(changeId: string) {
    setActiveChangeId(changeId);
    setRematchOpen(false);
    const m = doc?.abstain_matches.find((x) => x.change_id === changeId);
    const opts: ScrollIntoViewOptions = { behavior: "smooth", block: "center" };
    if (m?.baseline_node_id) document.getElementById(`cmp-b-${m.baseline_node_id}`)?.scrollIntoView(opts);
    if (m?.proposed_received_node_id)
      document.getElementById(`cmp-r-${m.proposed_received_node_id}`)?.scrollIntoView(opts);
  }

  function patchChange(updated: ReviewChange) {
    setReview((p) =>
      p ? { ...p, phase2: p.phase2.map((c) => (c.id === updated.id ? updated : c)) } : p,
    );
  }

  async function runHunk(hunk: ReviewHunk, verdict: HunkDecisionAction, finalText?: string) {
    setBusy(hunk.id);
    setActionError(null);
    try {
      const updated = await decideHunk(hunk.id, { verdict, final_text: finalText ?? null });
      patchChange(updated);
      setEditKey(null);
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
      setEditKey(null);
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
      setRematchOpen(false);
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
      setRematchOpen(false);
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't re-match that clause.");
    } finally {
      setBusy(null);
    }
  }

  // Bulk: there's no server-side bulk endpoint, so loop the existing confirm-match over
  // every still-abstained change. Re-fetch once at the end (each call can reclassify).
  async function runBulk(action: "confirm" | "new") {
    setBusy("bulk");
    setActionError(null);
    try {
      for (const c of abstains) {
        await confirmMatch(c.id, { action, baseline_node_id: null });
      }
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't resolve the remaining matches.");
    } finally {
      setBusy(null);
    }
  }

  async function openRematch(change: ReviewChange) {
    setActiveChangeId(change.id);
    setRematchOpen(true);
    if (baseline || !review) return;
    setBaseline({ loading: true, error: null, nodes: [] });
    try {
      const tree = await getSnapshotTree(id, review.session.baseline_snapshot_id);
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

  function openBrainstorm(c: ReviewChange) {
    setBrainstormSeed({ anchor: anchorLabel(c) || "this change", seed: composeSeed(c) });
  }

  const sourceLabel = useMemo(() => {
    const s = review?.session.source ?? "";
    return s === "legal" || s === "legal_team" ? "legal" : "counterparty";
  }, [review]);

  // The anchor label for a change: clause number/heading, else a role-based fallback.
  function anchorLabel(c: ReviewChange): string {
    const identity = clauseIdentity(primarySide(c));
    if (identity) return identity;
    if (c.change_kind === "new") return "New clause";
    const node = c.node_id ? baselineNodeById.get(c.node_id) : undefined;
    return node ? ROLE_LABEL[node.role] : "Clause";
  }

  // Seed the brainstorm opening turn with this change's before/after + Donna's read.
  function composeSeed(c: ReviewChange): string {
    const id = anchorLabel(c);
    const lines: string[] = [`Let's work through ${id}.`];
    if (c.change_kind === "new") {
      lines.push(`They added:\n${c.hunks[0]?.proposed_text ?? c.context?.their?.body ?? "(new clause)"}`);
    } else if (c.change_kind === "deleted") {
      lines.push(`They deleted:\n${c.hunks[0]?.original_text ?? c.context?.baseline?.body ?? "(clause)"}`);
    } else {
      const edits = c.hunks
        .map((h) => `• "${h.original_text ?? ""}" → "${h.proposed_text ?? ""}"`)
        .join("\n");
      lines.push(`Their edits:\n${edits}`);
    }
    const verdict = c.hunks.find((h) => h.donna_verdict)?.donna_verdict;
    const counter = c.hunks.find((h) => h.donna_counter_text)?.donna_counter_text;
    if (verdict) lines.push(`My read: ${verdict}`);
    if (counter) lines.push(`My counter-language:\n${counter}`);
    lines.push("Tell me to push harder, soften it, or take a different angle.");
    return lines.join("\n\n");
  }

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
        {review && (
          <span className={styles.sourceTag}>
            From {sourceLabel}
            <span className={styles.sourceCount}>
              {review.session.changes_count} change{review.session.changes_count === 1 ? "" : "s"}
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
            title={allDecided ? "Apply every decision to the working copy" : "Decide every change first"}
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
          <p className={styles.phaseHint}>Opening their revision…</p>
        </div>
      </div>
    );
  }

  if (state.kind === "error" || !review || !doc) {
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
              abstain queue clears (a content card has no diff until its match is set). */}
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
                {structureCleared ? `${stream.length - pending}/${stream.length} decided` : "locked"}
              </span>
            </button>
          </nav>

          {/* The to-do tracker: how many changes remain, at a glance. */}
          <div className={styles.tracker}>
            {phase === "structure" ? (
              <>
                <span className={styles.trackerN}>{abstains.length}</span>
                <span className={styles.trackerLabel}>
                  match{abstains.length === 1 ? "" : "es"} to confirm
                </span>
              </>
            ) : (
              <>
                <span className={styles.trackerN} data-done={pending === 0 ? "" : undefined}>
                  {pending}
                </span>
                <span className={styles.trackerLabel}>
                  {pending === 0 ? "all decided" : `pending of ${stream.length}`}
                </span>
              </>
            )}
          </div>

          <div className={styles.railList}>
            {phase === "structure"
              ? abstains.map((c, idx) => (
                  <button
                    key={c.id}
                    type="button"
                    className={[
                      styles.railItem,
                      activeChangeId === c.id ? styles.railItemActive : "",
                    ].join(" ")}
                    onClick={() => selectAbstain(c.id)}
                  >
                    <span className={styles.railTick} data-tone="pending">
                      ○
                    </span>
                    <span className={styles.railText}>{anchorLabel(c) || `Uncertain match ${idx + 1}`}</span>
                    <span className={styles.railConf}>{pct(c.match_confidence)}</span>
                  </button>
                ))
              : railChanges.map((c) => {
                  const dc = docChangeById.get(c.id);
                  const kinds = dc?.kinds ?? [];
                  const done = c.status === "complete";
                  return (
                    <button
                      key={c.id}
                      type="button"
                      className={[
                        styles.railItem,
                        activeChangeId === c.id ? styles.railItemActive : "",
                        done ? styles.railItemDone : "",
                      ].join(" ")}
                      onClick={() => selectChange(c.id)}
                    >
                      <span
                        className={styles.railTick}
                        data-tone={done ? "done" : c.status === "partial" ? "partial" : "pending"}
                      >
                        {done ? "✓" : c.status === "partial" ? "◐" : "○"}
                      </span>
                      <span className={styles.railBody}>
                        <span className={styles.railText}>{anchorLabel(c)}</span>
                        <span className={styles.railTagRow}>
                          {kinds
                            .filter((k): k is Exclude<DocumentChangeKind, "shifted"> => k !== "shifted")
                            .map((k) => (
                              <span key={k} className={[styles.tag, styles[TAG[k].cls]].join(" ")}>
                                {TAG[k].label}
                              </span>
                            ))}
                        </span>
                      </span>
                    </button>
                  );
                })}
          </div>
        </aside>

        {phase === "structure"
          ? renderStructure()
          : nothingToDo
            ? (
                <main className={styles.docPane}>
                  <div className={styles.cleared}>No content changes to review in this revision.</div>
                </main>
              )
            : renderContent()}
      </div>

      {brainstormSeed && (
        <BrainstormOverlay
          contractId={id}
          anchor={brainstormSeed.anchor}
          seed={brainstormSeed.seed}
          onClose={() => setBrainstormSeed(null)}
        />
      )}
    </div>
  );

  // ---- Phase 1: before / after with abstain match-confirm -----------------

  function renderStructure() {
    const active = abstains.find((c) => c.id === activeChangeId) ?? abstains[0] ?? null;
    const match = doc!.abstain_matches.find((m) => m.change_id === active?.id);
    const baselineHi = new Set(
      doc!.abstain_matches.map((m) => m.baseline_node_id).filter((x): x is string => !!x),
    );
    const revisedHi = new Set(
      doc!.abstain_matches
        .map((m) => m.proposed_received_node_id)
        .filter((x): x is string => !!x),
    );
    return (
      <main className={styles.structure}>
        <div className={styles.structureHead}>
          <div>
            <h2 className={styles.subheadTitle}>Confirm the matches</h2>
            <p className={styles.subheadHint}>
              Donna wasn&apos;t sure these clauses line up. Judge each in context, then confirm.
            </p>
          </div>
          {abstains.length > 0 && (
            <div className={styles.bulkBar}>
              <button
                type="button"
                className={styles.btnGhost}
                disabled={busy === "bulk"}
                onClick={() => void runBulk("confirm")}
              >
                {busy === "bulk" ? "Working…" : "Confirm all remaining"}
              </button>
              <button
                type="button"
                className={styles.btnGhost}
                disabled={busy === "bulk"}
                onClick={() => void runBulk("new")}
              >
                Mark rest as new
              </button>
            </div>
          )}
        </div>

        {abstains.length === 0 ? (
          <div className={styles.cleared}>
            Every match confirmed. Move on to{" "}
            <button type="button" className={styles.inlineLink} onClick={() => setPhase("content")}>
              content review →
            </button>
          </div>
        ) : (
          <>
            <div className={styles.compare}>
              <section className={styles.compareCol}>
                <header className={styles.compareHead}>Your draft</header>
                <div className={styles.compareBody}>
                  {doc!.baseline.map((n) => (
                    <CompareRow
                      key={n.node_id}
                      domId={`cmp-b-${n.node_id}`}
                      node={n}
                      flagged={baselineHi.has(n.node_id)}
                      active={!!match && match.baseline_node_id === n.node_id}
                    />
                  ))}
                </div>
              </section>
              <section className={styles.compareCol}>
                <header className={styles.compareHead}>Their version</header>
                <div className={styles.compareBody}>
                  {doc!.revised.map((n) => (
                    <CompareRow
                      key={n.node_id}
                      domId={`cmp-r-${n.node_id}`}
                      node={n}
                      flagged={revisedHi.has(n.node_id)}
                      active={!!match && match.proposed_received_node_id === n.node_id}
                    />
                  ))}
                </div>
              </section>
            </div>

            {active && renderAbstainControls(active)}
          </>
        )}
        {actionError && (
          <p className={styles.streamError} role="alert">
            {actionError}
          </p>
        )}
      </main>
    );
  }

  function renderAbstainControls(c: ReviewChange) {
    return (
      <div className={styles.abstainBar}>
        <div className={styles.abstainWho}>
          <span className={[styles.kindChip, styles.kindAbstain].join(" ")}>Uncertain match</span>
          <span className={styles.abstainName}>{anchorLabel(c)}</span>
          <span className={styles.confChip}>{pct(c.match_confidence)} sure</span>
        </div>
        {rematchOpen ? (
          <div className={styles.rematch}>
            <div className={styles.rematchHead}>
              <span>Pick the clause it matches</span>
              <button type="button" className={styles.linkBtn} onClick={() => setRematchOpen(false)}>
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
              Not a match — it&apos;s new
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === c.id}
              onClick={() => void openRematch(c)}
            >
              Match to a different clause…
            </button>
          </div>
        )}
      </div>
    );
  }

  // ---- Phase 2: the document with changes in place ------------------------

  function renderContent() {
    return (
      <main className={styles.docPane} ref={docPaneRef}>
        <div className={styles.docHead}>
          <h2 className={styles.subheadTitle}>Their revision, in your document</h2>
          <p className={styles.subheadHint}>
            Changed clauses are highlighted. Open one to see the redline and Donna&apos;s read.
          </p>
        </div>
        <div className={styles.docScroll}>
          {renderItems.map((item) => {
            const c = item.change;
            const dc = c ? docChangeById.get(c.id) : undefined;
            const kinds: DocumentChangeKind[] = dc
              ? dc.kinds
              : item.kind === "added"
                ? ["added"]
                : [];
            const expanded = c != null && expandedId === c.id;
            return (
              <DocRow
                key={item.key}
                node={item.node}
                added={item.kind === "added"}
                kinds={kinds}
                changeId={c?.id ?? null}
                active={c != null && activeChangeId === c.id}
                decided={c?.status === "complete"}
                onSelect={selectChange}
                registerRef={registerRef}
              >
                {expanded && c ? (
                  <div className={styles.expand} onClick={(e) => e.stopPropagation()}>
                    {renderExpand(c)}
                  </div>
                ) : null}
              </DocRow>
            );
          })}
        </div>
        {actionError && (
          <p className={styles.streamError} role="alert">
            {actionError}
          </p>
        )}
      </main>
    );
  }

  function renderExpand(c: ReviewChange) {
    return (
      <>
        {renderCardContext(c)}
        {c.change_kind === "edited" ? (
          <>
            {renderEditedInContext(c)}
            {c.hunks.map((h) => renderHunk(c, h))}
          </>
        ) : (
          renderWholeNode(c)
        )}
        <div className={styles.escalate}>
          <button type="button" className={styles.btnDonna} onClick={() => openBrainstorm(c)}>
            Brainstorm with Donna ↗
          </button>
        </div>
      </>
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
              {h.donna_counter_text && <p className={styles.donnaCounter}>“{h.donna_counter_text}”</p>}
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
          <InlineEditor
            seed={h.donna_counter_text ?? h.proposed_text ?? ""}
            busy={busy === h.id}
            onSave={(text) => void runHunk(h, "edit", text)}
            onCancel={() => setEditKey(null)}
          />
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
              Use Donna&apos;s
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === h.id}
              onClick={() => setEditKey(h.id)}
            >
              Edit
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy === h.id}
              onClick={() => void runHunk(h, "keep")}
            >
              Keep
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
              {h.donna_counter_text && <p className={styles.donnaCounter}>“{h.donna_counter_text}”</p>}
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
          <InlineEditor
            seed={h?.donna_counter_text ?? text}
            busy={busy === c.id}
            onSave={(t) => void runNode(c, "edit", t)}
            onCancel={() => setEditKey(null)}
          />
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
            {h?.donna_counter_text && (
              <button
                type="button"
                className={styles.btnDonna}
                disabled={busy === c.id}
                onClick={() => void runNode(c, "edit", h.donna_counter_text ?? "")}
              >
                Use Donna&apos;s
              </button>
            )}
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
              onClick={() => setEditKey(c.id)}
            >
              Edit
            </button>
          </div>
        )}
      </div>
    );
  }
}

// ---- a compare-pane row (Phase 1) -------------------------------------------

const CompareRow = memo(function CompareRow({
  node,
  flagged,
  active,
  domId,
}: {
  node: DocumentNode;
  flagged: boolean;
  active: boolean;
  domId: string;
}) {
  const cls = [
    styles.cmpRow,
    flagged ? styles.cmpFlagged : "",
    active ? styles.cmpActive : "",
  ].join(" ");
  return (
    <div id={domId} className={cls} style={{ paddingLeft: 8 + node.depth * 16 }}>
      {node.clause_number && <span className={styles.cmpNum}>{node.clause_number}</span>}
      <span className={styles.cmpText}>{node.text ?? "(empty clause)"}</span>
    </div>
  );
});

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

function segClass(type: DiffSeg["type"]): string {
  if (type === "ins") return styles.diffIns;
  if (type === "del") return styles.diffDel;
  return styles.diffSame;
}

// A content change's header context (every kind): clause identity + the breadcrumb of
// the section it sits in, plus the flanking-clause note for adds/removals.
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
// IN PLACE, with the surrounding sentences visible.
function inContextSegs(body: string, hunks: ReviewHunk[]): DiffSeg[] {
  const sorted = [...hunks].sort((a, b) => (a.position_in_body ?? 0) - (b.position_in_body ?? 0));
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
