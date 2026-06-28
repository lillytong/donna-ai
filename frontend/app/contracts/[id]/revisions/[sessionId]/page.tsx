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
import { Fragment, memo, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./review.module.css";
import bs from "../../../cockpit.module.css";
import {
  ApiError,
  applyRevisionSession,
  brainstormTurn,
  closeBrainstorm,
  confirmMatch,
  decideCluster,
  decideHunk,
  decideNode,
  donnaErrorMessage,
  getRevisionDocument,
  getRevisionReview,
  getSnapshotTree,
  resetRevisionSession,
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
  type ProjectedNode,
  type ReviewChange,
  type ReviewHunk,
  type ReviewPayload,
  type RevisionDocumentView,
  type Role,
} from "../../../../lib/api";
import { renderRich } from "../../../../lib/richText";

type Phase = "structure" | "content";

// A staged issue is required by the brainstorm endpoints, but an undecided revision
// change has none yet (issues are seeded only on apply, for rejects). The nil UUID
// passes the backend's uuid cast and makes get_issue return null → Donna brainstorms
// grounded on the contract + the seeded transcript, with no issue anchor; close then
// distils nothing to persist (no issue to attach to). See the report's follow-up.
const NIL_ISSUE_ID = "00000000-0000-0000-0000-000000000000";

// One node of an inline diff segment. ins/del/donna/kept/declined carry a hunkId so the
// renderer can make them click targets. "same" segments are never interactive.
//   - ins/del:  a counterparty insertion / deletion (green underline / red strikethrough).
//   - donna:    the language the operator adopted from Donna's counter (purple, solid).
//   - DD-91 reject-trace pair (both SOLID; a rejected MODIFICATION shows BOTH):
//     - declined: a REJECTED counterparty's declined NEW text — their addition, or the
//                 new-text side of a modification — rendered GREY strikethrough (distinct
//                 from the RED `del` strike, which is an active/accepted deletion).
//     - kept:     a REJECTED counterparty change's retained ORIGINAL text — a kept deletion,
//                 or the original side of a modification — rendered SOLID BLACK underline
//                 (distinct from the GREEN `ins` underline by colour).
//   - pending:  true while the change is undecided → a soft tinted PILL (background fill of
//               its own hue) reading "needs your decision"; false once decided → no pill,
//               just colored text + solid underline/strike. Lines are SOLID everywhere.
type DiffSeg = { type: "same" | "ins" | "del" | "donna" | "kept" | "declined"; text: string; hunkId?: string; pending?: boolean };

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

// A non-clause node carries no clause number; show a human-readable role category
// chip instead so front/back-matter (title, parties, recitals, appendices, signature,
// drafting notes) reads as such and is never mistaken for a numbered clause.
const CATEGORY_LABEL: Record<Role, string> = {
  title: "Title",
  date: "Date",
  parties: "Parties",
  recital: "Recital",
  agreement_statement: "Statement",
  clause: "Clause",
  appendix: "Appendix",
  appendix_title: "Appendix",
  signature_block: "Signature",
  drafting_note: "Drafting note",
};

const TAG: Record<Exclude<DocumentChangeKind, "shifted">, { label: string; cls: string }> = {
  added: { label: "Added", cls: "tagAdded" },
  deleted: { label: "Deleted", cls: "tagDeleted" },
  modified: { label: "Modified", cls: "tagModified" },
};

// Donna's advisory verdict ("keep"/"accept"/"counter") capitalized for display.
// "keep" maps to "Reject" — keep semantics = keep our version = reject the counterparty change.
const DONNA_VERDICT_LABEL: Record<string, string> = {
  keep: "Reject",
  accept: "Accept",
  counter: "Counter",
};
function donnaPrettyVerdict(v: string | null): string {
  if (!v) return "";
  const key = v.trim().toLowerCase();
  return DONNA_VERDICT_LABEL[key] ?? (v.charAt(0).toUpperCase() + v.slice(1));
}

// Content-type typography in the document pane — REUSED from the import parse-review
// "Source" panel (app/import/page.tsx renderRich + .sBold/.sHeadingText): bold quoted
// defined terms everywhere, and in front/back matter additionally bold ALL-CAPS
// connectives/party names; heading-role lines bold whole. DocumentNode here carries no
// content_type, so headings are styled by role (title / appendix title).
const FRONT_MATTER_ROLES: ReadonlySet<Role> = new Set<Role>([
  "title",
  "date",
  "parties",
  "recital",
  "agreement_statement",
]);
const BACK_MATTER_ROLES: ReadonlySet<Role> = new Set<Role>([
  "appendix",
  "appendix_title",
  "signature_block",
]);
const HEADING_ROLES: ReadonlySet<Role> = new Set<Role>(["title", "appendix_title"]);

function capsBoldForRole(role: Role): boolean {
  return FRONT_MATTER_ROLES.has(role) || BACK_MATTER_ROLES.has(role);
}

// Plain (non-redline) clause body with import-parity typography: rich emphasis, with
// heading lines bolded whole. A line is a heading when the backend flags it
// (is_heading, mirroring import's content_type==="Heading") OR by structural role
// (title / appendix title) as the pre-ship fallback while is_heading is undefined.
function renderDocText(text: string, role: Role, isHeading: boolean): React.ReactNode {
  const rich = renderRich(text, capsBoldForRole(role), styles.sBold);
  return HEADING_ROLES.has(role) || isHeading ? (
    <span className={styles.sHeadingText}>{rich}</span>
  ) : (
    rich
  );
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
  node: ProjectedNode;
  added: boolean;
  // numbered===false on a projected node: an accepted/pending DELETION shown in place,
  // struck/greyed via .docRowDeleted (the clause is removed from the renumbered tree).
  struck: boolean;
  kinds: DocumentChangeKind[];
  changeId: string | null;
  active: boolean;
  decided: boolean;
  onSelect: (changeId: string) => void;
  registerRef: (changeId: string, el: HTMLDivElement | null) => void;
  // Collapse/expand — the node's flat-list key, whether it has children, current state,
  // and the toggle callback (isolated from the change-select onClick via stopPropagation).
  nodeKey: string;
  isParent: boolean;
  collapsed: boolean;
  onToggleCollapse: (key: string) => void;
  // For edited clauses only: the inline tracked-changes redline rendered in the row body
  // in place of the plain node.text baseline. Undefined for unchanged/added/deleted rows.
  inlineRedline?: React.ReactNode;
}

const DocRow = memo(function DocRow({
  node,
  added,
  struck,
  kinds,
  changeId,
  active,
  decided,
  onSelect,
  registerRef,
  nodeKey,
  isParent,
  collapsed,
  onToggleCollapse,
  inlineRedline,
}: RowProps) {
  const changed = changeId !== null;
  // Placement + depth come from the backend's projected sequence, so indent is depth only.
  const indent = 10 + node.depth * 18;
  const cls = [
    styles.docRow,
    changed ? styles.docRowChanged : "",
    added ? styles.docRowAdded : "",
    struck ? styles.docRowDeleted : "",
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
        {/* Collapse chevron — only on nodes that have children; click is isolated from
            the row-level onSelect via stopPropagation so collapsing never also opens
            the change-select expand panel. */}
        {isParent ? (
          <button
            type="button"
            aria-label={collapsed ? "Expand subtree" : "Collapse subtree"}
            aria-expanded={!collapsed}
            className={styles.docChevron}
            onClick={(e) => {
              e.stopPropagation();
              onToggleCollapse(nodeKey);
            }}
          >
            {collapsed ? "▸" : "▾"}
          </button>
        ) : (
          <span className={styles.docChevronPlaceholder} aria-hidden />
        )}
        {node.clause_number ? (
          <span className={styles.docNum}>{node.clause_number}</span>
        ) : node.role !== "clause" ? (
          <span className={styles.docCat}>{CATEGORY_LABEL[node.role]}</span>
        ) : (
          // A clause-role node with no number = a removed deletion (numbered===false);
          // keep the number-column width, show no number.
          <span className={styles.docNum} aria-hidden />
        )}
        {/* When an inlineRedline is provided (edited clauses), suppress node.text so the
            plain baseline does not show alongside the tracked-changes body. The span is kept
            as an empty flex-1 spacer so the tags stay right-aligned in docRowLine. */}
        <span className={styles.docText}>
          {inlineRedline != null
            ? ""
            : renderDocText(node.text ?? "(empty clause)", node.role, node.is_heading ?? false)}
        </span>
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
      {/* Inline tracked-changes redline for edited clauses — rendered once here in the row
          body so the operator sees the "after with tracked changes" without needing to
          expand. The dock (right pane) carries context + controls + brainstorm (DD-83). */}
      {inlineRedline}
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

  // Guided cursor (DD-83): the focused change + focused hunk within it. The decision dock
  // and the keyboard walk both read this; activeChangeId is also the highlighted clause.
  const [activeChangeId, setActiveChangeId] = useState<string | null>(null);
  const [selectedHunkId, setSelectedHunkId] = useState<string | null>(null);
  // Whether the dock is in edit mode for the focused change (Edit advances on SAVE, DD-83).
  const [editing, setEditing] = useState(false);
  // Decisions the operator has re-opened to re-decide (DD-83). A re-opened hunk renders as
  // pending (dotted) and counts as an OPEN cursor stop again, until a new decision settles
  // it. Client-only — re-deciding overwrites the persisted verdict (no schema change).
  const [reopened, setReopened] = useState<Set<string>>(new Set());
  // Set after a committed decision; the advance effect reads it to move the cursor to the
  // next open stop (or Apply). An effect, not inline, so it runs against FRESH state.
  const [advanceTarget, setAdvanceTarget] = useState<{ changeId: string; hunkId: string } | null>(
    null,
  );
  // Phase-2 document pane collapse state: Set of item keys whose subtrees are hidden.
  // Ephemeral — client-side only, no DB/API (mirrors Mode A import view pattern).
  const [collapsedDocNodes, setCollapsedDocNodes] = useState<Set<string>>(new Set());

  // DD-89 grouped stop: which cluster panels are peeled open (showing per-clause member rows),
  // and which member is currently being inline-edited as a peel-off override. Client-only.
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());
  const [peelEditHunk, setPeelEditHunk] = useState<string | null>(null);

  // Re-match picker (Phase 1) — lazily-loaded baseline tree, shared across abstains.
  const [baseline, setBaseline] = useState<{
    loading: boolean;
    error: string | null;
    nodes: FlatBaseline[];
  } | null>(null);
  const [rematchOpen, setRematchOpen] = useState(false);

  const [applied, setApplied] = useState<ApplyResult | null>(null);
  // DD-86 "Start over": two-stage inline confirm before the destructive reset.
  const [confirmReset, setConfirmReset] = useState(false);
  const [brainstormSeed, setBrainstormSeed] = useState<{ anchor: string; seed: string } | null>(null);

  // change-id → row element, for the rail-click scroll + the scroll spy.
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const docPaneRef = useRef<HTMLDivElement | null>(null);
  const applyBtnRef = useRef<HTMLButtonElement | null>(null);
  // Monotonic refetch token (DD-78 live renumber): a stale projected refetch is ignored
  // when a newer one has started, so out-of-order responses never clobber fresh numbers.
  const refreshSeq = useRef(0);

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

  // Re-fetch ONLY the document view (not the full review) so the projected sequence
  // renumbers/re-places live after a decision, without resetting phase or the cursor
  // (activeChangeId/selectedHunkId are untouched; node_ids are stable so `stops`
  // recompute consistently). Race-guarded: a superseded refetch is dropped (DD-78).
  const refreshDoc = useCallback(async () => {
    const seq = ++refreshSeq.current;
    try {
      const d = await getRevisionDocument(id, sessionId);
      if (seq === refreshSeq.current) setDoc(d);
    } catch {
      // Best-effort: keep the optimistic doc if the renumber refetch fails.
    }
  }, [id, sessionId]);

  // Rejecting an added PARENT cascades reject to its added descendants server-side
  // (their verdicts change but aren't in the single decideNode response). Refetch the
  // review payload so the cascaded children re-render struck. Preserves the cursor
  // (activeChangeId/selectedHunkId are separate state).
  const reviewSeq = useRef(0);
  const refreshReview = useCallback(async () => {
    const seq = ++reviewSeq.current;
    try {
      const r = await getRevisionReview(sessionId);
      if (seq === reviewSeq.current) setReview(r);
    } catch {
      // Best-effort: keep the optimistic review if the cascade refetch fails.
    }
  }, [sessionId]);

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

  // Canonical clause number keyed by baseline node_id — derived from the document view's
  // rendered nodes (role-aware derive_numbers, post-renumber). Use this instead of the
  // stale import-time number stored in change context (c.context.*.number).
  const canonicalNumberByNodeId = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of doc?.baseline ?? []) {
      if (n.clause_number) m.set(n.node_id, n.clause_number);
    }
    return m;
  }, [doc]);

  // The Phase-2 document spine is now the backend's projected reading order (DD-78):
  // baseline clauses in order, each non-rejected added clause already spliced at its real
  // position, deletions anchored in place. `clause_number`/`depth`/`numbered` are
  // recomputed server-side from the current verdicts, so they renumber on each decision.
  const projected = useMemo<ProjectedNode[]>(() => doc?.projected ?? [], [doc]);

  // node_ids of projected nodes with at least one child in document order (the next
  // projected node is strictly deeper). A node must be here to receive a collapse chevron.
  const docNodeParents = useMemo<Set<string>>(() => {
    const s = new Set<string>();
    for (let i = 0; i < projected.length - 1; i++) {
      if (projected[i + 1].depth > projected[i].depth) s.add(projected[i].node_id);
    }
    return s;
  }, [projected]);

  // Projected nodes filtered to only the rows currently visible — a collapsed node hides
  // every subsequent node with a strictly greater depth until depth returns to its own.
  const visibleProjected = useMemo(() => {
    if (collapsedDocNodes.size === 0) return projected;
    const out: ProjectedNode[] = [];
    let hideDeeperThan = Infinity;
    for (const node of projected) {
      if (node.depth > hideDeeperThan) continue;
      hideDeeperThan = Infinity;
      out.push(node);
      if (collapsedDocNodes.has(node.node_id)) hideDeeperThan = node.depth;
    }
    return out;
  }, [projected, collapsedDocNodes]);

  const docAllExpanded = collapsedDocNodes.size === 0;

  const toggleDocAll = useCallback(() => {
    setCollapsedDocNodes((prev) =>
      prev.size === 0 ? new Set(docNodeParents) : new Set(),
    );
  }, [docNodeParents]);

  const toggleDocNode = useCallback((key: string) => {
    setCollapsedDocNodes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // DD-89: peel a grouped stop open / closed (the per-clause member list). Closing also drops
  // any in-progress per-member inline edit.
  const toggleCluster = useCallback((clusterId: string) => {
    setPeelEditHunk(null);
    setExpandedClusters((prev) => {
      const next = new Set(prev);
      if (next.has(clusterId)) next.delete(clusterId);
      else next.add(clusterId);
      return next;
    });
  }, []);

  // Document-order change list for the far-left rail (review.phase2 is already ordered).
  const railChanges = stream;

  const registerRef = useCallback((changeId: string, el: HTMLDivElement | null) => {
    if (el) rowRefs.current.set(changeId, el);
    else rowRefs.current.delete(changeId);
  }, []);

  // ---- cross-document clusters (DD-89 / F34) --------------------------------
  // Member hunks of each cluster (the SAME counterparty edit recurring across clauses),
  // collected in document order. The backend stamps cluster_id/cluster_size on every member
  // (>1); here we group them so the cursor can treat a cluster as ONE grouped stop and the
  // panel can render the peel-off list + mixed summary.
  const clusterMembers = useMemo<Map<string, { hunk: ReviewHunk; change: ReviewChange }[]>>(() => {
    const m = new Map<string, { hunk: ReviewHunk; change: ReviewChange }[]>();
    for (const node of projected) {
      if (!node.change_id) continue;
      const c = changeById.get(node.change_id);
      if (!c) continue;
      for (const h of c.hunks) {
        if (h.cluster_id && h.cluster_size > 1) {
          const arr = m.get(h.cluster_id) ?? [];
          arr.push({ hunk: h, change: c });
          m.set(h.cluster_id, arr);
        }
      }
    }
    return m;
  }, [projected, changeById]);

  // ---- the guided decision cursor (DD-83) -----------------------------------
  // A "stop" is one decision unit in strict document order: each hunk of an edited clause,
  // or the single whole-node hunk of an added/deleted clause. A CLUSTER (DD-89) folds all its
  // member hunks into ONE stop (carrying `clusterId`) placed at the FIRST member's clause; the
  // other members are not separate stops. The cursor walks OPEN stops top-to-bottom.
  type Stop = { changeId: string; hunkId: string; clusterId?: string };

  const stops = useMemo<Stop[]>(() => {
    const out: Stop[] = [];
    const seenCluster = new Set<string>();
    for (const node of projected) {
      if (!node.change_id) continue;
      const c = changeById.get(node.change_id);
      if (!c) continue;
      const hunks =
        c.change_kind === "edited"
          ? [...c.hunks].sort((a, b) => (a.position_in_body ?? 0) - (b.position_in_body ?? 0))
          : c.hunks.slice(0, 1);
      for (const h of hunks) {
        if (!h) continue;
        if (h.cluster_id && h.cluster_size > 1) {
          if (seenCluster.has(h.cluster_id)) continue;
          seenCluster.add(h.cluster_id);
          out.push({ changeId: c.id, hunkId: h.id, clusterId: h.cluster_id });
        } else {
          out.push({ changeId: c.id, hunkId: h.id });
        }
      }
    }
    return out;
  }, [projected, changeById]);

  const liveHunk = useCallback(
    (stop: Stop): ReviewHunk | null => {
      const c = changeById.get(stop.changeId);
      return c?.hunks.find((h) => h.id === stop.hunkId) ?? null;
    },
    [changeById],
  );

  // OPEN = a cursor stop: hunk verdict still pending, OR re-opened to re-decide. A CLUSTER stop
  // is open while ANY member is still pending/re-opened — deciding the group (or peeling every
  // member) closes it; a peel-off that diverges keeps every member decided, so the group stays
  // closed (decided-with-exception, DD-89), surfaced as the mixed summary, not a re-opened stop.
  const isOpen = useCallback(
    (stop: Stop): boolean => {
      if (stop.clusterId) {
        const members = clusterMembers.get(stop.clusterId) ?? [];
        return members.some(({ hunk }) => reopened.has(hunk.id) || hunk.verdict === "pending");
      }
      if (reopened.has(stop.hunkId)) return true;
      const h = liveHunk(stop);
      return h ? h.verdict === "pending" : false;
    },
    [reopened, liveHunk, clusterMembers],
  );

  const scrollToChange = useCallback((changeId: string) => {
    rowRefs.current.get(changeId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const focusStop = useCallback(
    (stop: Stop, scroll = true) => {
      setActiveChangeId(stop.changeId);
      setSelectedHunkId(stop.hunkId);
      setEditing(false);
      // BUG 2 fix: drop any reopened-but-undecided hunks belonging to a clause we're leaving.
      // An abandoned re-open must NOT keep rendering a decided change (esp. a REJECT) through
      // the pending branch — that re-shows the counterparty addition green and reads as
      // "accepted". Only the focused clause may hold reopened hunks; a fresh decision clears
      // them (runHunk/runNode), and plain navigation (focusChange/stepClause/stepOpen all go
      // through here) prunes them, so a reject reverts to its persisted kept-original render.
      setReopened((prev) => {
        if (prev.size === 0) return prev;
        const keep = new Set(changeById.get(stop.changeId)?.hunks.map((h) => h.id) ?? []);
        let dropped = false;
        const next = new Set<string>();
        for (const id of prev) {
          if (keep.has(id)) next.add(id);
          else dropped = true;
        }
        return dropped ? next : prev;
      });
      if (scroll) scrollToChange(stop.changeId);
    },
    [scrollToChange, changeById],
  );

  // End of the walk: drop the focus and send the cursor to the Apply affordance.
  const focusApply = useCallback(() => {
    setActiveChangeId(null);
    setSelectedHunkId(null);
    setEditing(false);
    applyBtnRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    applyBtnRef.current?.focus();
  }, []);

  // Rail/row click: focus a change's FIRST open stop; if every stop is already decided,
  // focus the first WITHOUT re-opening it — navigation must not flip a settled verdict back
  // to pending (BUG 2: an adopted Donna clause would re-render as a raw accepted counterparty
  // change). The persisted verdict keeps rendering; re-deciding still works (decideFocused
  // overwrites the verdict regardless of the reopened flag). A deliberate fragment click
  // (focusHunk) is the gesture that re-opens for re-decide.
  function focusChange(changeId: string) {
    const changeStops = stops.filter((s) => s.changeId === changeId);
    if (changeStops.length === 0) {
      // A clause whose only change is a FOLDED cluster member (its stop lives under the cluster
      // representative, a different clause): focus the grouped stop instead of nothing.
      const c = changeById.get(changeId);
      const member = c?.hunks.find((h) => h.cluster_id && h.cluster_size > 1);
      const rep = member?.cluster_id ? stops.find((s) => s.clusterId === member.cluster_id) : null;
      if (rep) focusStop(rep);
      return;
    }
    const open = changeStops.find(isOpen);
    focusStop(open ?? changeStops[0]);
  }

  // Fragment click: focus a specific hunk; re-open it if already decided.
  function focusHunk(changeId: string, hunkId: string) {
    if (!isOpen({ changeId, hunkId })) setReopened((p) => new Set(p).add(hunkId));
    focusStop({ changeId, hunkId });
  }

  // Keyboard < / > : step to the previous / next OPEN stop (cyclic; skips decided). If
  // nothing is open, land on Apply.
  function stepOpen(dir: 1 | -1) {
    const n = stops.length;
    if (n === 0) return;
    const cur = selectedHunkId ? stops.findIndex((s) => s.hunkId === selectedHunkId) : -1;
    for (let k = 1; k <= n; k++) {
      const idx = (((cur + dir * k) % n) + n) % n;
      if (isOpen(stops[idx])) {
        focusStop(stops[idx]);
        return;
      }
    }
    focusApply();
  }

  // ---- clause-level navigation (DD-83) ------------------------------------
  // Auto-advance stays WITHIN a clause; moving BETWEEN clauses is manual (the Prev/Next
  // buttons here, or < / > for fine-grained open-stop nav). clauseOrder = document-order
  // unique changeIds; openClauseIds = clauses with >=1 OPEN stop.
  const clauseOrder = useMemo<string[]>(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const s of stops) {
      if (!seen.has(s.changeId)) {
        seen.add(s.changeId);
        out.push(s.changeId);
      }
    }
    return out;
  }, [stops]);

  const openClauseIds = useMemo<Set<string>>(() => {
    const s = new Set<string>();
    for (const stop of stops) if (isOpen(stop)) s.add(stop.changeId);
    return s;
  }, [stops, isOpen]);

  const hasOpenClauses = openClauseIds.size > 0;

  // Prev/Next clause: from activeChangeId, cyclically find the next/prev clause that has
  // an open stop and focus its FIRST open stop.
  const stepClause = useCallback(
    (dir: 1 | -1) => {
      const n = clauseOrder.length;
      if (n === 0) return;
      let cur = activeChangeId ? clauseOrder.indexOf(activeChangeId) : -1;
      if (cur === -1) cur = dir === 1 ? -1 : 0;
      for (let k = 1; k <= n; k++) {
        const cid = clauseOrder[(((cur + dir * k) % n) + n) % n];
        if (!openClauseIds.has(cid)) continue;
        const first = stops.find((s) => s.changeId === cid && isOpen(s));
        if (first) {
          focusStop(first);
          return;
        }
      }
    },
    [clauseOrder, activeChangeId, openClauseIds, stops, isOpen, focusStop],
  );

  // Decide the focused stop. Maps the operator actions onto the edited-hunk endpoint
  // (accept/counter/edit/keep) or the whole-node endpoint (accept/reject/edit). The cursor
  // auto-advances afterwards via the advance effect (once state settles).
  function decideFocused(action: "accept" | "reject" | "donna") {
    if (!activeChangeId || !selectedHunkId || busy) return;
    const c = changeById.get(activeChangeId);
    const h = c?.hunks.find((x) => x.id === selectedHunkId);
    if (!c || !h) return;
    // Grouped stop (DD-89): one decision fans to every member of the cluster.
    if (h.cluster_id && h.cluster_size > 1) {
      if (action === "accept") void runCluster(h.cluster_id, "accept");
      else if (action === "reject") void runCluster(h.cluster_id, "keep");
      else if (action === "donna" && h.donna_counter_text) void runCluster(h.cluster_id, "counter");
      return;
    }
    if (c.change_kind === "edited") {
      if (action === "accept") void runHunk(h, "accept");
      else if (action === "reject") void runHunk(h, "keep");
      else if (action === "donna" && h.donna_counter_text) void runHunk(h, "counter");
    } else {
      if (action === "accept") void runNode(c, "accept");
      else if (action === "reject") void runNode(c, "reject");
      else if (action === "donna" && h.donna_counter_text) void runNode(c, "edit", h.donna_counter_text);
    }
  }

  function editFocused() {
    if (!activeChangeId || !selectedHunkId) return;
    setEditing(true);
  }

  // Edit commits (and advances) on SAVE, not on the Edit button (DD-83).
  function saveEdit(text: string) {
    if (!activeChangeId || !selectedHunkId) return;
    const c = changeById.get(activeChangeId);
    const h = c?.hunks.find((x) => x.id === selectedHunkId);
    if (!c || !h) return;
    if (h.cluster_id && h.cluster_size > 1) void runCluster(h.cluster_id, "edit", text);
    else if (c.change_kind === "edited") void runHunk(h, "edit", text);
    else void runNode(c, "edit", text);
  }

  // After a committed decision, advance the cursor to the next OPEN stop (forward from the
  // decided stop, then wrap to the first open above; Apply when none remain). Runs in an
  // effect so it sees the freshly-patched verdict + cleared re-open flag.
  useEffect(() => {
    if (!advanceTarget) return;
    const { changeId, hunkId } = advanceTarget;
    const i = stops.findIndex((s) => s.hunkId === hunkId);
    setAdvanceTarget(null);
    // Auto-advance ONLY to the next OPEN stop WITH THE SAME changeId (same-clause stops are
    // contiguous, but filter by changeId to be safe). When the clause has no more open
    // stops, STOP — keep the cursor on the just-decided clause; the operator uses Prev/Next
    // (or < / >) to move on (DD-83). No auto-jump to another clause or to Apply.
    for (let j = i + 1; j < stops.length; j++) {
      if (stops[j].changeId === changeId && isOpen(stops[j])) {
        focusStop(stops[j]);
        return;
      }
    }
  }, [advanceTarget, stops, isOpen, focusStop]);

  // On first entry to the content phase, focus the first open stop (no scroll yank).
  const contentInitRef = useRef(false);
  useEffect(() => {
    if (phase !== "content" || contentInitRef.current || stops.length === 0) return;
    contentInitRef.current = true;
    const open = stops.find(isOpen);
    if (open) focusStop(open, false);
  }, [phase, stops, isOpen, focusStop]);

  // Keyboard shortcuts (DD-83). Latest handlers via a ref so the listener attaches once per
  // phase but never goes stale. ALL shortcuts are disabled while a text input/textarea is
  // focused or the dock is editing (so typing the replacement never triggers them); < / >
  // work regardless of focus otherwise.
  const kbRef = useRef({ editing, stepOpen, decideFocused, editFocused });
  kbRef.current = { editing, stepOpen, decideFocused, editFocused };
  useEffect(() => {
    if (phase !== "content" || applied || brainstormSeed) return;
    function onKey(e: KeyboardEvent) {
      const h = kbRef.current;
      const el = document.activeElement as HTMLElement | null;
      const typing =
        el?.tagName === "INPUT" || el?.tagName === "TEXTAREA" || el?.isContentEditable === true;
      if (h.editing || typing) return;
      if (e.key === ">") {
        e.preventDefault();
        h.stepOpen(1);
        return;
      }
      if (e.key === "<") {
        e.preventDefault();
        h.stepOpen(-1);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "a") {
        e.preventDefault();
        h.decideFocused("accept");
      } else if (k === "r") {
        e.preventDefault();
        h.decideFocused("reject");
      } else if (k === "d") {
        e.preventDefault();
        h.decideFocused("donna");
      } else if (k === "e") {
        e.preventDefault();
        h.editFocused();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, applied, brainstormSeed]);

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
    // The decide endpoints return the change WITHOUT `context` (the redline's
    // baseline/their body + heading). Context is static across a verdict change, so
    // carry it forward from the prior change — otherwise the inline redline loses its
    // source and the whole clause falls back to plain text.
    setReview((p) =>
      p
        ? {
            ...p,
            phase2: p.phase2.map((c) =>
              c.id === updated.id ? { ...updated, context: updated.context ?? c.context } : c,
            ),
          }
        : p,
    );
  }

  async function runHunk(hunk: ReviewHunk, verdict: HunkDecisionAction, finalText?: string) {
    setBusy(hunk.id);
    setActionError(null);
    try {
      const updated = await decideHunk(hunk.id, { verdict, final_text: finalText ?? null });
      patchChange(updated);
      setReopened((p) => {
        if (!p.has(hunk.id)) return p;
        const n = new Set(p);
        n.delete(hunk.id);
        return n;
      });
      setEditing(false);
      setAdvanceTarget({ changeId: updated.id, hunkId: hunk.id });
      void refreshDoc();
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
    const hunkId = change.hunks[0]?.id;
    setBusy(change.id);
    setActionError(null);
    try {
      const updated = await decideNode(change.id, { verdict, final_text: finalText ?? null });
      patchChange(updated);
      if (hunkId) {
        setReopened((p) => {
          if (!p.has(hunkId)) return p;
          const n = new Set(p);
          n.delete(hunkId);
          return n;
        });
        setAdvanceTarget({ changeId: updated.id, hunkId });
      }
      setEditing(false);
      void refreshDoc();
      // Reject of an added clause cascades to its added sub-clauses server-side.
      if (verdict === "reject" && change.change_kind === "new") void refreshReview();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't record that decision.");
    } finally {
      setBusy(null);
    }
  }

  // DD-89 grouped-stop decision: one verdict fanned to every member of the cluster in a single
  // transaction. Members span multiple change rows, so the server returns the refreshed review
  // payload — apply it wholesale (one refresh), clear any re-open flags on the members, and
  // re-fetch the document view so the projected numbers settle.
  async function runCluster(clusterId: string, verdict: HunkDecisionAction, finalText?: string) {
    setBusy(clusterId);
    setActionError(null);
    try {
      const payload = await decideCluster(sessionId, clusterId, verdict, finalText);
      setReview(payload);
      setReopened((p) => {
        const members = clusterMembers.get(clusterId);
        if (p.size === 0 || !members) return p;
        const n = new Set(p);
        for (const { hunk } of members) n.delete(hunk.id);
        return n;
      });
      setEditing(false);
      void refreshDoc();
    } catch (e) {
      setActionError(
        verdict === "counter" && e instanceof ApiError && e.status === 422
          ? "Donna hasn't staged counter-language for this one — edit it instead."
          : e instanceof Error
            ? e.message
            : "Couldn't record that decision.",
      );
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

  // DD-86 reset: discard every decision and re-seat the review at its fresh all-pending
  // state WITHOUT a full reload — apply the returned payload exactly like a fresh load,
  // clear local UI state, refresh the document view, and re-derive the phase.
  async function runReset() {
    setBusy("reset");
    setActionError(null);
    try {
      const payload = await resetRevisionSession(id, sessionId);
      setReview(payload);
      setReopened(new Set());
      setActiveChangeId(null);
      setSelectedHunkId(null);
      setEditing(false);
      setApplied(null);
      setRematchOpen(false);
      setCollapsedDocNodes(new Set());
      contentInitRef.current = false;
      setPhase(payload.phase1.abstains.length > 0 ? "structure" : "content");
      await refreshDoc();
      setConfirmReset(false);
    } catch (e) {
      setActionError(
        e instanceof ApiError && e.status === 409
          ? "This review was already applied to your working copy — it can't be reset."
          : e instanceof ApiError && e.status === 404
            ? "This review session is no longer available."
            : e instanceof Error
              ? e.message
              : "Couldn't start over.",
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

  // The anchor label for a change: canonical clause number (from the document view,
  // role-aware, correct after renumbering) + heading if present; ELSE the CATEGORY_LABEL
  // the DocRow chip shows for non-clause roles (Recital, Parties, Appendix, etc.).
  // NEVER falls back to the stale import-time side?.number so the rail and right pane
  // always show identical labels.
  function anchorLabel(c: ReviewChange): string {
    const canonicalNum = c.node_id ? (canonicalNumberByNodeId.get(c.node_id) ?? null) : null;
    if (canonicalNum !== null) {
      const heading = primarySide(c)?.heading ?? null;
      return [canonicalNum, heading].filter(Boolean).join(" — ");
    }
    // New clause: no baseline node; use incoming side heading if available.
    if (c.change_kind === "new") {
      const side = primarySide(c);
      return side?.heading ?? "New clause";
    }
    // Non-clause role (recital, parties, appendix, etc.): use the category label
    // the DocRow chip shows -- never the stale positional side?.number.
    const node = c.node_id ? baselineNodeById.get(c.node_id) : undefined;
    return node ? CATEGORY_LABEL[node.role] : "Clause";
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
    if (verdict) lines.push(`My read: ${donnaPrettyVerdict(verdict)}`);
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
        <span className={styles.crumb}>Reviewing {sourceLabel}</span>
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
          <>
            {confirmReset ? (
              <span
                className={styles.startOverConfirm}
                role="alertdialog"
                aria-label="Start over?"
              >
                <span className={styles.startOverText}>
                  Discard all your decisions and restart this review?
                </span>
                <button
                  type="button"
                  className={styles.startOverDanger}
                  disabled={busy === "reset"}
                  onClick={() => void runReset()}
                >
                  {busy === "reset" ? "Starting over…" : "Start over"}
                </button>
                <button
                  type="button"
                  className={styles.btnText}
                  disabled={busy === "reset"}
                  onClick={() => setConfirmReset(false)}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                className={styles.btnGhost}
                disabled={busy != null}
                title="Discard every decision and restart this review"
                onClick={() => setConfirmReset(true)}
              >
                Start over
              </button>
            )}
            <button
              ref={applyBtnRef}
              type="button"
              className={styles.applyBtn}
              disabled={!allDecided || busy === "apply"}
              title={allDecided ? "Apply every decision to the working copy" : "Decide every change first"}
              onClick={() => void runApply()}
            >
              {busy === "apply" ? "Applying…" : "Apply to working copy"}
            </button>
          </>
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
                      onClick={() => focusChange(c.id)}
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
          <div className={styles.docHeadRow}>
            <div className={styles.docNav}>
              <button
                type="button"
                className={styles.docNavBtn}
                disabled={!hasOpenClauses}
                title="Previous clause with an open change"
                onClick={() => stepClause(-1)}
              >
                ‹ Prev
              </button>
              <button
                type="button"
                className={styles.docNavBtn}
                disabled={!hasOpenClauses}
                title="Next clause with an open change"
                onClick={() => stepClause(1)}
              >
                Next ›
              </button>
            </div>
            {docNodeParents.size > 0 && (
              <button
                type="button"
                className={styles.docCollapseBtn}
                onClick={toggleDocAll}
              >
                {docAllExpanded ? "Collapse all" : "Expand all"}
              </button>
            )}
          </div>
        </div>
        <div className={styles.docScroll}>
          {visibleProjected.map((node) => {
            const c = node.change_id ? (changeById.get(node.change_id) ?? null) : null;
            // Tags chip from the projected node's single change_kind ("shifted" never tagged).
            const kinds: DocumentChangeKind[] =
              node.change_kind && node.change_kind !== "shifted" ? [node.change_kind] : [];
            const isActive = c != null && activeChangeId === c.id;

            // The inline tracked-changes redline rendered in the row body in place of plain
            // node.text, for EVERY change kind (edited / added / deleted) — ONCE (DD-83),
            // never a box-below copy. Clicking a change fragment focuses that hunk; the inline
            // decision panel (rendered under the active row, below) carries the controls. The
            // focused clause highlights its selected fragment; re-opened decisions render
            // pending (dotted) via `reopened`.
            const inlineRedline = c
              ? renderInlineTrackedClause(
                  c,
                  isActive ? selectedHunkId : null,
                  (hunkId, rejectTrace) => {
                    if (!hunkId) return;
                    // Reject-trace spans (kept / declined) focus WITHOUT re-opening — the
                    // grey-strike / black-underline stays and the dock offers re-decide
                    // (DD-91). Pending/countered spans re-open via focusHunk to re-decide.
                    if (rejectTrace) focusStop({ changeId: c.id, hunkId });
                    else focusHunk(c.id, hunkId);
                  },
                  reopened,
                  node.role,
                )
              : undefined;

            return (
              <Fragment key={node.node_id}>
                <DocRow
                  node={node}
                  added={node.change_kind === "added"}
                  // A rejected addition is also numbered===false, but it must read as a
                  // struck ADDITION (its inline redline handles that), not the grey deletion
                  // row — so the grey treatment applies only to removed deletions.
                  struck={node.numbered === false && node.change_kind !== "added"}
                  kinds={kinds}
                  changeId={c?.id ?? null}
                  active={isActive}
                  decided={c?.status === "complete"}
                  onSelect={focusChange}
                  registerRef={registerRef}
                  nodeKey={node.node_id}
                  isParent={docNodeParents.has(node.node_id)}
                  collapsed={collapsedDocNodes.has(node.node_id)}
                  onToggleCollapse={toggleDocNode}
                  inlineRedline={inlineRedline}
                />
                {isActive && c && renderInlinePanel(c)}
                {/* Non-focused changes surface Donna's recommendation read-only and compact
                    (verdict + rationale + counter), so EVERY change shows the rec up front —
                    not just the focused one. The focused change renders its rec inside
                    renderInlinePanel (with the full controls), so this branch is !isActive
                    only → no double-render. Shown for ALL changes with a rec regardless of
                    status (DD-91: the rec persists through decided states). */}
                {!isActive && c && renderDonnaRec(c)}
              </Fragment>
            );
          })}
        </div>
      </main>
    );
  }

  // Unified Donna recommendation block — single shared renderer called by BOTH
  // renderHunkMenu (edited hunks) and renderWholeNode (added/deleted clauses).
  // Shows "Donna" label + bold verdict + one-line rationale, with counter-language below.
  // Verdict map: accept->"Accept", counter->"Counter", keep->"Reject", edit->"Edit".
  function renderDonnaBlock(h: ReviewHunk) {
    if (!h.donna_verdict && !h.donna_counter_text) return null;
    const verdictLabel = h.donna_verdict ? donnaPrettyVerdict(h.donna_verdict) : null;
    return (
      <div className={styles.donna}>
        <span className={styles.donnaMark} aria-hidden>
          Donna
        </span>
        <div className={styles.donnaBody}>
          {verdictLabel && (
            <p className={styles.donnaVerdict}>
              <strong>{verdictLabel}</strong>
              {h.donna_rationale && <> &mdash; {h.donna_rationale}</>}
            </p>
          )}
          {h.donna_counter_text && (
            <p className={styles.donnaCounter}>&ldquo;{h.donna_counter_text}&rdquo;</p>
          )}
        </div>
      </div>
    );
  }

  // Compact, read-only Donna recommendation surfaced under EVERY non-focused change that
  // has one — the verdict + a concise rationale line (+ counter language if staged). It
  // uses the change's representative hunk (first hunk carrying a verdict or counter) and
  // self-guards null when none exists, so rec-less rows stay clean. Deliberately lighter
  // than the focused panel's filled .donna box — a hairline Donna-purple rule + small mark
  // + prominent verdict — so a 30+ change stack stays scannable, not a wall of purple boxes.
  // No controls: those live only in the focused panel (renderInlinePanel).
  function renderDonnaRec(c: ReviewChange) {
    const h = c.hunks.find((x) => x.donna_verdict || x.donna_counter_text);
    if (!h) return null;
    const verdictLabel = h.donna_verdict ? donnaPrettyVerdict(h.donna_verdict) : null;
    return (
      <p className={styles.donnaRec}>
        <span className={styles.donnaRecMark} aria-hidden>
          Donna
        </span>
        {verdictLabel && <strong className={styles.donnaRecVerdict}>{verdictLabel}</strong>}
        {h.donna_rationale && <span className={styles.donnaRecRationale}>{h.donna_rationale}</span>}
        {h.donna_counter_text && (
          <span className={styles.donnaRecCounter}>&ldquo;{h.donna_counter_text}&rdquo;</span>
        )}
      </p>
    );
  }

  // ---- the inline decision panel (DD-83, revised: dock relocated inline) ---------
  // The single home for Phase-2 decision controls, rendered INLINE in the document flow
  // directly below the FOCUSED clause's row (activeChangeId + selectedHunkId). Only the
  // focused clause shows a panel (one at a time). The redline stays in place in the row
  // above; this panel carries the clause context, Donna's recommendation, and the action
  // buttons. Clicking a change focuses it (rendering this panel under it); the cursor walk
  // / `<`/`>` move the focus; Edit commits on SAVE (advancing the cursor), not on click.
  function renderInlinePanel(c: ReviewChange) {
    const h = selectedHunkId ? (c.hunks.find((x) => x.id === selectedHunkId) ?? null) : null;
    // No selected hunk for the focused clause (defensive — focusStop always sets one).
    if (!h) return null;

    const added = c.change_kind === "new";
    // Two-signal button encoding (DD-83): rec = what Donna recommends, chosen = the operator's
    // committed decision. Both render on the SAME row so agreement vs override is legible at a glance.
    const rec = recButton(h.donna_verdict);
    const chosen = chosenButton(h);
    // DD-89 grouped stop: this clause's edit recurs across `cluster_size` clauses, so the
    // decision buttons fan to ALL members (decide-once); the panel adds the "appears in N"
    // header, an expand-to-peel-off list, and a mixed summary once a member is overridden.
    const isCluster = !!h.cluster_id && h.cluster_size > 1;
    const clusterId = h.cluster_id;
    const members = isCluster && clusterId ? (clusterMembers.get(clusterId) ?? []) : [];
    const expanded = !!clusterId && expandedClusters.has(clusterId);
    const tally = clusterTally(members);
    const mixed = isCluster && clusterDivergent(members);
    const majority = clusterMajority(tally);
    const allTag = isCluster ? ` · all ${h.cluster_size}` : "";
    const acceptBase = added ? "Accept addition" : c.change_kind === "deleted" ? "Accept removal" : "Accept theirs";
    const acceptLabel = `${acceptBase}${allTag}`;
    const busyHere = busy === c.id || busy === h.id || (clusterId ? busy === clusterId : false);

    return (
      <div className={styles.inlinePanel} aria-label="Decision">
        {isCluster && clusterId && (
          <div className={styles.clusterHead}>
            <span className={styles.clusterBadge}>This change appears in {h.cluster_size} clauses</span>
            <button
              type="button"
              className={styles.clusterToggle}
              aria-expanded={expanded}
              onClick={() => toggleCluster(clusterId)}
            >
              {expanded ? "Hide clauses" : "Review each clause"}
            </button>
          </div>
        )}
        {mixed && (
          <p className={styles.clusterMixed}>
            {(["accepted", "rejected", "modified", "pending"] as ReviewHunk["verdict"][])
              .filter((v) => tally[v] > 0)
              .map((v, i) => (
                <span key={v} className={styles.clusterCount} data-verdict={v}>
                  {i > 0 && <span className={styles.clusterCountSep}>·</span>}
                  {tally[v]} {STORED_VERDICT_LABEL[v]}
                </span>
              ))}
            {majority && (
              <span className={styles.clusterMixedNote}>
                {" — "}
                {members
                  .filter((m) => m.hunk.verdict !== majority)
                  .map((m) => clusterMemberLabel(m.change))
                  .join(", ")}{" "}
                overridden
              </span>
            )}
          </p>
        )}
        {/* Donna's recommendation persists through pending/decided/adopted/re-opened states
            (BUG 3: it must not vanish after "Use Donna's"). renderDonnaBlock self-guards null
            when no recommendation exists, so the box shows exactly when there is one.
            No clause-number/kind head: the clause number already shows on the row above. */}
        {renderDonnaBlock(h)}
        {editing ? (
          <InlineEditor
            seed={h.donna_counter_text ?? h.proposed_text ?? h.original_text ?? ""}
            busy={busyHere}
            onSave={(text) => saveEdit(text)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          // All five controls on one line: the four decisions + Brainstorm.
          <div className={styles.menuActions}>
            <button
              type="button"
              className={buttonStateClass("accept", rec, chosen)}
              disabled={busyHere}
              onClick={() => decideFocused("accept")}
            >
              {acceptLabel}
            </button>
            <button
              type="button"
              className={buttonStateClass("useDonna", rec, chosen)}
              disabled={busyHere || !h.donna_counter_text}
              title={h.donna_counter_text ? undefined : "Donna hasn't staged counter-language here"}
              onClick={() => decideFocused("donna")}
            >
              Use Donna&apos;s{allTag}
            </button>
            <button
              type="button"
              className={buttonStateClass("edit", rec, chosen)}
              disabled={busyHere}
              onClick={() => editFocused()}
            >
              {isCluster ? "Edit all" : "Edit"}
            </button>
            <button
              type="button"
              className={buttonStateClass("reject", rec, chosen)}
              disabled={busyHere}
              onClick={() => decideFocused("reject")}
            >
              Reject{allTag}
            </button>
            <button type="button" className={styles.btnDonna} onClick={() => openBrainstorm(c)}>
              Brainstorm with Donna ↗
            </button>
          </div>
        )}
        {isCluster && expanded && (
          <ul className={styles.clusterMembers}>
            {members.map(({ hunk: mh, change: mc }) => {
              const mrec = recButton(mh.donna_verdict);
              const mchosen = chosenButton(mh);
              const mbusy = busy === mh.id || busy === mc.id;
              return (
                <li key={mh.id} className={styles.clusterMember}>
                  <div className={styles.clusterMemberHead}>
                    <button
                      type="button"
                      className={styles.clusterMemberLabel}
                      onClick={() => scrollToChange(mc.id)}
                      title="Scroll to this clause"
                    >
                      {clusterMemberLabel(mc)}
                    </button>
                    <span className={styles.clusterMemberState} data-verdict={mh.verdict}>
                      {STORED_VERDICT_LABEL[mh.verdict]}
                    </span>
                  </div>
                  {peelEditHunk === mh.id ? (
                    <InlineEditor
                      seed={mh.donna_counter_text ?? mh.proposed_text ?? mh.original_text ?? ""}
                      busy={mbusy}
                      onSave={(text) => {
                        setPeelEditHunk(null);
                        void runHunk(mh, "edit", text);
                      }}
                      onCancel={() => setPeelEditHunk(null)}
                    />
                  ) : (
                    <div className={styles.menuActions}>
                      <button
                        type="button"
                        className={buttonStateClass("accept", mrec, mchosen)}
                        disabled={mbusy}
                        onClick={() => void runHunk(mh, "accept")}
                      >
                        Accept theirs
                      </button>
                      <button
                        type="button"
                        className={buttonStateClass("useDonna", mrec, mchosen)}
                        disabled={mbusy || !mh.donna_counter_text}
                        title={mh.donna_counter_text ? undefined : "Donna hasn't staged counter-language here"}
                        onClick={() => void runHunk(mh, "counter")}
                      >
                        Use Donna&apos;s
                      </button>
                      <button
                        type="button"
                        className={buttonStateClass("edit", mrec, mchosen)}
                        disabled={mbusy}
                        onClick={() => setPeelEditHunk(mh.id)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className={buttonStateClass("reject", mrec, mchosen)}
                        disabled={mbusy}
                        onClick={() => void runHunk(mh, "keep")}
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {actionError && (
          <p className={styles.streamError} role="alert">
            {actionError}
          </p>
        )}
      </div>
    );
  }

  // The short clause anchor for a cluster member row: its canonical (live, role-aware) number,
  // else the full anchor label (heading / category) so a non-numbered clause is still named.
  function clusterMemberLabel(change: ReviewChange): string {
    const num = change.node_id ? (canonicalNumberByNodeId.get(change.node_id) ?? null) : null;
    return num ?? anchorLabel(change);
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
      {node.clause_number ? (
        <span className={styles.cmpNum}>{node.clause_number}</span>
      ) : (
        <span className={styles.cmpCat}>{CATEGORY_LABEL[node.role]}</span>
      )}
      <span className={styles.cmpText}>{node.text ?? "(empty clause)"}</span>
    </div>
  );
});

// ---- per-button decision-state encoding (DD-83 visual system) ----------------
// Two signals on the SAME action row, legible at once: what Donna RECOMMENDS (rec) and what
// the operator actually CHOSE (chosen). The faded-purple REC marker PERSISTS on Donna's
// recommended button even after a DIFFERENT button is chosen — so "Donna said reject /
// I chose accept" shows as faded-purple Reject + solid-green Accept simultaneously.
type DecisionButton = "accept" | "useDonna" | "edit" | "reject";

// Whitespace-tolerant equality for the Donna-adopted basis (DD-82): a `modified` hunk whose
// final_text equals donna_counter_text is an adoption ("Use Donna's"), else the operator's own counter.
function sameLanguage(a: string | null, b: string | null): boolean {
  if (a == null || b == null) return false;
  return a.replace(/\s+/g, " ").trim() === b.replace(/\s+/g, " ").trim();
}

// donna_verdict -> recommended button (Edit is never the recommendation).
function recButton(donnaVerdict: string | null): DecisionButton | null {
  const dv = (donnaVerdict ?? "").trim().toLowerCase();
  return dv === "accept" ? "accept" : dv === "keep" ? "reject" : dv === "counter" ? "useDonna" : null;
}

// Committed hunk verdict -> chosen button (pending => none chosen).
function chosenButton(h: ReviewHunk): DecisionButton | null {
  switch (h.verdict) {
    case "accepted":
      return "accept";
    case "rejected":
      return "reject";
    case "modified":
      return sameLanguage(h.final_text, h.donna_counter_text) ? "useDonna" : "edit";
    default:
      return null;
  }
}

// State-class precedence (highest first): adopted/agreed (solid purple) > chosen override
// (solid green/red/slate) > Donna's persisting recommendation (faded purple) > neutral.
function buttonStateClass(
  btn: DecisionButton,
  rec: DecisionButton | null,
  chosen: DecisionButton | null,
): string {
  const isRec = btn === rec;
  const isChosen = btn === chosen;
  if (isChosen && isRec) return styles.btnAdopted;
  if (isChosen && btn === "useDonna") return styles.btnAdopted;
  if (isChosen && btn === "accept") return styles.btnChosenAccept;
  if (isChosen && btn === "reject") return styles.btnChosenReject;
  if (isChosen && btn === "edit") return styles.btnChosenEdit;
  if (isRec) return styles.btnRec;
  return styles.btnGhost;
}

// ---- grouped-stop (cluster) summary helpers (DD-89) -------------------------
type ClusterTally = { accepted: number; rejected: number; modified: number; pending: number };

function clusterTally(members: { hunk: ReviewHunk }[]): ClusterTally {
  const t: ClusterTally = { accepted: 0, rejected: 0, modified: 0, pending: 0 };
  for (const { hunk } of members) t[hunk.verdict] += 1;
  return t;
}

// The members disagree once they hold more than one distinct verdict state (a peel-off
// override, or a partial decision) — the trigger for the mixed summary.
function clusterDivergent(members: { hunk: ReviewHunk }[]): boolean {
  return new Set(members.map((m) => m.hunk.verdict)).size > 1;
}

// The dominant DECIDED verdict, or null on a tie / nothing decided — used to single out the
// minority (overridden) members by name in the mixed summary.
function clusterMajority(t: ClusterTally): ReviewHunk["verdict"] | null {
  const ranked: [ReviewHunk["verdict"], number][] = [
    ["accepted", t.accepted],
    ["rejected", t.rejected],
    ["modified", t.modified],
  ];
  ranked.sort((a, b) => b[1] - a[1]);
  if (ranked[0][1] === 0 || ranked[0][1] === ranked[1][1]) return null;
  return ranked[0][0];
}

const STORED_VERDICT_LABEL: Record<ReviewHunk["verdict"], string> = {
  accepted: "accepted",
  rejected: "rejected",
  modified: "edited",
  pending: "undecided",
};

// Compose the encoding classes for one segment. Colour comes from the type
// (ins=green / del=red / donna=purple / same=normal); the decision-state axis adds
// .tcPending (a tinted background pill) when the change is still pending. Decided segments
// drop the pill — colour + solid line only.
function segClasses(seg: DiffSeg): string {
  const cls: string[] = [];
  if (seg.type === "ins") cls.push(styles.diffIns);
  else if (seg.type === "del") cls.push(styles.diffDel);
  else if (seg.type === "donna") cls.push(styles.diffDonna);
  else if (seg.type === "kept") cls.push(styles.keptOriginal);
  else if (seg.type === "declined") cls.push(styles.declinedText);
  else cls.push(styles.diffSame);
  if (seg.pending) cls.push(styles.tcPending);
  return cls.join(" ");
}

// Verdict-aware segments for ONE hunk — shared by the edited-clause splice and the
// whole-node (added/deleted) renderer so the colour x line-style encoding is identical
// everywhere (DD-83). Redline content is always the deterministic diff text (DD-64); only
// colour/line-style are decided here, from the persisted verdict + final_text.
//   pending  -> their del (red) + their ins (green), both DOTTED.
//   accepted -> same, SOLID.
//   rejected -> DD-91 reject trace (both SOLID, scales from a fragment to a whole clause):
//               the declined counterparty NEW text (their addition / the new side of a
//               modification) renders GREY strikethrough (`declined`); the retained ORIGINAL
//               (a kept deletion / the original side of a modification) renders SOLID BLACK
//               underline (`kept`). A modification emits BOTH; an addition only `declined`; a
//               deletion only `kept`. Never silently plain — every reject leaves a trace.
//   modified -> their PROPOSED text red SOLID strikethrough (their ask) + the operator's
//               final language purple SOLID underline (our counter) — "their ask -> our
//               counter" (Use Donna's / Edit). The baseline is NOT shown: the swap reads as
//               striking THEIR proposal, not the original. Falls back to the baseline only
//               when there is no proposed text (a whole-node change with no counterparty ask).
function hunkChangeSegs(h: ReviewHunk, forcePending = false): DiffSeg[] {
  const orig = h.original_text ?? "";
  const proposed = h.proposed_text ?? "";
  const final = h.final_text ?? "";
  const segs: DiffSeg[] = [];
  // A re-opened decision (DD-83) renders as pending again until re-decided (a tinted pill),
  // even though the persisted verdict is still settled.
  const verdict = forcePending ? "pending" : h.verdict;
  switch (verdict) {
    case "pending":
      if (orig) segs.push({ type: "del", text: orig, hunkId: h.id, pending: true });
      if (proposed) segs.push({ type: "ins", text: proposed, hunkId: h.id, pending: true });
      break;
    case "accepted":
      if (orig) segs.push({ type: "del", text: orig, hunkId: h.id });
      if (proposed) segs.push({ type: "ins", text: proposed, hunkId: h.id });
      break;
    case "rejected":
      // DD-91 reject trace. The declined counterparty NEW text (an addition, or the new side
      // of a modification) renders GREY strikethrough (`declined`); the retained ORIGINAL (a
      // kept deletion, or the original side of a modification) renders SOLID BLACK underline
      // (`kept`). A modification emits BOTH. Both carry the hunkId so the span is a click
      // target. Never silently plain — a decided reject is always marked.
      if (proposed) segs.push({ type: "declined", text: proposed, hunkId: h.id });
      if (orig) segs.push({ type: "kept", text: orig, hunkId: h.id });
      break;
    case "modified": {
      const swappedOut = proposed || orig;
      if (swappedOut) segs.push({ type: "del", text: swappedOut, hunkId: h.id });
      if (final) segs.push({ type: "donna", text: final, hunkId: h.id });
      break;
    }
  }
  return segs;
}

// Splice an edited clause's hunks back into its full baseline body so the diff reads
// IN PLACE, with the surrounding sentences visible.
// Each ins/del segment now carries the id of the hunk it comes from so the renderer
// can make it a click target (selecting that hunk's inline menu). "same" segments
// never have a hunkId — they are not interactive. Adjacent same-type segments from the
// same hunk are still merged; segments from different hunks are NOT merged (they
// carry different hunkIds and are always separated by a "same" run anyway).
function inContextSegs(body: string, hunks: ReviewHunk[], reopened?: Set<string>): DiffSeg[] {
  const sorted = [...hunks].sort((a, b) => (a.position_in_body ?? 0) - (b.position_in_body ?? 0));
  const out: DiffSeg[] = [];
  const push = (seg: DiffSeg) => {
    if (!seg.text) return;
    const last = out[out.length - 1];
    if (
      last &&
      last.type === seg.type &&
      last.hunkId === seg.hunkId &&
      last.pending === seg.pending
    ) {
      last.text += seg.text;
    } else {
      out.push({ ...seg });
    }
  };
  let cursor = 0;
  for (const h of sorted) {
    const pos = h.position_in_body ?? 0;
    if (pos > cursor) push({ type: "same", text: body.slice(cursor, pos) });
    // The original substring [pos, pos+orig.length) is represented by the hunk's own
    // verdict-aware segments (del/same), never by a body slice — so advance the cursor
    // past it. Pure-insertion hunks (no original) contribute only an ins segment.
    for (const s of hunkChangeSegs(h, reopened?.has(h.id) ?? false)) push(s);
    cursor = Math.max(cursor, pos + (h.original_text ?? "").length);
  }
  if (cursor < body.length) push({ type: "same", text: body.slice(cursor) });
  return out;
}

// Inline segments for ANY change kind, rendered ONCE in the clause body (DD-83):
//   - edited:  the hunks spliced into the full baseline body (redline in place).
//   - new:     the whole added clause as one green insertion (pending) -> grey strikethrough
//              on reject (declined) -> green solid on accept.
//   - deleted: the whole removed clause as one red deletion (pending) -> black underline on
//              reject (kept original).
function changeInlineSegs(c: ReviewChange, reopened?: Set<string>): DiffSeg[] {
  if (c.change_kind === "edited") {
    const body = c.context?.baseline?.body;
    if (!body) return [];
    return inContextSegs(body, c.hunks, reopened);
  }
  const h = c.hunks[0];
  return h ? hunkChangeSegs(h, reopened?.has(h.id) ?? false) : [];
}

// Full-clause inline tracked-changes view, Word-style, for every change kind. Colour x
// line-style encoding via segClasses (DD-83). Each change span is a click target — it
// carries its source hunk id so clicking it focuses that change. "same" spans are plain
// text, not interactive.
function renderInlineTrackedClause(
  c: ReviewChange,
  selectedHunkId: string | null,
  onSelectHunk: (hunkId: string | null, rejectTrace?: boolean) => void,
  reopened?: Set<string>,
  clauseRole: Role = "clause",
) {
  const segs = changeInlineSegs(c, reopened);
  if (segs.length === 0) return null;
  const capsBold = capsBoldForRole(clauseRole);
  return (
    <div className={styles.inlineClause}>
      <p className={styles.inlineClauseBody}>
        {segs.map((seg, i) => {
          if (seg.hunkId) {
            const isSelected = selectedHunkId === seg.hunkId;
            // A reject-trace span (kept original / declined new text) FOCUSES on click but
            // must NOT re-open the change (DD-91): the dock shows its current verdict + the
            // re-decide buttons while the trace stays put. Pending/countered spans re-open.
            const rejectTrace = seg.type === "kept" || seg.type === "declined";
            return (
              <span
                key={i}
                className={[
                  segClasses(seg),
                  isSelected ? styles.fragSelected : styles.fragChange,
                ].join(" ")}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectHunk(isSelected ? null : seg.hunkId!, rejectTrace);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    onSelectHunk(isSelected ? null : seg.hunkId!, rejectTrace);
                  }
                }}
              >
                {seg.type === "same" || seg.type === "kept"
                  ? renderRich(seg.text, capsBold, styles.sBold)
                  : seg.text}
              </span>
            );
          }
          return (
            <span key={i} className={segClasses(seg)}>
              {renderRich(seg.text, capsBold, styles.sBold)}
            </span>
          );
        })}
      </p>
    </div>
  );
}
