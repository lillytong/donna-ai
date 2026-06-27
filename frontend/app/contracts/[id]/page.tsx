"use client";

import { Fragment, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import styles from "../cockpit.module.css";
import { deriveNumbers } from "../../lib/numbering";
import {
  ApiError,
  askDonna,
  brainstormTurn,
  closeBrainstorm,
  confirmRecommendation,
  createIssue,
  createNode,
  deleteNode,
  deleteSnapshotVersion,
  donnaErrorMessage,
  draftClause,
  editNode,
  exportCleanCopy,
  exportIssueList,
  exportRedline,
  generateRecommendation,
  getBrainstormSummaries,
  getContractTree,
  getDefinedTerms,
  getDonnaThread,
  getLineage,
  getRecommendation,
  getSnapshotTree,
  importRevision,
  listIssues,
  listRevisionSessions,
  markSent,
  previewDeleteSnapshot,
  searchClause,
  updateIssue,
  updateIssueStatus,
  type BrainstormTurn,
  type DefinedTerm,
  type DonnaAnswerKind,
  type DonnaChatMode,
  type LineageView,
  type LineageTimelineEntry,
  type MarkSentRecipient,
  type RevisionSource,
  type Initiator,
  type IssueStatus,
  type NodeTreeItem,
  type Role,
  type StoredBrainstormSummary,
  type StoredIssue,
  type SnapshotDeleteResponse,
  type StoredRecommendation,
  type StoredRevisionSession,
} from "../../lib/api";

// Rearrange mode is gated + lazy: @dnd-kit and the sortable tree only enter the
// client bundle the first time the operator flips "Rearrange" (DESIGN: keep the
// click-heavy navigate tree out of drag scope, and @dnd-kit off the initial load).
// `ssr: false` is valid here because this is a Client Component (next/dynamic docs:
// skipping SSR only works inside Client Components).
const RearrangeTree = dynamic(() => import("../RearrangeTree"), {
  ssr: false,
  loading: () => <div className={styles.rearrangeLoading}>Loading rearrange…</div>,
});

// A clause/region in document order, with its derived outline number (clauses
// only — DD-02/DD-54). This is the read-only spine the operator navigates.
interface FlatNode {
  id: string;
  depth: number;
  role: Role;
  text: string;
  isHeading: boolean;
  contentType: string; // structural kind source — "table" / "prose" (DD-56 labels)
  number: string; // "" for non-clause roles
}

// Depth-first walk = document order; children arrive pre-sorted by order_index.
function flatten(nodes: NodeTreeItem[]): Omit<FlatNode, "number">[] {
  const out: Omit<FlatNode, "number">[] = [];
  const walk = (n: NodeTreeItem, depth: number) => {
    const text = n.heading ?? n.body ?? n.plain_text ?? "";
    out.push({
      id: n.id,
      depth,
      role: n.role,
      text,
      isHeading: !!n.heading && !n.body,
      contentType: n.content_type,
    });
    for (const c of n.children) walk(c, depth + 1);
  };
  for (const n of nodes) walk(n, 0);
  return out;
}

// Derive clause numbers from the clause-role depth sequence (mirrors the import
// review: lib/numbering.deriveNumbers over clause depths only).
function withNumbers(flat: Omit<FlatNode, "number">[]): FlatNode[] {
  const numbers = deriveNumbers(flat.filter((f) => f.role === "clause").map((f) => f.depth));
  let ci = 0;
  return flat.map((f) => (f.role === "clause" ? { ...f, number: numbers[ci++] } : { ...f, number: "" }));
}

function titleCase(role: string): string {
  return role.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// Per-row label for a non-clause row. Back-matter `appendix` shows its KIND
// (Heading / Body / Table) rather than a flat "Appendix", mirroring the import
// review's categoryLabel — appendix_title and every other role keep titleCase
// ("Appendix title", "Signature block", front-matter roles). (DD-56)
function nonClauseLabel(r: FlatNode): string {
  if (r.role === "appendix") {
    return r.contentType === "table" ? "Table" : r.isHeading ? "Heading" : "Body";
  }
  return titleCase(r.role);
}

// A jump query is a clause-number lookup when it starts with a digit ("7.2", "1");
// anything else is a keyword query (substring search, with an AI fallback on Enter).
function isNumericQuery(q: string): boolean {
  return /^[0-9]/.test(q.trim());
}

// DD-54 regions: front-matter above, the numbered clause tree, back-matter below.
// Mirrors the import review's FRONT_MATTER / BACK_MATTER role buckets exactly
// (drafting_note, like clause, falls through to the body region).
const FRONT_MATTER: ReadonlySet<Role> = new Set<Role>([
  "title",
  "date",
  "parties",
  "recital",
  "agreement_statement",
]);
const BACK_MATTER: ReadonlySet<Role> = new Set<Role>([
  "appendix",
  "appendix_title",
  "signature_block",
]);
type Region = "front" | "body" | "back";
// Partition the flat rows into the three document-order regions (mirrors the
// import review's preamble / body / backmatter role split — see import/page.tsx),
// but POSITIONAL for drafting_note: a note ABOVE the first clause is front-matter,
// a note among/after the clauses belongs to the operative tree. Role alone can't
// tell those apart, so we split on the note's document index vs the first clause.
function partitionRegions(rows: FlatNode[]): Record<Region, FlatNode[]> {
  const firstClause = rows.findIndex((r) => r.role === "clause");
  const clauseStart = firstClause < 0 ? Infinity : firstClause;
  const front: FlatNode[] = [];
  const body: FlatNode[] = [];
  const back: FlatNode[] = [];
  rows.forEach((r, i) => {
    if (FRONT_MATTER.has(r.role)) front.push(r);
    else if (BACK_MATTER.has(r.role)) back.push(r);
    else if (r.role === "drafting_note" && i < clauseStart) front.push(r);
    else body.push(r);
  });
  return { front, body, back };
}
// Label + hint text mirror the import review's region heads verbatim.
const REGION_LABEL: Record<Region, string> = {
  front: "Preamble / front-matter",
  body: "Clauses",
  back: "Back-matter",
};
const REGION_HINT: Record<Region, string> = {
  front: "not numbered",
  body: "numbered · the operative tree",
  back: "not numbered · section / body styling",
};

// Mark-as-sent recipient labels (DD-71).
const MARK_LABEL: Record<MarkSentRecipient, string> = {
  counterparty: "Counterparty",
  legal: "Legal",
  both: "Counterparty & Legal",
};

// F27 badge colour-key, by lifecycle label. "Your move" reads amber (action owed),
// any "Sent to…" reads green (out the door), "Working copy" stays neutral, and
// "Signed" reads as a done/settled blue. Unknown labels fall back to neutral.
function badgeTone(label: string): string {
  if (label === "Your move") return styles.badgeToneMove;
  if (label === "Reviewing revision") return styles.badgeToneReview;
  if (label.startsWith("Sent to")) return styles.badgeToneSent;
  if (label === "Signed") return styles.badgeToneSigned;
  return styles.badgeToneWorking;
}

// The full badge text: label + " · v{n}" (when numbered) + " · edited since sent"
// (when the working copy drifted past the last send).
function badgeText(b: { label: string; version: number | null; marker: boolean }): string {
  let t = b.label;
  if (b.version != null) t += ` · v${b.version}`;
  if (b.marker) t += " · edited since sent";
  return t;
}

// DD-48 baseline pointers → friendly tags for the lineage rows.
function friendlyPointer(p: string): string {
  const map: Record<string, string> = {
    last_shared_with_counterparty: "Counterparty baseline",
    last_shared_with_legal: "Legal baseline",
    last_received_from_counterparty: "Latest from counterparty",
    last_received_from_legal: "Latest from legal",
    counterparty: "Counterparty baseline",
    legal: "Legal baseline",
  };
  return map[p] ?? p.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// Compact date for lineage rows / the snapshot banner.
function lineageDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// A node has children iff the next row in document order is deeper — children
// always follow their parent immediately (mirrors the import review's childrenSet).
function childIds(rows: FlatNode[]): Set<string> {
  const s = new Set<string>();
  for (let i = 0; i < rows.length - 1; i++) {
    if (rows[i + 1].depth > rows[i].depth) s.add(rows[i].id);
  }
  return s;
}

// Rows currently visible: a collapsed node hides its whole descendant run (every
// following row deeper than it, until depth returns to its level or shallower).
function visibleRows(rows: FlatNode[], collapsed: ReadonlySet<string>): FlatNode[] {
  const out: FlatNode[] = [];
  let hideDeeperThan = Infinity;
  for (const r of rows) {
    if (r.depth > hideDeeperThan) continue;
    hideDeeperThan = Infinity;
    out.push(r);
    if (collapsed.has(r.id)) hideDeeperThan = r.depth;
  }
  return out;
}

// Ancestors of a target row: the nearest preceding rows of strictly decreasing
// depth. Used so a jump expands any collapsed parent that hides the target.
function ancestorIds(rows: FlatNode[], targetId: string): string[] {
  const i = rows.findIndex((r) => r.id === targetId);
  if (i < 0) return [];
  const out: string[] = [];
  let need = rows[i].depth;
  for (let j = i - 1; j >= 0 && need > 0; j--) {
    if (rows[j].depth < need) {
      out.push(rows[j].id);
      need = rows[j].depth;
    }
  }
  return out;
}

// --- F08b insert placement (pure, over the flat document-order rows) -------
// A node's parent is the nearest preceding row of strictly smaller depth.
function parentOf(rows: FlatNode[], id: string): string | null {
  const i = rows.findIndex((r) => r.id === id);
  if (i < 0) return null;
  const d = rows[i].depth;
  for (let j = i - 1; j >= 0; j--) if (rows[j].depth < d) return rows[j].id;
  return null;
}
// Index just past a node and its whole subtree — the splice point for a node that
// lands immediately after that subtree (insert-below / append-as-last-child).
function subtreeEndIndex(rows: FlatNode[], id: string): number {
  const i = rows.findIndex((r) => r.id === id);
  if (i < 0) return rows.length;
  const d = rows[i].depth;
  let j = i + 1;
  while (j < rows.length && rows[j].depth > d) j++;
  return j;
}
// How many descendants a node has (its whole subtree minus itself) — drives the
// delete confirm's "and its N sub-clause(s)" copy.
function descendantCount(rows: FlatNode[], id: string): number {
  const i = rows.findIndex((r) => r.id === id);
  if (i < 0) return 0;
  return subtreeEndIndex(rows, id) - i - 1;
}

// The first row after a node's subtree (its structural boundary), or null at end.
function firstAfterSubtreeId(rows: FlatNode[], id: string): string | null {
  const j = subtreeEndIndex(rows, id);
  return j < rows.length ? rows[j].id : null;
}

// --- F05 defined-term hover-to-define (pure helpers) ------------------------
// An index over the deal's defined terms: one case-insensitive, whole-word
// regex pattern (longest term first, so "Confidential Information" wins over a
// bare "Information") plus a lowercased term → entry lookup. `pattern` is null
// for an empty registry — callers then render clause text untouched.
interface TermEntry {
  term: string;
  definition: string | null;
}
interface TermIndex {
  pattern: string | null;
  lookup: Map<string, TermEntry>;
}
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function buildTermIndex(terms: DefinedTerm[]): TermIndex {
  const lookup = new Map<string, TermEntry>();
  for (const t of terms) {
    const key = t.term.trim().toLowerCase();
    if (key && !lookup.has(key)) lookup.set(key, { term: t.term.trim(), definition: t.definition });
  }
  if (lookup.size === 0) return { pattern: null, lookup };
  // Alternation is tried left-to-right, so longer phrases must precede the
  // shorter terms they contain — that's what gives longest-match-first.
  const alts = [...lookup.values()]
    .map((e) => e.term)
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp);
  return { pattern: `\\b(?:${alts.join("|")})\\b`, lookup };
}

// Split a row's text into plain runs and matched defined-term tokens, in order.
// A fresh RegExp per call keeps the global lastIndex local (no state shared
// across rows); the matched span resolves to its entry via the lowercased key.
type TextToken =
  | { kind: "text"; value: string }
  | { kind: "term"; value: string; entry: TermEntry };
function tokenizeText(text: string, index: TermIndex): TextToken[] {
  if (!index.pattern) return [{ kind: "text", value: text }];
  const re = new RegExp(index.pattern, "gi");
  const out: TextToken[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const entry = index.lookup.get(m[0].toLowerCase());
    if (!entry) continue;
    if (m.index > last) out.push({ kind: "text", value: text.slice(last, m.index) });
    out.push({ kind: "term", value: m[0], entry });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", value: text.slice(last) });
  return out;
}

// Deterministic, no-LLM issue title (DD-59): the first non-empty line of the
// operator's description, trimmed and capped at ~80 chars with a trailing
// ellipsis when longer. `title` is NOT NULL, so callers pass a non-empty
// description and always get a non-empty string back.
function deriveTitle(text: string): string {
  const firstLine = text.split("\n").map((l) => l.trim()).find((l) => l.length > 0) ?? "";
  return firstLine.length > 80 ? `${firstLine.slice(0, 80).trimEnd()}…` : firstLine;
}

// Issue lifecycle (F07, DD-65). Binary status: open | closed. Order drives the
// segmented toggle; labels are operator-facing.
const STATUS_ORDER: IssueStatus[] = ["open", "closed"];
const STATUS_LABEL: Record<IssueStatus, string> = {
  open: "Open",
  closed: "Closed",
};
const STATUS_CLASS: Record<IssueStatus, string> = {
  open: "statusOpen",
  closed: "statusClosed",
};
function asStatus(s: string): IssueStatus {
  return s === "closed" ? "closed" : "open";
}

function shortTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// --- F10 Donna tab: chat model + citation resolution ------------------------
// Example prompts that seed the empty thread — one of each question shape (status
// / explain / locate) so the operator sees what Donna can do.
const DONNA_EXAMPLES = [
  "What's still open?",
  "What does clause 12 say about termination?",
  "Where's the liability cap?",
];
// Staged labels for the answer wait — motion + a sense of where Donna is, never a
// frozen word (frontend-design loading rule); cycled while a question is in flight.
const DONNA_PHASES = [
  "Donna's reading the contract…",
  "Finding the clauses you mean…",
  "Checking what's still open…",
];

// A resolved citation chip. A clause chip jumps to + flashes its node in the tree
// (reusing the cockpit jump); an issue chip is non-jumping (the ledger has no tree
// row of its own).
type ChatCitation =
  | { kind: "clause"; nodeId: string; label: string }
  | { kind: "issue"; label: string };

// One rendered chat turn. A FRESH ask carries `mode` (F10b context-aware
// treatment) + optional `draftLanguage` + `citations`. A RELOADED thread turn
// carries `kind` + `citations` (F10 persistence) and no `mode`. The bubble
// treatment derives from whichever is present (mode wins; see donnaBubbleClass).
interface DonnaUiMessage {
  role: "user" | "donna";
  content: string;
  kind?: DonnaAnswerKind;
  mode?: DonnaChatMode;
  draftLanguage?: string | null;
  citations?: ChatCitation[];
}

// Map a turn to its bubble treatment. Live `mode` wins; otherwise fall back to the
// persisted `kind`. legal_referral reuses the amber deflected wash; need_context is
// its own gentle (non-error) hint; every advisory/explain mode reads as a normal
// grounded answer (the draft block + tags are layered on separately).
function donnaBubbleClass(m: DonnaUiMessage): string {
  if (m.mode) {
    if (m.mode === "legal_referral") return styles.bubbleDeflected;
    if (m.mode === "need_context") return styles.bubbleNeedContext;
    return styles.bubbleDonna;
  }
  if (m.kind === "deflected") return styles.bubbleDeflected;
  if (m.kind === "not_found") return styles.bubbleNotFound;
  return styles.bubbleDonna;
}

// F11 resolution-card state, scoped to the issue being resolved. `status` drives the
// card: loading (generating/fetching), ready (a draft to show), or error. `acting` is
// the in-flight action ("use" = confirm, "refresh" = regenerate). `editing` reveals the
// edit buffers (editPos/editLang) so the operator can adjust before [Use]. Held against
// `issueId` so a late async never paints onto a different issue's view.
interface RecState {
  issueId: string;
  status: "loading" | "ready" | "error";
  draft: StoredRecommendation | null;
  error?: string;
  acting?: "use" | "refresh" | null;
  editing?: boolean;
  editPos: string;
  editLang: string;
}

// A draft is "usable" (worth a [Use]/[Edit]) only when it carries language to apply —
// the honest could-not-ground fallback has neither field and offers no action.
function recHasLanguage(d: StoredRecommendation): boolean {
  return !!(d.draft_recommended_position?.trim() || d.draft_counter_language?.trim());
}

// Resolve backend citation ids to chips. A node id → a jumping clause chip labeled
// with its derived number/heading. An issue id → its anchored clause chip when the
// issue has a node, else a muted non-jumping "Issue" chip. Unknown ids are dropped
// (never a dead chip); deduped by id.
function resolveCitations(
  ids: string[],
  rowById: Map<string, FlatNode>,
  issueById: Map<string, StoredIssue>,
): ChatCitation[] {
  const clauseLabel = (r: FlatNode): string => (r.number ? `§${r.number}` : titleCase(r.role));
  const out: ChatCitation[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    const row = rowById.get(id);
    if (row) {
      out.push({ kind: "clause", nodeId: id, label: clauseLabel(row) });
      continue;
    }
    const issue = issueById.get(id);
    if (issue) {
      const anchor = issue.node_id ? rowById.get(issue.node_id) ?? null : null;
      if (anchor) out.push({ kind: "clause", nodeId: anchor.id, label: clauseLabel(anchor) });
      else out.push({ kind: "issue", label: "Issue" });
    }
  }
  return out;
}

// --- F10 Donna answer markdown (small, dependency-free, safe) ----------------
// Donna's answers are simple, controlled markdown — **bold**, *italic*, ordered
// (`1.`) and bulleted (`- ` / `* `) lists, and paragraphs. We hand-roll a tiny
// renderer rather than pull a markdown dep: every span flows through React
// elements (never dangerouslySetInnerHTML), so React auto-escapes the text — no
// injection surface. Applied only to Donna's answer text; operator messages,
// citations, and the deflect footer stay plain.
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  // Bold (**…**) is tried before italic (*…*) at each position, so "**x**" reads
  // as one bold run, not two empty italics. Inner runs exclude "*" to stay tight.
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*/g;
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={`${keyPrefix}-b${k}`}>{m[1]}</strong>);
    else out.push(<em key={`${keyPrefix}-i${k}`}>{m[2]}</em>);
    last = m.index + m[0].length;
    k++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderDonnaMarkdown(text: string): React.ReactNode[] {
  const lines = text.split("\n");
  const orderedRe = /^\s*\d+\.\s+/;
  const bulletRe = /^\s*[-*]\s+/;
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    if (lines[i].trim() === "") {
      i++;
      continue;
    }
    if (orderedRe.test(lines[i]) || bulletRe.test(lines[i])) {
      const ordered = orderedRe.test(lines[i]);
      const rowRe = ordered ? orderedRe : bulletRe;
      const items: React.ReactNode[] = [];
      let li = 0;
      while (i < lines.length && rowRe.test(lines[i])) {
        const content = lines[i].replace(rowRe, "");
        items.push(<li key={`${key}-li${li}`}>{renderInline(content, `${key}-li${li}`)}</li>);
        i++;
        li++;
      }
      blocks.push(
        ordered ? (
          <ol key={key} className={styles.mdList}>
            {items}
          </ol>
        ) : (
          <ul key={key} className={styles.mdList}>
            {items}
          </ul>
        ),
      );
      key++;
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !orderedRe.test(lines[i]) &&
      !bulletRe.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    const paraNodes: React.ReactNode[] = [];
    para.forEach((p, idx) => {
      if (idx > 0) paraNodes.push(<br key={`${key}-br${idx}`} />);
      for (const node of renderInline(p, `${key}-p${idx}`)) paraNodes.push(node);
    });
    blocks.push(
      <p key={key} className={styles.mdP}>
        {paraNodes}
      </p>,
    );
    key++;
  }
  return blocks;
}

// Fetch stages so the wait shows motion + a sense of progress, never a frozen word.
type LoadState =
  | { kind: "loading"; phase: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; rows: FlatNode[]; issues: StoredIssue[]; terms: DefinedTerm[] };

// F08 inline edit: one node's text becomes an in-place textarea (only one at a time).
interface EditState {
  nodeId: string;
  draft: string;
  saving: boolean;
  error: string | null;
}
// F08b insert: an empty editor slotted at the target position. `parentId` +
// exactly one of `afterNodeId` / `beforeNodeId` drive the create call (mutually
// exclusive); `subtreeAnchorId`/`depth` drive the local splice; `beforeId` is the
// visible body row the editor renders ahead of (null = append at the body
// region's end). `mode` only varies the label + depth.
interface InsertState {
  mode: "below" | "sub" | "above";
  parentId: string | null;
  afterNodeId: string | null;
  beforeNodeId: string | null;
  subtreeAnchorId: string;
  depth: number;
  beforeId: string | null;
  draft: string;
  saving: boolean;
  error: string | null;
  // F08d "Draft with Donna": the anchor the draft grounds on (the clause the insert is
  // relative to), the description box, and its async state. `donnaOpen` toggles the panel.
  anchorNodeId: string | null;
  donnaOpen: boolean;
  donnaDesc: string;
  donnaBusy: boolean;
  donnaError: string | null;
}

// F08c delete: a per-clause confirm before the destructive call. `descendantCount`
// is the number of sub-clauses that go with it (computed from the local subtree),
// surfaced in the confirm copy so the operator knows the blast radius.
interface DeleteState {
  nodeId: string;
  descendantCount: number;
  deleting: boolean;
  error: string | null;
}

// Jump-bar AI fallback states: idle (no call), searching (in flight), conceptual
// (landed on a non-literal match), none (AI found nothing), error (call failed).
type AiSearchState =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "conceptual"; query: string }
  | { kind: "none"; query: string }
  | { kind: "error"; message: string };

// Inline icons for the selected-clause card — monochrome strokes that inherit
// currentColor, matching the cockpit's glyph vocabulary (no icon library).
const EditIcon = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M11.4 2.6a1.4 1.4 0 0 1 2 2L5.6 12.4l-2.7.7.7-2.7z" />
    <path d="M10 4l2 2" />
  </svg>
);
const DeleteIcon = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 4.5h10" />
    <path d="M6.5 4.5V3h3v1.5" />
    <path d="M4.6 4.5l.5 8.4a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.5-8.4" />
    <path d="M6.7 7v4M9.3 7v4" />
  </svg>
);

export default function Cockpit({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [state, setState] = useState<LoadState>({ kind: "loading", phase: "Loading contract" });
  const [reloadKey, setReloadKey] = useState(0);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [jumpVal, setJumpVal] = useState("");
  // Which keyword match the ‹ › stepper is on (index into keywordMatches).
  const [matchIdx, setMatchIdx] = useState(0);
  // Conceptual-search (AI fallback) lifecycle for the jump bar — only fires on
  // Enter when a keyword query has zero literal matches (DD-jump).
  const [aiSearch, setAiSearch] = useState<AiSearchState>({ kind: "idle" });
  const [flashId, setFlashId] = useState<string | null>(null);
  // F05 hover-to-define: the term whose definition popover is showing, anchored
  // to the hovered span's viewport rect (fixed-positioned so it escapes the
  // tree's single-line text clip). Null = nothing hovered.
  const [termPopover, setTermPopover] = useState<{
    entry: TermEntry;
    top: number;
    left: number;
  } | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // Whole-region collapse (front / body / back), independent of the per-node
  // `collapsed` above — mirrors the import review's collapsedRegions (DD-54). A
  // collapsed region hides every one of its rows, leaving only its header label.
  const [collapsedRegions, setCollapsedRegions] = useState<Set<Region>>(new Set());

  // F08 / F08b: per-clause actions menu + the single active inline editor. Edit
  // and insert are mutually exclusive (starting one clears the other).
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditState | null>(null);
  const [inserting, setInserting] = useState<InsertState | null>(null);
  const [deleteState, setDeleteState] = useState<DeleteState | null>(null);
  // Rearrange (drag-and-drop) mode: when true the read tree is swapped for the
  // lazily-loaded sortable RearrangeTree. Reorder/reparent replaces the old ⋮
  // Move up/down items.
  const [rearranging, setRearranging] = useState(false);
  const menuRef = useRef<HTMLSpanElement | null>(null);

  // Export ▾ menu (SPEC §9, DD-71). Every item is a pure grab — clean copy, redline,
  // and issue list all just download (no snapshot/pointer). `busy` is the action in
  // flight, driving the brief "Exporting…" tag.
  const [exportOpen, setExportOpen] = useState(false);
  const [exportBusy, setExportBusy] = useState<"clean" | "issues" | "redline" | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  // Redline has no baseline until the first Mark as sent (DD-71); the backend signals
  // that with a 409, which we turn into the friendly inline hint instead of a raw error.
  const [redlineNoBaseline, setRedlineNoBaseline] = useState(false);
  const exportRef = useRef<HTMLDivElement | null>(null);

  // Mark as sent (DD-71): the boundary event, separate from export. Records that the
  // exported .docx went out — cuts the baseline snapshot + advances the DD-48
  // pointer(s). `markDrift` holds the non-blocking DD-72 "edited since last export"
  // warning (Mark anyway / Re-export); `markDone` is the transient confirmation.
  const [markOpen, setMarkOpen] = useState(false);
  const [markBusy, setMarkBusy] = useState<MarkSentRecipient | null>(null);
  const [markError, setMarkError] = useState<string | null>(null);
  const [markDrift, setMarkDrift] = useState<{
    recipient: MarkSentRecipient;
    version: number;
    lastExportAt: string | null;
  } | null>(null);
  const [markDone, setMarkDone] = useState<{ recipient: MarkSentRecipient; version: number } | null>(
    null,
  );
  const markRef = useRef<HTMLDivElement | null>(null);

  // Import revision (F03b, Mode B entry): an upload + source picker beside Mark as
  // sent. Picking a source arms the hidden file input; a chosen .docx is imported
  // against the last-sent baseline and routes into the F03c review surface.
  // `importSource` is the armed source while the file dialog is open; `importBusy`
  // drives the inline progress; `importError` surfaces the typed 422/409 messages.
  const router = useRouter();
  const [importOpen, setImportOpen] = useState(false);
  const [importSource, setImportSource] = useState<RevisionSource | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const importRef = useRef<HTMLDivElement | null>(null);
  const revFileRef = useRef<HTMLInputElement | null>(null);

  // F27 lifecycle badge + lineage drawer. `lineage` is the full version/snapshot
  // view (badge + timeline + working copy + reserved slots), loaded on mount and
  // refreshed after a Mark-as-sent. `lineageOpen` toggles the in-place drawer the
  // header badge opens. `snapshotView` (when set) swaps the live tree for a past
  // snapshot rendered read-only, behind a banner — null = the live working copy.
  const [lineage, setLineage] = useState<LineageView | null>(null);
  // F03c: the open ('reviewing') revision session, if any — drives the persistent
  // Resume affordance. Backed out of the review page, this is the only way back in.
  const [openRevision, setOpenRevision] = useState<StoredRevisionSession | null>(null);
  const [lineageOpen, setLineageOpen] = useState(false);
  const [snapshotView, setSnapshotView] = useState<{
    snapshotId: string;
    version: number;
    direction: string;
    party: string;
    date: string;
    loading: boolean;
    error: string | null;
    rows: FlatNode[];
  } | null>(null);
  const lineageRef = useRef<HTMLDivElement | null>(null);
  // DD-85/DD-87 version delete. `deletePreviewFor` = snapshot id whose preview is in
  // flight (trash clicked, awaiting the no-mutation preview). `deleteVersion` (when
  // set) holds the loaded preview → renders the confirm dialog. `deleteBusy` = the
  // confirm/execute call in flight. `deleteError` is shared: shown in the dialog when
  // a confirm fails, and drawer-level when a preview fails (mutually exclusive).
  const [deletePreviewFor, setDeletePreviewFor] = useState<string | null>(null);
  const [deleteVersion, setDeleteVersion] = useState<{
    snapshotId: string;
    version: number;
    preview: SnapshotDeleteResponse;
  } | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Issue status write in flight (DD-65 toggle), keyed by issue id.
  const [statusBusyId, setStatusBusyId] = useState<string | null>(null);
  // Inline description edit (DD-67) in the resolution view: keyed by issue id.
  // Drafts are prefilled from the issue on enter.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editOur, setEditOur] = useState("");
  const [editTheir, setEditTheir] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // The clause-scoped raise form (Current Clause tab) — node_id = the selected clause.
  const [description, setDescription] = useState("");
  const [initiator, setInitiator] = useState<Initiator>("operator");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // The contract-level (free-floating) raise form lives on the Issues tab (DD-66.3),
  // kept on its own state so the two forms never share a draft. node_id = null.
  const [freeFormOpen, setFreeFormOpen] = useState(false);
  const [freeDescription, setFreeDescription] = useState("");
  const [freeInitiator, setFreeInitiator] = useState<Initiator>("operator");
  const [freeSubmitting, setFreeSubmitting] = useState(false);
  const [freeError, setFreeError] = useState<string | null>(null);
  // Closed issues stay accessible but collapsed out of the working view (DD-65.4).
  const [showClosed, setShowClosed] = useState(false);

  // DD-68 single-issue resolution view — a master-detail drill-in over the rail.
  // When `resolvingId` is set the rail shows the resolution view instead of a tab;
  // `resolveOrigin` is the surface the back arrow returns to (the issue list, or
  // the Current Clause tab). The clause context is compact by default.
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [resolveOrigin, setResolveOrigin] = useState<"issues" | "clause">("issues");
  const [clauseCtxOpen, setClauseCtxOpen] = useState(false);
  // F11 Donna recommendation for the issue in the resolution view. Generated/fetched
  // on drill-in (auto-on-first-open); `resolvingIdRef` lets the async generate guard
  // against painting onto an issue the operator has since navigated away from.
  const [rec, setRec] = useState<RecState | null>(null);
  const resolvingIdRef = useRef<string | null>(null);

  // F10 Donna tab. The rail cycles Issues | Current Clause (DD-66) | Donna without
  // unmounting the tree. `donnaMessages` is null until the thread is first loaded
  // (lazy on first open); the load runs once and stops on error.
  const [railTab, setRailTab] = useState<"clause" | "issues" | "donna">("issues");
  const [donnaMessages, setDonnaMessages] = useState<DonnaUiMessage[] | null>(null);
  const [donnaLoading, setDonnaLoading] = useState(false);
  const [donnaError, setDonnaError] = useState<string | null>(null);
  const [donnaInput, setDonnaInput] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [phaseIdx, setPhaseIdx] = useState(0);
  // F10b context-aware chat. `donnaContext` is the active clause/issue Donna grounds
  // on; null = open read-and-explain. Sent with every ask (node_ids + issue_id).
  const [donnaContext, setDonnaContext] = useState<{ nodeIds: string[]; issueId: string | null } | null>(null);
  // A draft turn's "Use this language" routes through an existing confirmed apply
  // surface (DD-68 rec-edit confirm). When an issue is in context we open its
  // resolution view; this holds the language to drop into the rec edit buffer once
  // that view's recommendation finishes loading (see the effect below).
  const [pendingDraftLang, setPendingDraftLang] = useState<string | null>(null);
  const [copiedDraft, setCopiedDraft] = useState(false);
  // F10b draft-apply error surface: a visible note when the issue's recommendation can't be
  // loaded to apply drafted language INTO (otherwise the apply would silently no-op).
  const [draftApplyError, setDraftApplyError] = useState<string | null>(null);
  const donnaScrollRef = useRef<HTMLDivElement | null>(null);
  const donnaInputRef = useRef<HTMLInputElement | null>(null);

  // F10b / DD-73 / DD-77: the Brainstorm overlay is a STATELESS EPHEMERAL surface.
  // The transcript lives only here in React state — never persisted, never the Donna
  // thread, never localStorage. `brainstorm` is null when closed; opening seeds the
  // first assistant turn client-side from the current recommendation. On close the
  // transcript is distilled into a stored summary and DISCARDED.
  const [brainstorm, setBrainstorm] = useState<{
    issueId: string;
    transcript: DonnaUiMessage[];
  } | null>(null);
  const [brainstormInput, setBrainstormInput] = useState("");
  const [brainstormBusy, setBrainstormBusy] = useState(false);
  const [brainstormError, setBrainstormError] = useState<string | null>(null);
  const [brainstormClosing, setBrainstormClosing] = useState(false);
  // A transient note after close: a saved summary, or a quiet "nothing to save".
  const [brainstormNotice, setBrainstormNotice] = useState<
    { kind: "saved"; summary: StoredBrainstormSummary } | { kind: "empty" } | null
  >(null);
  // Stored brainstorm history per issue (DD-77), loaded for the resolving issue.
  const [brainstormSummaries, setBrainstormSummaries] = useState<
    Record<string, StoredBrainstormSummary[]>
  >({});
  const brainstormScrollRef = useRef<HTMLDivElement | null>(null);
  const brainstormInputRef = useRef<HTMLInputElement | null>(null);

  // Refresh an issue's stored brainstorm history (DD-77). Non-critical — a failure leaves the
  // prior list untouched rather than surfacing an error.
  const refreshBrainstormSummaries = useCallback(async (issueId: string) => {
    try {
      const res = await getBrainstormSummaries(issueId);
      setBrainstormSummaries((m) => ({ ...m, [issueId]: res.summaries }));
    } catch {
      // history is a continuity convenience, not load-bearing — leave it as-is
    }
  }, []);

  const rowRefs = useRef(new Map<string, HTMLElement>());
  const jumpRef = useRef<HTMLInputElement>(null);
  const flashTimer = useRef<number | null>(null);

  useEffect(() => {
    let live = true;
    setState({ kind: "loading", phase: "Loading contract" });
    (async () => {
      try {
        const tree = await getContractTree(id);
        if (!live) return;
        setState({ kind: "loading", phase: "Capturing issues" });
        const issues = await listIssues(id);
        if (!live) return;
        // Defined terms are an enrichment, not a load gate: extraction may not
        // have run yet (auto-on-import is wired separately), so a failure or an
        // empty registry just means the tree renders without term affordances.
        let terms: DefinedTerm[] = [];
        try {
          terms = (await getDefinedTerms(id)).terms;
        } catch {
          terms = [];
        }
        if (!live) return;
        const rows = withNumbers(flatten(tree.nodes));
        setState({ kind: "ready", rows, issues, terms });
      } catch (e) {
        if (live) setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
      }
    })();
    return () => {
      live = false;
    };
  }, [id, reloadKey]);

  // F27: load the lifecycle badge + lineage. An enrichment, not a load gate — a
  // failure just leaves the badge absent (the tree still renders). Re-run on
  // reloadKey so a Mark-as-sent (which bumps it) refreshes the badge + timeline.
  const loadLineage = async () => {
    try {
      setLineage(await getLineage(id));
    } catch {
      setLineage(null);
    }
  };
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const lv = await getLineage(id);
        if (live) setLineage(lv);
      } catch {
        if (live) setLineage(null);
      }
    })();
    return () => {
      live = false;
    };
  }, [id, reloadKey]);

  // F03c: detect an open revision review so the cockpit can offer Resume. An
  // enrichment, not a load gate — a failure just leaves the affordance hidden.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const sessions = await listRevisionSessions(id);
        const open = sessions.find((s) => s.status === "reviewing") ?? null;
        if (live) setOpenRevision(open);
      } catch {
        if (live) setOpenRevision(null);
      }
    })();
    return () => {
      live = false;
    };
  }, [id, reloadKey]);

  const ready = state.kind === "ready" ? state : null;
  const rows = ready?.rows ?? [];
  const issues = ready?.issues ?? [];
  const terms = ready?.terms ?? [];

  // Lookups: clause number → id (jump), id → row (anchor labels + tree badges).
  const clauseByNumber = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of rows) if (r.number && !m.has(r.number)) m.set(r.number, r.id);
    return m;
  }, [rows]);
  const rowById = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  // Defined-term match index (one regex + lookup), rebuilt only when the term
  // registry changes — empty registry yields a null pattern (no marking).
  const termIndex = useMemo(() => buildTermIndex(terms), [terms]);
  const issuesByNode = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of issues) if (i.node_id) m.set(i.node_id, (m.get(i.node_id) ?? 0) + 1);
    return m;
  }, [issues]);
  const sortedIssues = useMemo(
    () => [...issues].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [issues],
  );
  // Stable per-contract issue numbers (#1, #2 …) by creation order — independent of the
  // display sort and of open/closed, so each issue keeps a fixed handle even when two
  // share a title (the operator can say "issue #3").
  const issueNumberById = useMemo(() => {
    const m = new Map<string, number>();
    [...issues]
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .forEach((i, idx) => m.set(i.id, idx + 1));
    return m;
  }, [issues]);
  // The Issues tab works the OPEN list; closed issues collapse into a footer (DD-65.4).
  const openIssues = useMemo(
    () => sortedIssues.filter((i) => asStatus(i.status) === "open"),
    [sortedIssues],
  );
  const closedIssues = useMemo(
    () => sortedIssues.filter((i) => asStatus(i.status) === "closed"),
    [sortedIssues],
  );
  // Current Clause tab: the open issues already contested on the selected clause (DD-66.2c).
  const clauseOpenIssues = useMemo(
    () => openIssues.filter((i) => i.node_id === selectedId),
    [openIssues, selectedId],
  );
  // Glanceable badge on the Issues tab: how many issues are still open (unresolved).
  // The list shows every tracked issue regardless of status; the badge counts only
  // the ones still needing action, so it stays a "what's left" signal during a call.
  const openIssueCount = useMemo(
    () => issues.filter((i) => asStatus(i.status) === "open").length,
    [issues],
  );
  // id → issue, so a Donna citation that points at an issue (not a node) resolves
  // to that issue's anchored clause chip.
  const issueById = useMemo(() => new Map(issues.map((i) => [i.id, i])), [issues]);
  // F10b context chip label: "Issue #3 · §3.4.12" (issue + its clause), "§3.4.12"
  // (clause only), or "this contract" as a fallback. Null when no context is active.
  const donnaContextLabel = useMemo(() => {
    if (!donnaContext) return null;
    const parts: string[] = [];
    if (donnaContext.issueId) parts.push(`Issue #${issueNumberById.get(donnaContext.issueId) ?? "—"}`);
    const nodeId = donnaContext.nodeIds[0];
    const row = nodeId ? rowById.get(nodeId) : null;
    if (row) parts.push(row.number ? `§${row.number}` : titleCase(row.role));
    return parts.join(" · ") || "this contract";
  }, [donnaContext, issueNumberById, rowById]);
  // Three document-order region partitions (FIX 2: positional drafting_note).
  const { front, body, back } = useMemo(() => partitionRegions(rows), [rows]);
  // Per-node children + visibility are computed PER REGION (mirrors the import
  // review's childrenSet(body) / regionVisible(body, collapsed)); the union of
  // region parents drives the row twirl + collapse-all.
  const allParents = useMemo(
    () => new Set<string>([...childIds(front), ...childIds(body), ...childIds(back)]),
    [front, body, back],
  );
  const visibleFront = useMemo(() => visibleRows(front, collapsed), [front, collapsed]);
  const visibleBody = useMemo(() => visibleRows(body, collapsed), [body, collapsed]);
  const visibleBack = useMemo(() => visibleRows(back, collapsed), [back, collapsed]);
  // The regions that actually render (≥1 row), keyed as their headers — so a
  // collapsed region still shows its label (mirrors import's presentRegions).
  const presentRegions = useMemo(() => {
    const s = new Set<Region>();
    if (front.length) s.add("front");
    if (body.length) s.add("body");
    if (back.length) s.add("back");
    return s;
  }, [front, body, back]);
  // node id → its region, so a jump can un-collapse the target's region.
  const regionByNode = useMemo(() => {
    const m = new Map<string, Region>();
    for (const r of front) m.set(r.id, "front");
    for (const r of body) m.set(r.id, "body");
    for (const r of back) m.set(r.id, "back");
    return m;
  }, [front, body, back]);

  // Keyword jump: every row whose text contains the query (case-insensitive), in
  // document order. Empty for a numeric (clause-number) query — that path is the
  // existing number jump. Drives the live first-match jump + the ‹ › stepper.
  const keywordMatches = useMemo(() => {
    const q = jumpVal.trim();
    if (!q || isNumericQuery(q)) return [];
    const lower = q.toLowerCase();
    return rows.filter((r) => r.text.toLowerCase().includes(lower)).map((r) => r.id);
  }, [jumpVal, rows]);

  // Collapse-all spans BOTH axes now: every region header AND every parent node.
  // "All collapsed" iff every present region is collapsed AND every parent id is
  // in the node set — drives the header toggle label (mirrors the import review's
  // everythingExpanded, inverted).
  const allCollapsed =
    (allParents.size > 0 || [...presentRegions].some((k) => k !== "body")) &&
    [...allParents].every((pid) => collapsed.has(pid)) &&
    [...presentRegions].every((k) => k === "body" || collapsedRegions.has(k));

  function flashRow(nodeId: string) {
    const el = rowRefs.current.get(nodeId);
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "auto" });
    setFlashId(nodeId);
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashId((f) => (f === nodeId ? null : f)), 1200);
  }

  function jumpTo(nodeId: string) {
    setSelectedId(nodeId);
    // Expand any collapsed ancestor so the target is mounted, then scroll on the
    // next frame — the row's ref only exists after React commits the expanded tree.
    const ancestors = ancestorIds(rows, nodeId);
    setCollapsed((c) => {
      if (ancestors.every((a) => !c.has(a))) return c;
      const n = new Set(c);
      for (const a of ancestors) n.delete(a);
      return n;
    });
    // Also un-collapse the target's REGION — a jump/arrow into a collapsed
    // Preamble or Back-matter would otherwise land on a row that isn't rendered.
    const region = regionByNode.get(nodeId);
    if (region) {
      setCollapsedRegions((c) => {
        if (!c.has(region)) return c;
        const n = new Set(c);
        n.delete(region);
        return n;
      });
    }
    requestAnimationFrame(() => flashRow(nodeId));
  }

  // A jump that originates from a navigation INSTRUMENT — the clause tree row, the
  // § command bar, or an issue card (DD-66.4) — lands on the clause AND opens the
  // Current Clause tab, so its clause text, scoped raise form, and open issues are
  // ready. Donna citations call jumpTo directly to stay on their current surface.
  function jumpAndCapture(nodeId: string) {
    setRailTab("clause");
    jumpTo(nodeId);
  }

  function openDonna() {
    setRailTab("donna");
    requestAnimationFrame(() => donnaInputRef.current?.focus());
  }

  // DD-68: drill into an issue's resolution view from either door (the Issues list
  // or the Current Clause tab). Selecting + flashing the issue's clause keeps it in
  // context (reuses the tree jump/flash); the rail switches to the resolution view
  // without touching the underlying tab, so Back returns to where the drill started.
  function openResolve(issueId: string, origin: "issues" | "clause", nodeId: string | null) {
    setResolveOrigin(origin);
    setResolvingId(issueId);
    resolvingIdRef.current = issueId;
    setClauseCtxOpen(false);
    setEditingId(null);
    if (nodeId) jumpTo(nodeId);
    void loadRecommendation(issueId);
  }
  function closeResolve() {
    setResolvingId(null);
    resolvingIdRef.current = null;
    setEditingId(null);
    setRec(null);
    setRailTab(resolveOrigin);
  }

  // F11 auto-on-first-open: show the persisted draft if one exists, else generate one
  // (the only LLM call — subsequent opens reuse the stored draft). `regenerate` forces a
  // fresh draft ([↻ Refresh]). Every setRec is guarded on the issue still being open, so
  // a slow generate can't paint onto an issue the operator has navigated away from.
  async function loadRecommendation(issueId: string, regenerate = false) {
    setRec({ issueId, status: "loading", draft: null, editPos: "", editLang: "" });
    try {
      let draft = regenerate ? null : await getRecommendation(id, issueId);
      if (draft === null) draft = await generateRecommendation(id, issueId);
      if (resolvingIdRef.current !== issueId) return;
      setRec({ issueId, status: "ready", draft, editPos: "", editLang: "" });
    } catch (e) {
      if (resolvingIdRef.current !== issueId) return;
      setRec({
        issueId,
        status: "error",
        draft: null,
        error: donnaErrorMessage(e),
        editPos: "",
        editLang: "",
      });
    }
  }

  // [↻ Refresh]: regenerate, but keep the current card visible with a refresh spinner
  // rather than flashing the loading state.
  async function refreshRecommendation(issueId: string) {
    setRec((s) => (s && s.issueId === issueId ? { ...s, acting: "refresh", editing: false } : s));
    try {
      const draft = await generateRecommendation(id, issueId);
      if (resolvingIdRef.current !== issueId) return;
      setRec({ issueId, status: "ready", draft, editPos: "", editLang: "" });
    } catch (e) {
      if (resolvingIdRef.current !== issueId) return;
      setRec((s) =>
        s && s.issueId === issueId ? { ...s, acting: null, error: donnaErrorMessage(e) } : s,
      );
    }
  }

  // Open the inline edit buffers seeded from the current draft language.
  function startRecEdit(d: StoredRecommendation) {
    setRec((s) =>
      s
        ? {
            ...s,
            editing: true,
            error: undefined,
            editPos: d.draft_recommended_position ?? "",
            editLang: d.draft_counter_language ?? "",
          }
        : s,
    );
  }
  function cancelRecEdit() {
    setRec((s) => (s ? { ...s, editing: false, error: undefined } : s));
  }

  // [Use Donna's language] / [Use this language]: confirm the draft into the issue's
  // exported fields (DD-68). When `edited` is true the operator's edit buffers are sent
  // (DD-68 edited-confirm addendum); otherwise the stored draft is copied verbatim. On
  // success the draft reflects what was confirmed and flips `confirmed`.
  async function useRecommendation(issueId: string, edited: boolean) {
    setRec((s) => (s && s.issueId === issueId ? { ...s, acting: "use", error: undefined } : s));
    try {
      const editPayload = edited
        ? { edited_recommended_position: rec?.editPos ?? "", edited_counter_language: rec?.editLang ?? "" }
        : undefined;
      const result = await confirmRecommendation(id, issueId, editPayload);
      if (resolvingIdRef.current !== issueId) return;
      setRec((s) =>
        s && s.draft && s.issueId === issueId
          ? {
              ...s,
              acting: null,
              editing: false,
              draft: {
                ...s.draft,
                confirmed: true,
                draft_recommended_position: result.recommended_position,
                draft_counter_language: result.donna_counter_language,
              },
            }
          : s,
      );
    } catch (e) {
      if (resolvingIdRef.current !== issueId) return;
      setRec((s) =>
        s && s.issueId === issueId ? { ...s, acting: null, error: donnaErrorMessage(e) } : s,
      );
    }
  }

  function toggleCollapse(nodeId: string) {
    setCollapsed((c) => {
      const n = new Set(c);
      if (n.has(nodeId)) n.delete(nodeId);
      else n.add(nodeId);
      return n;
    });
  }

  async function changeStatus(issueId: string, status: IssueStatus) {
    setStatusBusyId(issueId);
    try {
      const updated = await updateIssueStatus(issueId, status);
      setState((s) =>
        s.kind === "ready"
          ? { ...s, issues: s.issues.map((i) => (i.id === issueId ? updated : i)) }
          : s,
      );
    } catch {
      // Leave the prior status in place; the select reflects server truth on retry.
    } finally {
      setStatusBusyId(null);
    }
  }

  // --- DD-67 inline description edit ---
  function startIssueEdit(i: StoredIssue) {
    setEditingId(i.id);
    setEditTitle(i.title);
    setEditOur(i.our_position ?? "");
    setEditTheir(i.their_position ?? "");
    setEditError(null);
  }
  function cancelIssueEdit() {
    setEditingId(null);
    setEditError(null);
  }
  // Save the edited description. Empty position fields persist as null; the title
  // falls back to the issue's existing title when blank (title is NOT NULL).
  async function saveIssueEdit(i: StoredIssue) {
    if (editBusy) return;
    setEditBusy(true);
    setEditError(null);
    try {
      const updated = await updateIssue(i.id, {
        title: editTitle.trim() || i.title,
        our_position: editOur.trim() || null,
        their_position: editTheir.trim() || null,
      });
      setState((s) =>
        s.kind === "ready"
          ? { ...s, issues: s.issues.map((x) => (x.id === i.id ? updated : x)) }
          : s,
      );
      setEditingId(null);
    } catch {
      setEditError("Couldn't save the changes");
    } finally {
      setEditBusy(false);
    }
  }

  // Reset Donna's session when the contract changes — the thread is per-contract.
  useEffect(() => {
    setRailTab("issues");
    setResolvingId(null);
    setDonnaMessages(null);
    setDonnaError(null);
    setDonnaInput("");
    setAsking(false);
    setAskError(null);
    setDonnaContext(null);
    setPendingDraftLang(null);
    setDraftApplyError(null);
  }, [id]);

  // DD-68 reuse for a draft turn's "Use this language" with an issue in context:
  // openResolve fires loadRecommendation (which resets `rec`), so we can't seed the
  // edit buffer synchronously. Once the issue's recommendation lands ready, drop the
  // pending draft language into the rec [Edit] buffer and open it — the operator then
  // commits via the SAME edited-confirm path the card uses (useRecommendation(_, true)).
  useEffect(() => {
    if (!pendingDraftLang || !rec) return;
    // The issue's recommendation errored while we waited to apply into it — surface a
    // visible note instead of silently dropping the drafted language (F10b polish).
    if (rec.status === "error") {
      setDraftApplyError("Couldn't load the issue's recommendation to apply into — try again.");
      setPendingDraftLang(null);
      return;
    }
    if (rec.status !== "ready" || !rec.draft) return;
    setRec((s) =>
      s && s.status === "ready" && s.draft
        ? {
            ...s,
            editing: true,
            error: undefined,
            editPos: s.draft.draft_recommended_position ?? "",
            editLang: pendingDraftLang,
          }
        : s,
    );
    setPendingDraftLang(null);
  }, [pendingDraftLang, rec]);

  // Auto-dismiss the draft-apply error note after a few seconds (a transient toast-style
  // surface, not a persistent error). Cleared early when a new apply starts.
  useEffect(() => {
    if (!draftApplyError) return;
    const t = window.setTimeout(() => setDraftApplyError(null), 5000);
    return () => window.clearTimeout(t);
  }, [draftApplyError]);

  // Lazily load the persistent thread the first time Donna's tab is opened. Stored
  // history has no live citations/kind, so it renders as plain grounded answers.
  // Guarded on `donnaError` so a failed load shows a retry instead of looping.
  useEffect(() => {
    if (railTab !== "donna" || donnaMessages !== null || donnaError) return;
    let live = true;
    setDonnaLoading(true);
    (async () => {
      try {
        const thread = await getDonnaThread(id);
        if (!live) return;
        // An assistant turn persists its kind + cited ids (DD-40 rehydration): resolve the
        // stored ids back into chips so reloaded answers render identical to a fresh ask.
        // A turn without stored kind/citations (user turns, pre-migration rows) stays plain.
        setDonnaMessages(
          thread.messages.map((m) =>
            m.role === "user"
              ? { role: "user" as const, content: m.content }
              : {
                  role: "donna" as const,
                  content: m.content,
                  ...(m.kind ? { kind: m.kind } : {}),
                  ...(m.citations
                    ? { citations: resolveCitations(m.citations, rowById, issueById) }
                    : {}),
                },
          ),
        );
      } catch (e) {
        if (live) setDonnaError(donnaErrorMessage(e));
      } finally {
        if (live) setDonnaLoading(false);
      }
    })();
    return () => {
      live = false;
    };
    // donnaLoading must NOT be a dependency here, and is not in the guard above:
    // setDonnaLoading(true) would otherwise re-run the effect, whose cleanup flips
    // the in-flight fetch's `live=false`, so the (instant) resolved fetch skips its
    // state updates and the "Opening your thread…" spinner hangs forever.
  }, [railTab, id, donnaMessages, donnaError]);

  // Cycle the staged wait label while a question is in flight (frontend-design:
  // never a frozen word). Resets to the first phase when idle.
  useEffect(() => {
    if (!asking) {
      setPhaseIdx(0);
      return;
    }
    const t = window.setInterval(() => setPhaseIdx((p) => (p + 1) % DONNA_PHASES.length), 1500);
    return () => window.clearInterval(t);
  }, [asking]);

  // Keep the chat pinned to the newest turn as messages land / the wait shows.
  useEffect(() => {
    if (railTab !== "donna") return;
    const el = donnaScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [donnaMessages, asking, railTab]);

  // Load the resolving issue's stored brainstorm history (DD-77) when its view opens.
  useEffect(() => {
    if (!resolvingId) return;
    void refreshBrainstormSummaries(resolvingId);
  }, [resolvingId, refreshBrainstormSummaries]);

  // Pin the brainstorm overlay to its newest turn as messages land / the wait shows.
  useEffect(() => {
    if (!brainstorm) return;
    const el = brainstormScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [brainstorm, brainstormBusy]);

  // Ask Donna a grounded question. The operator's turn lands immediately; the
  // answer's citation ids resolve to clause chips against the current tree + ledger.
  async function sendDonna(raw: string) {
    const question = raw.trim();
    if (!question || asking) return;
    setDonnaInput("");
    setAskError(null);
    setDonnaMessages((m) => [...(m ?? []), { role: "user", content: question }]);
    setAsking(true);
    try {
      // Send the active context chip (clause node(s) + issue) so Donna advises/drafts
      // grounded on it; omitted entirely when nothing is selected (open read-and-explain).
      const ctx = donnaContext
        ? { node_ids: donnaContext.nodeIds, issue_id: donnaContext.issueId }
        : undefined;
      const res = await askDonna(id, question, ctx);
      const citations = resolveCitations(res.citations, rowById, issueById);
      setDonnaMessages((m) => [
        ...(m ?? []),
        {
          role: "donna",
          content: res.reply,
          mode: res.mode,
          draftLanguage: res.draft_language,
          citations,
        },
      ]);
    } catch (e) {
      setAskError(donnaErrorMessage(e));
    } finally {
      setAsking(false);
    }
  }

  // Clear only the DISPLAYED thread. The backend conversation + running summary
  // persist untouched (DD-40), so Donna keeps full context on the next ask — this
  // is a view reset, not a delete. Setting messages to [] (not null) shows the
  // empty state without re-fetching the stored history.
  function clearDonnaView() {
    setDonnaMessages([]);
    setAskError(null);
  }

  // F10b "Ask Donna about this clause": set the clause as Donna's context, jump to her
  // tab, focus the composer. Lilly's motivating case — editing a clause and wanting help
  // grounded on it. Donna then advises/drafts on this clause until the chip is cleared.
  function askDonnaAboutClause(clauseId: string) {
    setDonnaContext({ nodeIds: [clauseId], issueId: null });
    setRailTab("donna");
    requestAnimationFrame(() => donnaInputRef.current?.focus());
  }

  // F10b / DD-73: "Brainstorm with Donna ↗" opens the EPHEMERAL OVERLAY, primed with an
  // opening turn that restates the current recommendation `d`. The restatement is composed
  // CLIENT-SIDE (the server no longer seeds it) so the operator lands in a conversation that
  // already knows the rec and can iterate ("more aggressive", "tighter"). Nothing is persisted
  // — the transcript lives only in `brainstorm` state until close. Opening fresh always starts
  // empty (no reload of prior turns); stored summaries surface separately in the rec card.
  function brainstormFromRec(issueId: string, d: StoredRecommendation) {
    const num = issueNumberById.get(issueId) ?? "—";
    const parts: string[] = [d.rationale.trim()];
    if (d.draft_recommended_position?.trim())
      parts.push(`**Recommended position:** ${d.draft_recommended_position.trim()}`);
    if (d.draft_counter_language?.trim())
      parts.push(`**Counter-language:**\n${d.draft_counter_language.trim()}`);
    const opening: DonnaUiMessage = {
      role: "donna",
      content:
        `Let's brainstorm issue #${num}. Here's where I've landed — tell me to make it more ` +
        `aggressive, tighten it, or take a different angle.\n\n${parts.join("\n\n")}`,
      mode: "advise",
      citations: resolveCitations(d.citations ?? [], rowById, issueById),
    };
    setBrainstormInput("");
    setBrainstormError(null);
    setBrainstormNotice(null);
    setBrainstorm({ issueId, transcript: [opening] });
    requestAnimationFrame(() => brainstormInputRef.current?.focus());
  }

  // Encode the running transcript into the {question, answer} pairs the backend replays.
  // Each operator message pairs with the Donna reply that follows it. The seeded opening
  // (a Donna turn with no preceding operator message) is carried as an answer-only turn so
  // Donna re-reads her own starting position on every replay (and the close distiller sees it).
  function transcriptToTurns(transcript: DonnaUiMessage[]): BrainstormTurn[] {
    const turns: BrainstormTurn[] = [];
    let pendingQuestion: string | null = null;
    for (const m of transcript) {
      if (m.role === "user") {
        pendingQuestion = m.content;
      } else {
        turns.push({ question: pendingQuestion ?? "", answer: m.content });
        pendingQuestion = null;
      }
    }
    return turns;
  }

  // One ephemeral brainstorm turn (DD-77): append the operator message locally, replay the
  // whole running transcript to the stateless endpoint, append Donna's reply. Persists nothing.
  async function sendBrainstorm(raw: string) {
    const message = raw.trim();
    if (!message || brainstormBusy || brainstormClosing || !brainstorm) return;
    const { issueId, transcript } = brainstorm;
    const turns = transcriptToTurns(transcript);
    setBrainstormInput("");
    setBrainstormError(null);
    setBrainstorm((b) =>
      b ? { ...b, transcript: [...b.transcript, { role: "user", content: message }] } : b,
    );
    setBrainstormBusy(true);
    try {
      const res = await brainstormTurn(id, { issue_id: issueId, turns, message });
      const citations = resolveCitations(res.citations, rowById, issueById);
      setBrainstorm((b) =>
        b
          ? {
              ...b,
              transcript: [
                ...b.transcript,
                { role: "donna", content: res.reply, mode: "advise", citations },
              ],
            }
          : b,
      );
    } catch (e) {
      setBrainstormError(donnaErrorMessage(e));
    } finally {
      setBrainstormBusy(false);
    }
  }

  // "Close & save summary" (DD-73): distil the transcript into ONE stored summary on the issue,
  // then DISCARD the local transcript (ephemeral — it must not survive). A returned summary
  // confirms the save + refreshes the issue's history; a 204 shows a quiet "nothing to save".
  async function closeBrainstormOverlay() {
    if (!brainstorm || brainstormClosing) return;
    const { issueId, transcript } = brainstorm;
    setBrainstormClosing(true);
    setBrainstormError(null);
    try {
      const summary = await closeBrainstorm(id, {
        issue_id: issueId,
        turns: transcriptToTurns(transcript),
      });
      setBrainstorm(null);
      setBrainstormInput("");
      if (summary) {
        setBrainstormNotice({ kind: "saved", summary });
        void refreshBrainstormSummaries(issueId);
      } else {
        setBrainstormNotice({ kind: "empty" });
      }
    } catch (e) {
      setBrainstormError(donnaErrorMessage(e));
    } finally {
      setBrainstormClosing(false);
    }
  }

  // A draft turn's "Use this language" — never a new write. Route into an EXISTING
  // confirmed apply surface and let the operator commit there:
  //  • issue in context  → open its resolution view + prime the rec [Edit] buffer (DD-68
  //                        edited-confirm) via pendingDraftLang.
  //  • only a clause     → open the clause inline editor (F08) pre-filled; saveEdit commits.
  //  • neither           → copy to clipboard with a note (no apply surface to reuse).
  async function applyDraftLanguage(draftLang: string) {
    const text = draftLang.trim();
    if (!text) return;
    setDraftApplyError(null);
    const issueId = donnaContext?.issueId ?? null;
    if (issueId) {
      const node = issueById.get(issueId)?.node_id ?? null;
      setPendingDraftLang(text);
      openResolve(issueId, "issues", node);
      return;
    }
    const clauseId = donnaContext?.nodeIds[0] ?? null;
    const row = clauseId ? rowById.get(clauseId) ?? null : null;
    if (row) {
      setMenuFor(null);
      setInserting(null);
      setDeleteState(null);
      setRailTab("clause");
      setSelectedId(row.id);
      setEditing({ nodeId: row.id, draft: text, saving: false, error: null });
      jumpTo(row.id);
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setCopiedDraft(true);
      window.setTimeout(() => setCopiedDraft(false), 1600);
    } catch {
      // Clipboard blocked — the text stays visible in the bubble to copy manually.
    }
  }

  // Live jump as the operator types. A numeric query keeps the clause-number
  // behavior (jump on an exact number match). A keyword query substring-matches
  // every row and jumps live to the first match — same instant feel.
  function onJumpChange(v: string) {
    setJumpVal(v);
    setMatchIdx(0);
    setAiSearch({ kind: "idle" });
    const q = v.trim();
    if (!q) return;
    if (isNumericQuery(q)) {
      const exact = clauseByNumber.get(q);
      if (exact) jumpAndCapture(exact);
      return;
    }
    const lower = q.toLowerCase();
    const first = rows.find((r) => r.text.toLowerCase().includes(lower));
    if (first) jumpAndCapture(first.id);
  }

  // Step through keyword matches (‹ / ›, and Enter advances), wrapping at the ends.
  function stepMatch(dir: 1 | -1) {
    if (keywordMatches.length === 0) return;
    const next = (matchIdx + dir + keywordMatches.length) % keywordMatches.length;
    setMatchIdx(next);
    jumpAndCapture(keywordMatches[next]);
  }

  // AI fallback (Enter only — one call, never on keystroke). When a keyword query
  // has zero literal matches, ask the backend for a conceptual match and jump to
  // it, flagging that it was not a literal hit so the operator isn't misled.
  async function runConceptualSearch(query: string) {
    setAiSearch({ kind: "searching" });
    try {
      const res = await searchClause(id, query);
      if (res.node_id && rowById.has(res.node_id)) {
        jumpAndCapture(res.node_id);
        setAiSearch({ kind: "conceptual", query });
      } else {
        setAiSearch({ kind: "none", query });
      }
    } catch (e) {
      setAiSearch({ kind: "error", message: e instanceof Error ? e.message : "Search failed" });
    }
  }

  function onJumpEnter() {
    const q = jumpVal.trim();
    if (!q) return;
    if (isNumericQuery(q)) {
      const exact = clauseByNumber.get(q);
      if (exact) return jumpAndCapture(exact);
      const prefix = rows.find((r) => r.number && r.number.startsWith(q));
      if (prefix) jumpAndCapture(prefix.id);
      return;
    }
    // Recompute literal matches from the LIVE query (not the memo) so the first
    // Enter always reflects what's in the field — no stale-render "press Enter
    // again". A literal hit steps the matches; zero literal hits fires the
    // semantic search immediately (one Enter, never on keystroke).
    const lower = q.toLowerCase();
    const literal = rows.filter((r) => r.text.toLowerCase().includes(lower));
    if (literal.length > 0) return stepMatch(1);
    void runConceptualSearch(q);
  }

  function toggleCollapseAll() {
    if (allCollapsed) {
      setCollapsed(new Set());
      setCollapsedRegions(new Set());
    } else {
      // Collapse all: fully fold the Preamble + Back-matter regions (label only),
      // and fold every clause's sub-clauses — but leave the Clauses ("body")
      // region open so top-level clauses (1,2,3,…) stay visible.
      setCollapsed(new Set(allParents));
      setCollapsedRegions(new Set([...presentRegions].filter((k) => k !== "body")));
    }
  }

  function toggleRegion(key: Region) {
    setCollapsedRegions((c) => {
      const n = new Set(c);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  }

  const jumpMatch = jumpVal.trim() ? rowById.get(clauseByNumber.get(jumpVal.trim()) ?? "") ?? null : null;

  // "/" focuses the jump bar from anywhere; Escape clears the selected clause.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA");
      if (e.key === "/" && !typing) {
        e.preventDefault();
        jumpRef.current?.focus();
      } else if (e.key === "Escape" && !typing) {
        setSelectedId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close the actions menu on an outside click or Escape. Escape is captured so
  // it closes the menu without the global handler also clearing the selection.
  useEffect(() => {
    if (!menuFor) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuFor(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setMenuFor(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [menuFor]);

  // Close the Export menu on an outside click or Escape (mirrors the ⋮ menu).
  // A click on the trigger itself is inside `exportRef`, so it toggles, not closes.
  useEffect(() => {
    if (!exportOpen) return;
    const onDown = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        closeExport();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeExport();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [exportOpen]);

  function closeExport() {
    setExportOpen(false);
    setExportError(null);
    setRedlineNoBaseline(false);
  }
  async function runCleanCopy() {
    if (exportBusy) return;
    setExportBusy("clean");
    setExportError(null);
    try {
      await exportCleanCopy(id);
      closeExport();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExportBusy(null);
    }
  }
  async function runIssueList() {
    if (exportBusy) return;
    setExportBusy("issues");
    setExportError(null);
    try {
      await exportIssueList(id);
      closeExport();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExportBusy(null);
    }
  }
  // 409 = no baseline yet (nothing sent) → friendly hint, not a raw error. Any
  // other failure falls through to the shared exportError line.
  async function runRedline() {
    if (exportBusy) return;
    setExportBusy("redline");
    setExportError(null);
    setRedlineNoBaseline(false);
    try {
      await exportRedline(id);
      closeExport();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setRedlineNoBaseline(true);
      } else {
        setExportError(e instanceof Error ? e.message : "Export failed");
      }
    } finally {
      setExportBusy(null);
    }
  }

  function closeMark() {
    setMarkOpen(false);
    setMarkDrift(null);
    setMarkError(null);
  }
  // Mark as sent (DD-71). On the first call for a drifted contract the backend
  // returns marked:false/drift:true (nothing cut) → show the non-blocking warning;
  // "Mark anyway" re-calls with acknowledge=true; "Re-export" grabs a fresh .docx.
  async function runMarkSent(recipient: MarkSentRecipient, acknowledgeDrift = false) {
    if (markBusy) return;
    setMarkBusy(recipient);
    setMarkError(null);
    try {
      const res = await markSent(id, recipient, acknowledgeDrift);
      if (!res.marked && res.drift) {
        setMarkDrift({ recipient, version: res.version, lastExportAt: res.last_export_at });
        return;
      }
      setMarkDone({ recipient, version: res.version });
      closeMark();
      // The send moved the lifecycle on — refresh the badge + timeline.
      void loadLineage();
    } catch (e) {
      setMarkError(e instanceof Error ? e.message : "Couldn't mark as sent");
    } finally {
      setMarkBusy(null);
    }
  }
  // "Re-export" from the drift warning: grab a fresh clean copy (which restamps
  // last_export_at), then drop the warning so the operator can mark cleanly.
  async function reExportFromDrift() {
    setMarkDrift(null);
    await runCleanCopy();
  }

  // Import revision (F03b). Picking a source arms the file input and opens the
  // dialog; the menu stays open so a failure can show inline.
  function closeImport() {
    setImportOpen(false);
    setImportError(null);
  }
  function armImport(source: RevisionSource) {
    if (importBusy) return;
    setImportError(null);
    setImportSource(source);
    revFileRef.current?.click();
  }
  // On a chosen .docx: import against the last-sent baseline, then route into the
  // F03c review surface for the new session. The typed backend errors get clean,
  // operator-facing lines (422 tracked-changes, 409 no-baseline / open session).
  async function onRevisionFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file after an error
    if (!file || !importSource) return;
    setImportBusy(true);
    setImportError(null);
    try {
      const res = await importRevision(id, importSource, file);
      router.push(`/contracts/${id}/revisions/${res.session_id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setImportError(
          "This file has tracked changes, which isn't supported yet. Accept all changes in Word, then upload the clean copy.",
        );
      } else if (err instanceof ApiError && err.status === 409) {
        setImportError(err.message);
      } else {
        setImportError(err instanceof Error ? err.message : "Couldn't import this revision.");
      }
    } finally {
      setImportBusy(false);
    }
  }

  // F27: open a past version's snapshot read-only in the document panel. Closes the
  // drawer, shows the snapshot rows (built the same way as the live tree) behind a
  // banner, and clears any in-flight selection/edit so nothing dangles from the
  // live tree. "Return to working copy" (closeSnapshot) restores the live view.
  async function openSnapshot(entry: LineageTimelineEntry) {
    setLineageOpen(false);
    setSelectedId(null);
    setMenuFor(null);
    setEditing(null);
    setInserting(null);
    setDeleteState(null);
    setSnapshotView({
      snapshotId: entry.snapshot_id,
      version: entry.version,
      direction: entry.direction,
      party: entry.party,
      date: lineageDate(entry.created_at),
      loading: true,
      error: null,
      rows: [],
    });
    try {
      const tree = await getSnapshotTree(id, entry.snapshot_id);
      const rows = withNumbers(flatten(tree.nodes));
      setSnapshotView((s) =>
        s && s.snapshotId === entry.snapshot_id ? { ...s, loading: false, rows } : s,
      );
    } catch (e) {
      setSnapshotView((s) =>
        s && s.snapshotId === entry.snapshot_id
          ? { ...s, loading: false, error: e instanceof Error ? e.message : "Couldn't load this version" }
          : s,
      );
    }
  }
  function closeSnapshot() {
    setSnapshotView(null);
  }

  // DD-85/DD-87 version delete — preview (no mutation) then confirm. Trash click
  // arms this: fetch the preview, then open the confirm dialog with its warnings.
  async function startDeleteVersion(entry: LineageTimelineEntry) {
    if (deletePreviewFor || deleteBusy) return;
    setDeleteError(null);
    setDeleteVersion(null);
    setDeletePreviewFor(entry.snapshot_id);
    try {
      const preview = await previewDeleteSnapshot(id, entry.snapshot_id);
      setDeleteVersion({ snapshotId: entry.snapshot_id, version: entry.version, preview });
    } catch (e) {
      // 404/409 (e.g. a revision baseline) surface here, drawer-level.
      setDeleteError(e instanceof Error ? e.message : "Couldn't check this version");
    } finally {
      setDeletePreviewFor(null);
    }
  }
  // Confirm → execute the wipe. On success: close any open read-only view of the
  // deleted snapshot, then bump reloadKey so BOTH the tree (a latest-delete rolled
  // the working copy back) and the lineage/badge re-fetch.
  async function confirmDeleteVersion() {
    if (!deleteVersion || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    const deletedId = deleteVersion.snapshotId;
    try {
      await deleteSnapshotVersion(id, deletedId);
      setSnapshotView((s) => (s && s.snapshotId === deletedId ? null : s));
      setDeleteVersion(null);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Couldn't delete this version");
    } finally {
      setDeleteBusy(false);
    }
  }
  function cancelDeleteVersion() {
    setDeleteVersion(null);
    setDeleteError(null);
  }

  // Close the lineage drawer on an outside click or Escape (mirrors the Export menu).
  useEffect(() => {
    if (!lineageOpen) return;
    const onDown = (e: MouseEvent) => {
      if (lineageRef.current && !lineageRef.current.contains(e.target as Node)) setLineageOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setLineageOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [lineageOpen]);

  // Transient "Marked as sent" confirmation — clears itself after a few seconds.
  useEffect(() => {
    if (!markDone) return;
    const t = setTimeout(() => setMarkDone(null), 4500);
    return () => clearTimeout(t);
  }, [markDone]);

  // Close the Mark-as-sent menu on an outside click or Escape (mirrors Export).
  useEffect(() => {
    if (!markOpen) return;
    function onDown(e: MouseEvent) {
      if (markRef.current && !markRef.current.contains(e.target as Node)) closeMark();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeMark();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [markOpen]);

  // Close the Import-revision menu on an outside click or Escape (mirrors Mark).
  useEffect(() => {
    if (!importOpen) return;
    function onDown(e: MouseEvent) {
      if (importRef.current && !importRef.current.contains(e.target as Node)) closeImport();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeImport();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [importOpen]);

  const selectedRow = selectedId ? rowById.get(selectedId) ?? null : null;
  // Right-pane "Selected clause" card state. Edit shows only for a node with real
  // editable prose (the backend 422s tables / empty text), Delete shows for any.
  const editingSelected = !!selectedRow && editing?.nodeId === selectedRow.id;
  const deletingSelected = !!selectedRow && deleteState?.nodeId === selectedRow.id;
  const canEditSelected =
    !!selectedRow && selectedRow.text.trim().length > 0 && selectedRow.contentType !== "table";

  // DD-68: the issue currently drilled into (null = no resolution view). Resolves
  // live from `issues` so an inline edit / status toggle reflects server truth.
  const resolvingIssue = resolvingId ? issues.find((i) => i.id === resolvingId) ?? null : null;
  const resolveClauseRow =
    resolvingIssue?.node_id ? rowById.get(resolvingIssue.node_id) ?? null : null;

  // DD-59: the single Description is the operator's substance and routes to the
  // stance side by who raised it (initiator); `title` is a deterministic snippet
  // (no LLM). Shared by the clause-scoped form and the contract-level form, which
  // differ only in node_id (a selected clause vs null).
  async function raiseIssue(nodeId: string | null, desc: string, init: Initiator) {
    const issue = await createIssue(id, {
      node_id: nodeId,
      title: deriveTitle(desc),
      our_position: init === "operator" ? desc : null,
      their_position: init === "counterparty" ? desc : null,
      initiator: init,
    });
    setState((s) => (s.kind === "ready" ? { ...s, issues: [issue, ...s.issues] } : s));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = description.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setFormError(null);
    try {
      await raiseIssue(selectedId, trimmed, initiator);
      setDescription("");
      setInitiator("operator");
      // Stay on the Current Clause tab so the new issue appears in this clause's
      // open-issues list below — immediate confirmation it was captured (DD-66.2c).
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Couldn't raise the issue");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitFreeIssue(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = freeDescription.trim();
    if (!trimmed || freeSubmitting) return;
    setFreeSubmitting(true);
    setFreeError(null);
    try {
      await raiseIssue(null, trimmed, freeInitiator);
      setFreeDescription("");
      setFreeInitiator("operator");
      setFreeFormOpen(false);
    } catch (err) {
      setFreeError(err instanceof Error ? err.message : "Couldn't raise the issue");
    } finally {
      setFreeSubmitting(false);
    }
  }

  function openMenu(nodeId: string) {
    setMenuFor((m) => (m === nodeId ? null : nodeId));
  }

  // --- Rearrange drop callback: RearrangeTree reports the moved node id after a
  // successful move. Re-derived numbers depend on the whole tree's new shape, so a
  // scoped refetch is the reliable path (DD-02); it PRESERVES `collapsed` (the moved
  // id is still valid) and replaces only `rows`. The parent owns selection + flash:
  // we select the moved node and flash it so the operator sees where it landed
  // (RearrangeTree scrolls its row into view on the flashId change).
  async function handleNodeMoved(movedId: string) {
    try {
      const tree = await getContractTree(id);
      setState((st) => (st.kind === "ready" ? { ...st, rows: withNumbers(flatten(tree.nodes)) } : st));
    } finally {
      setSelectedId(movedId);
      setFlashId(movedId);
      if (flashTimer.current) window.clearTimeout(flashTimer.current);
      flashTimer.current = window.setTimeout(() => setFlashId((f) => (f === movedId ? null : f)), 1200);
    }
  }

  // --- F08 inline edit ---
  function startEdit(r: FlatNode) {
    setMenuFor(null);
    setInserting(null);
    setDeleteState(null);
    setSelectedId(r.id);
    setEditing({ nodeId: r.id, draft: r.text, saving: false, error: null });
  }
  function cancelEdit() {
    setEditing(null);
  }
  async function saveEdit() {
    if (!editing || editing.saving) return;
    const original = rowById.get(editing.nodeId)?.text ?? "";
    const text = editing.draft;
    // No-op (unchanged) exits cleanly without a write; otherwise require content.
    if (text === original) return setEditing(null);
    if (!text.trim()) return;
    const nodeId = editing.nodeId;
    setEditing((s) => (s ? { ...s, saving: true, error: null } : s));
    try {
      const stored = await editNode(id, nodeId, text);
      const newText = stored.heading ?? stored.body ?? stored.plain_text ?? text;
      setState((st) =>
        st.kind === "ready"
          ? { ...st, rows: st.rows.map((r) => (r.id === nodeId ? { ...r, text: newText } : r)) }
          : st,
      );
      setEditing(null);
    } catch (e) {
      setEditing((s) =>
        s ? { ...s, saving: false, error: e instanceof Error ? e.message : "Couldn't save the edit" } : s,
      );
    }
  }

  // --- F08b insert: open an empty editor at the computed target position ---
  function startInsert(node: FlatNode, mode: "below" | "sub" | "above") {
    setMenuFor(null);
    setEditing(null);
    let parentId: string | null;
    let afterNodeId: string | null;
    let beforeNodeId: string | null;
    let subtreeAnchorId: string;
    let depth: number;
    let beforeId: string | null;
    if (mode === "below") {
      parentId = parentOf(rows, node.id);
      afterNodeId = node.id;
      beforeNodeId = null;
      subtreeAnchorId = node.id;
      depth = node.depth;
      beforeId = firstAfterSubtreeId(rows, node.id);
    } else if (mode === "sub") {
      parentId = node.id;
      afterNodeId = null;
      beforeNodeId = null;
      subtreeAnchorId = node.id;
      depth = node.depth + 1;
      beforeId = firstAfterSubtreeId(rows, node.id);
      // Expand the parent so the appended child's editor is visible.
      setCollapsed((c) => {
        if (!c.has(node.id)) return c;
        const n = new Set(c);
        n.delete(node.id);
        return n;
      });
    } else {
      // Insert-above lands immediately BEFORE the target via the backend's
      // before_node_id — uniform for a first child AND a middle node; the local
      // splice (below) lands at the target's own index.
      parentId = parentOf(rows, node.id);
      afterNodeId = null;
      beforeNodeId = node.id;
      subtreeAnchorId = node.id;
      depth = node.depth;
      beforeId = node.id;
    }
    // The editor lives in the body region; if the boundary falls outside it (the
    // node is the last clause), append at the body region's end instead.
    if (beforeId && regionByNode.get(beforeId) !== "body") beforeId = null;
    setInserting({
      mode,
      parentId,
      afterNodeId,
      beforeNodeId,
      subtreeAnchorId,
      depth,
      beforeId,
      draft: "",
      saving: false,
      error: null,
      anchorNodeId: node.id,
      donnaOpen: false,
      donnaDesc: "",
      donnaBusy: false,
      donnaError: null,
    });
  }
  function cancelInsert() {
    setInserting(null);
  }

  // F08d "Draft with Donna": send the operator's description + the insert anchor/mode to
  // Donna, then pre-fill the editor with her grounded clause (heading prepended when she
  // returns one, since the create path stores a single text field). The operator reviews
  // and edits before committing through the normal saveInsert (F08b) — nothing is
  // persisted by the draft call. An empty body = Donna couldn't draft → a gentle nudge.
  async function draftWithDonna() {
    if (!inserting || inserting.donnaBusy) return;
    const description = inserting.donnaDesc.trim();
    if (!description) return;
    const { anchorNodeId, mode } = inserting;
    setInserting((s) => (s ? { ...s, donnaBusy: true, donnaError: null } : s));
    try {
      const result = await draftClause(id, {
        description,
        anchor_node_id: anchorNodeId,
        mode,
      });
      const body = result.body.trim();
      if (!body) {
        setInserting((s) =>
          s
            ? {
                ...s,
                donnaBusy: false,
                donnaError:
                  "Donna couldn't draft this from the contract — try describing it with more detail.",
              }
            : s,
        );
        return;
      }
      const composed = result.heading?.trim() ? `${result.heading.trim()}\n${body}` : body;
      // Fill the editor, close the prompt panel, keep the description for a re-draft.
      setInserting((s) => (s ? { ...s, draft: composed, donnaBusy: false, donnaOpen: false } : s));
    } catch (e) {
      setInserting((s) => (s ? { ...s, donnaBusy: false, donnaError: donnaErrorMessage(e) } : s));
    }
  }
  async function saveInsert() {
    if (!inserting || inserting.saving) return;
    const text = inserting.draft.trim();
    if (!text) return;
    const { mode, parentId, afterNodeId, beforeNodeId, subtreeAnchorId, depth } = inserting;
    setInserting((s) => (s ? { ...s, saving: true, error: null } : s));
    try {
      const stored = await createNode(id, {
        parent_id: parentId,
        after_node_id: afterNodeId,
        before_node_id: beforeNodeId,
        text,
        role: "clause",
      });
      const newRow: FlatNode = {
        id: stored.id,
        depth,
        role: "clause",
        text: stored.body ?? stored.heading ?? stored.plain_text ?? text,
        isHeading: false,
        contentType: stored.content_type,
        number: "",
      };
      setState((st) => {
        if (st.kind !== "ready") return st;
        // Insert-above lands at the target's own index (before it); below/sub land
        // just past the anchor's whole subtree.
        const at =
          mode === "above"
            ? st.rows.findIndex((r) => r.id === subtreeAnchorId)
            : subtreeEndIndex(st.rows, subtreeAnchorId);
        const next = [...st.rows.slice(0, at), newRow, ...st.rows.slice(at)];
        return { ...st, rows: withNumbers(next) };
      });
      if (parentId) {
        setCollapsed((c) => {
          if (!c.has(parentId)) return c;
          const n = new Set(c);
          n.delete(parentId);
          return n;
        });
      }
      setInserting(null);
      setSelectedId(stored.id);
      requestAnimationFrame(() => flashRow(stored.id));
    } catch (e) {
      setInserting((s) =>
        s ? { ...s, saving: false, error: e instanceof Error ? e.message : "Couldn't add the clause" } : s,
      );
    }
  }

  // --- F08c delete: arm an inline confirm, then drop the subtree on confirm ---
  function startDelete(r: FlatNode) {
    setEditing(null);
    setInserting(null);
    setDeleteState({ nodeId: r.id, descendantCount: descendantCount(rows, r.id), deleting: false, error: null });
  }
  async function confirmDelete() {
    if (!deleteState || deleteState.deleting) return;
    const nodeId = deleteState.nodeId;
    setDeleteState((s) => (s ? { ...s, deleting: true, error: null } : s));
    try {
      const { deleted_ids } = await deleteNode(id, nodeId);
      const removed = new Set(deleted_ids);
      setState((st) =>
        st.kind === "ready"
          ? { ...st, rows: withNumbers(st.rows.filter((r) => !removed.has(r.id))) }
          : st,
      );
      setSelectedId((sel) => (sel && removed.has(sel) ? null : sel));
      setMenuFor(null);
      setDeleteState(null);
    } catch (e) {
      setDeleteState((s) =>
        s ? { ...s, deleting: false, error: e instanceof Error ? e.message : "Couldn't delete the clause" } : s,
      );
    }
  }

  // Shared inline editor (edit-in-place + new-node). Stops click propagation so
  // typing in it never toggles the underlying row's collapse/selection.
  const renderEditor = (opts: {
    draft: string;
    saving: boolean;
    error: string | null;
    busyLabel: string;
    saveLabel: string;
    placeholder?: string;
    onChange: (v: string) => void;
    onSave: () => void;
    onCancel: () => void;
  }) => {
    const canSave = opts.draft.trim().length > 0;
    return (
      <div className={styles.editor} onClick={(e) => e.stopPropagation()}>
        <textarea
          className={styles.editorArea}
          rows={5}
          value={opts.draft}
          placeholder={opts.placeholder}
          autoFocus
          onChange={(e) => opts.onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              opts.onCancel();
            } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              if (canSave && !opts.saving) opts.onSave();
            }
          }}
        />
        <div className={styles.editorBar}>
          <button
            type="button"
            className={styles.editorSave}
            disabled={!canSave || opts.saving}
            onClick={opts.onSave}
          >
            {opts.saving ? opts.busyLabel : opts.saveLabel}
          </button>
          <button type="button" className={styles.editorCancel} disabled={opts.saving} onClick={opts.onCancel}>
            Cancel
          </button>
          {opts.saving && <span className={styles.editorBusy}>{opts.busyLabel}</span>}
          {opts.error && <span className={styles.editorError}>{opts.error}</span>}
        </div>
      </div>
    );
  };

  // The standalone new-node editor row, slotted into the tree at the target depth.
  const renderInsertRow = () => {
    if (!inserting) return null;
    return (
      <div
        className={styles.insertRow}
        style={{ paddingLeft: 18 + inserting.depth * 22 }}
        onClick={(e) => e.stopPropagation()}
      >
        <span className={styles.twirlSpace} aria-hidden />
        <span className={styles.insertLabel}>{inserting.mode === "sub" ? "New sub" : "New"}</span>
        <div className={styles.insertBody}>
          {/* F08d: draft this clause with Donna, then edit the result below. */}
          {inserting.donnaOpen ? (
            <div className={styles.draftPanel} onClick={(e) => e.stopPropagation()}>
              <div className={styles.draftPanelHead}>
                <span className={styles.recMark} aria-hidden>
                  ✦
                </span>
                Draft with Donna
              </div>
              <textarea
                className={styles.draftDesc}
                rows={2}
                value={inserting.donnaDesc}
                autoFocus
                placeholder="Describe the clause you need — e.g. “a 30-day cure period before termination for breach”"
                onChange={(e) => setInserting((s) => (s ? { ...s, donnaDesc: e.target.value } : s))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    void draftWithDonna();
                  }
                }}
              />
              <div className={styles.draftBar}>
                <button
                  type="button"
                  className={styles.recBtnPrimary}
                  disabled={inserting.donnaBusy || !inserting.donnaDesc.trim()}
                  onClick={() => void draftWithDonna()}
                >
                  {inserting.donnaBusy ? "Drafting…" : "Draft it"}
                </button>
                <button
                  type="button"
                  className={styles.recBtnGhost}
                  disabled={inserting.donnaBusy}
                  onClick={() => setInserting((s) => (s ? { ...s, donnaOpen: false } : s))}
                >
                  Cancel
                </button>
                {inserting.donnaError && (
                  <span className={styles.editorError}>{inserting.donnaError}</span>
                )}
              </div>
            </div>
          ) : (
            <button
              type="button"
              className={styles.draftWithDonnaBtn}
              onClick={(e) => {
                e.stopPropagation();
                setInserting((s) => (s ? { ...s, donnaOpen: true } : s));
              }}
            >
              <span className={styles.recMark} aria-hidden>
                ✦
              </span>
              {inserting.draft.trim() ? "Redraft with Donna" : "Draft with Donna"}
            </button>
          )}
          {renderEditor({
            draft: inserting.draft,
            saving: inserting.saving,
            error: inserting.error,
            busyLabel: "Adding…",
            saveLabel: "Add clause",
            placeholder: "New clause text… or draft it with Donna above",
            onChange: (v) => setInserting((s) => (s ? { ...s, draft: v } : s)),
            onSave: saveInsert,
            onCancel: cancelInsert,
          })}
        </div>
      </div>
    );
  };

  // F05 hover-to-define: anchor the definition popover to the hovered term's
  // viewport rect, clamped so a term near the right edge keeps the card on
  // screen. Hover only (no click handler) so the row's select/expand is intact.
  function showTermPopover(e: React.MouseEvent<HTMLElement>, entry: TermEntry) {
    const rect = e.currentTarget.getBoundingClientRect();
    const left = Math.min(Math.max(8, rect.left), window.innerWidth - 308);
    setTermPopover({ entry, top: rect.bottom + 6, left });
  }
  function hideTermPopover() {
    setTermPopover(null);
  }

  // Render a row's text with known defined terms marked (dotted underline +
  // hover popover). Returns the raw string when nothing matches, so an empty
  // registry or a term-free row stays a plain text node.
  const renderClauseText = (text: string) => {
    const tokens = tokenizeText(text, termIndex);
    if (tokens.length === 1 && tokens[0].kind === "text") return text;
    return tokens.map((tok, i) =>
      tok.kind === "text" ? (
        <Fragment key={i}>{tok.value}</Fragment>
      ) : (
        <span
          key={i}
          className={styles.definedTerm}
          onMouseEnter={(e) => showTermPopover(e, tok.entry)}
          onMouseLeave={hideTermPopover}
        >
          {tok.value}
        </span>
      ),
    );
  };

  // One tree row, rendered uniformly across all three regions. A row that has
  // children (FIX 3) toggles its own collapse when clicked anywhere — selecting
  // AND expanding/collapsing its sub-clauses; a leaf row only selects. Selecting
  // any row opens the Current Clause tab on that clause (its text + raise form).
  // The twirl keeps stopPropagation so it doesn't double-toggle.
  const renderRow = (r: FlatNode) => {
    const isClause = r.role === "clause";
    const count = issuesByNode.get(r.id) ?? 0;
    const hasChildren = allParents.has(r.id);
    const isCollapsed = collapsed.has(r.id);
    const menuOpen = menuFor === r.id;
    return (
      <div
        key={r.id}
        ref={(el) => {
          if (el) rowRefs.current.set(r.id, el);
          else rowRefs.current.delete(r.id);
        }}
        className={[
          styles.row,
          selectedId === r.id ? styles.selected : "",
          flashId === r.id ? styles.flash : "",
        ].join(" ")}
        style={{ paddingLeft: 18 + r.depth * 22 }}
        onClick={() => {
          setSelectedId(r.id);
          setRailTab("clause");
          if (hasChildren) toggleCollapse(r.id);
        }}
      >
        {hasChildren ? (
          <button
            type="button"
            className={styles.twirl}
            aria-label={isCollapsed ? "Expand" : "Collapse"}
            aria-expanded={!isCollapsed}
            title={isCollapsed ? "Expand" : "Collapse"}
            onClick={(e) => {
              e.stopPropagation();
              toggleCollapse(r.id);
            }}
          >
            {isCollapsed ? "▸" : "▾"}
          </button>
        ) : (
          <span className={styles.twirlSpace} aria-hidden />
        )}
        {isClause ? (
          <span className={styles.num}>{r.number}</span>
        ) : (
          <span className={styles.roleLabel}>{nonClauseLabel(r)}</span>
        )}
        <span
          className={[
            styles.text,
            selectedId === r.id ? styles.textFull : "",
            r.isHeading ? styles.headingText : "",
          ].join(" ")}
        >
          {r.text ? renderClauseText(r.text) : <em>(no text)</em>}
        </span>
        {count > 0 && (
          <span className={styles.rowIssues} title={`${count} issue${count === 1 ? "" : "s"} raised here`}>
            {count}
          </span>
        )}
        {isClause && (
          <span className={styles.actionsWrap} ref={menuOpen ? menuRef : null}>
            <button
              type="button"
              className={[styles.actions, menuOpen ? styles.actionsActive : ""].join(" ")}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label="Insert clause"
              title="Insert"
              onClick={(e) => {
                e.stopPropagation();
                openMenu(r.id);
              }}
            >
              ⋮
            </button>
            {menuOpen && (
              <div className={styles.menu} role="menu" onClick={(e) => e.stopPropagation()}>
                <button
                  type="button"
                  role="menuitem"
                  className={styles.menuItem}
                  onClick={(e) => {
                    e.stopPropagation();
                    startInsert(r, "below");
                  }}
                >
                  Insert clause below
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={styles.menuItem}
                  onClick={(e) => {
                    e.stopPropagation();
                    startInsert(r, "sub");
                  }}
                >
                  Insert sub-clause
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={styles.menuItem}
                  onClick={(e) => {
                    e.stopPropagation();
                    startInsert(r, "above");
                  }}
                >
                  Insert clause above
                </button>
              </div>
            )}
          </span>
        )}
      </div>
    );
  };

  // One clickable region header (mirrors the import review's regionHead +
  // toggleRegion): a ▸/▾ affordance + label/hint. Collapsing hides the region's
  // rows, leaving only this header. Rendered only when the region has ≥1 row.
  const renderRegion = (key: Region, visibleRowsOf: FlatNode[]) => {
    if (!presentRegions.has(key)) return null;
    const isCollapsed = collapsedRegions.has(key);
    // The insert editor only ever targets the body region (clauses). It renders
    // ahead of its `beforeId` row, or at the region's end when beforeId is null.
    const insertHere = !!inserting && key === "body" && !isCollapsed;
    return (
      <Fragment key={key}>
        <div
          className={styles.regionHead}
          role="button"
          tabIndex={0}
          aria-expanded={!isCollapsed}
          onClick={() => toggleRegion(key)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggleRegion(key);
            }
          }}
        >
          <span className={styles.regionTwirl} aria-hidden>
            {isCollapsed ? "▸" : "▾"}
          </span>
          {REGION_LABEL[key]}
          <span className={styles.regionHint}>{REGION_HINT[key]}</span>
        </div>
        {!isCollapsed &&
          visibleRowsOf.map((r) =>
            insertHere && inserting?.beforeId === r.id ? (
              <Fragment key={r.id}>
                {renderInsertRow()}
                {renderRow(r)}
              </Fragment>
            ) : (
              renderRow(r)
            ),
          )}
        {insertHere && inserting?.beforeId === null && renderInsertRow()}
      </Fragment>
    );
  };

  // F27: one read-only snapshot row. Mirrors renderRow's visual vocabulary (the
  // same .row / .num / .roleLabel / .text classes) but carries NO interactivity —
  // no select, no twirl, no actions — since a past version is immutable. Defined
  // terms still resolve (the deal-level registry is version-independent).
  const renderSnapshotRow = (r: FlatNode) => (
    <div
      key={r.id}
      className={[styles.row, styles.rowReadonly].join(" ")}
      style={{ paddingLeft: 18 + r.depth * 22 }}
    >
      <span className={styles.twirlSpace} aria-hidden />
      {r.role === "clause" ? (
        <span className={styles.num}>{r.number}</span>
      ) : (
        <span className={styles.roleLabel}>{nonClauseLabel(r)}</span>
      )}
      <span className={[styles.text, r.isHeading ? styles.headingText : ""].join(" ")}>
        {r.text ? renderClauseText(r.text) : <em>(no text)</em>}
      </span>
    </div>
  );

  // One issue card, shared by the Issues tab and the Current Clause open-issues list.
  // In the Issues tab a clause-anchored card is a navigation instrument: clicking it
  // jumps to the clause and opens the Current Clause tab (DD-66.4). Free-floating
  // cards — and every card already on the Current Clause tab — expand inline to the
  // status toggle and an editable description (DD-67) instead.
  // DD-68: every issue card is a drill-in to the resolution view (refines DD-66.4 —
  // an issue-card click navigates to the Issue view, not merely the Current Clause
  // tab). The card shows the anchor/status/who at a glance; clicking it opens the
  // view where the issue is worked (edit, status, resolve-by-editing-the-clause).
  const renderIssueCard = (i: StoredIssue, context: "issues" | "clause") => {
    const anchor = i.node_id ? rowById.get(i.node_id) ?? null : null;
    const isCp = i.initiator === "counterparty";
    const isDonna = i.initiator === "donna";
    const status = asStatus(i.status);
    return (
      <div key={i.id} className={styles.issueCard}>
        <div className={styles.issueTop}>
          <span className={styles.issueNum}>#{issueNumberById.get(i.id) ?? "—"}</span>
          <span className={[styles.issueAnchor, i.node_id ? "" : styles.issueAnchorNone].join(" ")}>
            {anchor ? (anchor.number ? `§${anchor.number}` : titleCase(anchor.role)) : "Contract"}
          </span>
          <span className={[styles.status, styles[STATUS_CLASS[status]]].join(" ")}>
            {STATUS_LABEL[status]}
          </span>
          <span className={styles.issueTime} title={new Date(i.created_at).toLocaleString()}>
            {shortTime(i.created_at)}
          </span>
          <span
            className={[styles.who, isCp ? styles.whoCp : isDonna ? styles.whoDonna : styles.whoUs].join(" ")}
          >
            {isCp ? "Counterparty" : isDonna ? "Donna" : "Us"}
          </span>
        </div>

        <button
          type="button"
          className={styles.issueBody}
          onClick={() => openResolve(i.id, context, i.node_id)}
        >
          <span className={styles.issueTitle}>{i.title}</span>
          {i.our_position && <span className={styles.issueNote}>{i.our_position}</span>}
          <span className={styles.issueExpand}>Open to resolve ▸</span>
        </button>
      </div>
    );
  };

  // Brainstorm history (DD-77): the preserved reasoning from past brainstorm passes on this
  // issue — question explored / where it landed / fallbacks weighed, newest first. This is
  // where the operator finds the distilled thinking after the raw transcript is discarded.
  const renderBrainstormHistory = (issueId: string) => {
    const summaries = brainstormSummaries[issueId] ?? [];
    if (summaries.length === 0) return null;
    return (
      <section className={styles.bsHistory} aria-label="Brainstorm history">
        <div className={styles.bsHistoryHead}>
          <span className={styles.bsHistoryMark} aria-hidden>
            ✦
          </span>
          Brainstorm history
          <span className={styles.bsHistoryCount}>{summaries.length}</span>
        </div>
        {summaries.map((s) => (
          <article key={s.id} className={styles.bsSummary}>
            {s.question && (
              <div className={styles.bsField}>
                <span className={styles.bsFieldLabel}>Explored</span>
                <p className={styles.bsFieldText}>{s.question}</p>
              </div>
            )}
            {s.conclusion && (
              <div className={styles.bsField}>
                <span className={styles.bsFieldLabel}>Where it landed</span>
                <p className={styles.bsFieldText}>{s.conclusion}</p>
              </div>
            )}
            {s.fallbacks && (
              <div className={styles.bsField}>
                <span className={styles.bsFieldLabel}>Fallbacks considered</span>
                <p className={styles.bsFieldText}>{s.fallbacks}</p>
              </div>
            )}
            <time className={styles.bsSummaryWhen} dateTime={s.created_at}>
              {new Date(s.created_at).toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
              })}
            </time>
          </article>
        ))}
      </section>
    );
  };

  // F11 recommendation card — the live resolution layer (DD-68). Donna's grounded,
  // cited draft for THIS issue: a rationale, a recommended landing and/or counter-
  // language, an honest market-gap flag, and the operator's confirm/edit/refresh
  // controls. Replaces the Phase-2 placeholder. The draft never reaches the exported
  // issue fields until [Use] (DD-68). Citation chips reuse the Donna-tab resolver.
  const renderRecCard = (issueId: string) => {
    const eyebrow = (
      <div className={styles.recEyebrow}>
        <span className={styles.recMark} aria-hidden>
          ✦
        </span>
        Donna&apos;s recommendation
      </div>
    );

    // Loading / error / no-state-yet: a quiet framed bay so the slot never looks broken.
    if (!rec || rec.issueId !== issueId || (rec.status === "loading" && !rec.draft)) {
      return (
        <section className={styles.recCard} aria-label="Donna's recommendation">
          {eyebrow}
          <p className={styles.recLoading}>
            <span className={styles.recSpinner} aria-hidden />
            Donna&apos;s working out a recommendation…
          </p>
        </section>
      );
    }
    if (rec.status === "error") {
      return (
        <section className={styles.recCard} aria-label="Donna's recommendation">
          {eyebrow}
          <p className={styles.recError}>{rec.error ?? "Donna couldn't produce one just now."}</p>
          <button type="button" className={styles.recBtnGhost} onClick={() => void loadRecommendation(issueId)}>
            ↻ Try again
          </button>
        </section>
      );
    }

    const d = rec.draft;
    if (!d) return null;
    const chips = resolveCitations(d.citations ?? [], rowById, issueById);
    const usable = recHasLanguage(d);
    const busy = rec.acting === "use";
    const refreshing = rec.acting === "refresh";

    return (
      <section className={styles.recCard} aria-label="Donna's recommendation">
        <div className={styles.recHead}>
          {eyebrow}
          {d.confirmed && <span className={styles.recApplied}>✓ Applied to this issue</span>}
        </div>

        <div className={styles.recRationale}>{renderDonnaMarkdown(d.rationale)}</div>

        {chips.length > 0 && (
          <div className={styles.cites}>
            {chips.map((c, ci) =>
              c.kind === "clause" ? (
                <button
                  key={ci}
                  type="button"
                  className={styles.cite}
                  title="Jump to this clause"
                  onClick={() => jumpTo(c.nodeId)}
                >
                  <span className={styles.citeArrow} aria-hidden>
                    ↳
                  </span>
                  {c.label}
                </button>
              ) : (
                <span key={ci} className={[styles.cite, styles.citeIssue].join(" ")}>
                  {c.label}
                </span>
              ),
            )}
          </div>
        )}

        {d.missing_benchmark && (
          <p className={styles.recGap}>
            Donna needed a market benchmark she couldn&apos;t ground here — she&apos;s recommended the
            structure, not a number. Add the figure before relying on it.
          </p>
        )}

        {rec.editing ? (
          <div className={styles.recEditForm}>
            <div className={styles.detailField}>
              <span className={styles.detailLabel}>Recommended position</span>
              <textarea
                className={[styles.control, styles.note].join(" ")}
                rows={3}
                value={rec.editPos}
                onChange={(e) => setRec((s) => (s ? { ...s, editPos: e.target.value } : s))}
                placeholder="Where this should land…"
              />
            </div>
            <div className={styles.detailField}>
              <span className={styles.detailLabel}>Counter-language</span>
              <textarea
                className={[styles.control, styles.note].join(" ")}
                rows={5}
                value={rec.editLang}
                onChange={(e) => setRec((s) => (s ? { ...s, editLang: e.target.value } : s))}
                placeholder="Exact clause language to propose…"
              />
            </div>
            <div className={styles.recActions}>
              <button
                type="button"
                className={styles.recBtnPrimary}
                disabled={busy}
                onClick={() => void useRecommendation(issueId, true)}
              >
                {busy ? "Applying…" : "Use this language"}
              </button>
              <button type="button" className={styles.recBtnGhost} disabled={busy} onClick={cancelRecEdit}>
                Cancel
              </button>
            </div>
            {rec.error && <span className={styles.recInlineError}>{rec.error}</span>}
          </div>
        ) : (
          <>
            {d.draft_recommended_position && (
              <div className={styles.detailField}>
                <span className={styles.detailLabel}>Recommended position</span>
                <p className={styles.detailText}>{d.draft_recommended_position}</p>
              </div>
            )}
            {d.draft_counter_language && (
              <div className={styles.detailField}>
                <span className={styles.detailLabel}>Counter-language</span>
                <p className={[styles.detailText, styles.recLanguage].join(" ")}>
                  {d.draft_counter_language}
                </p>
              </div>
            )}

            <div className={styles.recActions}>
              {usable && !d.confirmed && (
                <button
                  type="button"
                  className={styles.recBtnPrimary}
                  disabled={busy || refreshing}
                  onClick={() => void useRecommendation(issueId, false)}
                >
                  {busy ? "Applying…" : "Use Donna's language"}
                </button>
              )}
              {usable && (
                <button
                  type="button"
                  className={styles.recBtnGhost}
                  disabled={busy || refreshing}
                  onClick={() => startRecEdit(d)}
                >
                  Edit
                </button>
              )}
              <button
                type="button"
                className={styles.recBtnGhost}
                disabled={busy || refreshing}
                onClick={() => void refreshRecommendation(issueId)}
                title="Generate a fresh recommendation"
              >
                {refreshing ? "Refreshing…" : "↻ Refresh"}
              </button>
              {usable && (
                <button
                  type="button"
                  className={styles.brainstormBtn}
                  disabled={busy || refreshing}
                  onClick={() => void brainstormFromRec(issueId, d)}
                  title="Open a conversation with Donna primed on this recommendation"
                >
                  Brainstorm with Donna ↗
                </button>
              )}
            </div>
            {rec.error && <span className={styles.recInlineError}>{rec.error}</span>}
            <p className={styles.recDisclaimer}>
              Donna&apos;s draft — grounded in this contract, but yours to decide. Nothing reaches the
              export until you use it.
            </p>
          </>
        )}
      </section>
    );
  };

  // DD-68 single-issue resolution view — the surface where one issue is worked.
  // Rendered in place of the rail tabs while a drill-in is active. Top → bottom:
  // clause context (compact, jump/edit) → editable issue → Open/Closed → the F11
  // resolution card. The clause edit + status toggle reuse the cockpit's own
  // mechanisms (startEdit/saveEdit, changeStatus).
  const renderResolveView = (i: StoredIssue) => {
    const status = asStatus(i.status);
    const statusBusy = statusBusyId === i.id;
    const isCp = i.initiator === "counterparty";
    const isDonna = i.initiator === "donna";
    const clause = resolveClauseRow;
    const clauseEditing = !!clause && editing?.nodeId === clause.id;
    const clauseEditable = !!clause && clause.text.trim().length > 0 && clause.contentType !== "table";
    return (
      <div className={styles.resolve}>
        <div className={styles.resolveHead}>
          <button
            type="button"
            className={styles.backBtn}
            onClick={closeResolve}
            aria-label={resolveOrigin === "issues" ? "Back to issues" : "Back to the current clause"}
          >
            <span className={styles.backArrow} aria-hidden>
              ←
            </span>
            {resolveOrigin === "issues" ? "Issues" : "Current clause"}
          </button>
          <span
            className={[styles.who, isCp ? styles.whoCp : isDonna ? styles.whoDonna : styles.whoUs].join(" ")}
          >
            {isCp ? "Counterparty" : isDonna ? "Donna" : "Us"}
          </span>
        </div>

        <div className={styles.resolveScroll}>
          {/* F10b: a visible note when drafted chat language couldn't be applied because
              the issue's recommendation failed to load (otherwise it would silently no-op). */}
          {draftApplyError && <p className={styles.askError}>{draftApplyError}</p>}
          {/* Clause context — compact reference; clause-anchored issues carry it,
              free-floating issues show a note instead. */}
          {clause ? (
            <section className={styles.resolveClause}>
              <div className={styles.resolveClauseHead}>
                <span className={styles.anchorNum}>
                  {clause.number ? `§${clause.number}` : titleCase(clause.role)}
                </span>
                <div className={styles.resolveClauseTools}>
                  {clauseEditable && !clauseEditing && (
                    <button
                      type="button"
                      className={styles.selIcon}
                      aria-label="Edit clause text"
                      title="Edit clause"
                      onClick={() => startEdit(clause)}
                    >
                      {EditIcon}
                    </button>
                  )}
                  <button
                    type="button"
                    className={styles.resolveJump}
                    onClick={() => jumpTo(clause.id)}
                    title="Find this clause in the tree"
                  >
                    Find in tree
                  </button>
                </div>
              </div>
              {clauseEditing && editing ? (
                renderEditor({
                  draft: editing.draft,
                  saving: editing.saving,
                  error: editing.error,
                  busyLabel: "Saving…",
                  saveLabel: "Save clause",
                  onChange: (v) => setEditing((s) => (s ? { ...s, draft: v } : s)),
                  onSave: saveEdit,
                  onCancel: cancelEdit,
                })
              ) : (
                <>
                  <p className={clauseCtxOpen ? styles.resolveClauseFull : styles.resolveClauseText}>
                    {clause.text || "(no text)"}
                  </p>
                  {(clause.text?.length ?? 0) > 180 && (
                    <button
                      type="button"
                      className={styles.resolveMore}
                      onClick={() => setClauseCtxOpen((v) => !v)}
                    >
                      {clauseCtxOpen ? "Show less" : "Show full clause"}
                    </button>
                  )}
                </>
              )}
            </section>
          ) : (
            <section className={[styles.resolveClause, styles.resolveClauseFree].join(" ")}>
              <span className={styles.resolveFreeLabel}>Contract-level issue</span>
              <p className={styles.resolveFreeNote}>
                This issue isn&apos;t tied to a clause, so there&apos;s no clause to edit here. Work it
                through the positions and status below.
              </p>
            </section>
          )}

          {/* The editable issue (DD-67) — title + our/their position. */}
          <section className={styles.resolveIssue}>
            {editingId === i.id ? (
              <div className={styles.editForm}>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>Title</span>
                  <input
                    className={styles.control}
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="Issue title"
                  />
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>Our position</span>
                  <textarea
                    className={[styles.control, styles.note].join(" ")}
                    rows={5}
                    value={editOur}
                    onChange={(e) => setEditOur(e.target.value)}
                    placeholder="Our position…"
                  />
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailLabel}>Their position</span>
                  <textarea
                    className={[styles.control, styles.note].join(" ")}
                    rows={5}
                    value={editTheir}
                    onChange={(e) => setEditTheir(e.target.value)}
                    placeholder="Their position…"
                  />
                </div>
                <div className={styles.editBar}>
                  <button
                    type="button"
                    className={styles.editorSave}
                    disabled={editBusy}
                    onClick={() => saveIssueEdit(i)}
                  >
                    {editBusy ? "Saving…" : "Save"}
                  </button>
                  <button
                    type="button"
                    className={styles.editorCancel}
                    disabled={editBusy}
                    onClick={cancelIssueEdit}
                  >
                    Cancel
                  </button>
                  {editError && <span className={styles.editorError}>{editError}</span>}
                </div>
              </div>
            ) : (
              <>
                <h2 className={styles.resolveTitle}>
                  <span className={styles.resolveTitleNum}>Issue #{issueNumberById.get(i.id) ?? "—"}</span> {i.title}
                </h2>
                {i.our_position && (
                  <div className={styles.detailField}>
                    <span className={styles.detailLabel}>Our position</span>
                    <p className={styles.detailText}>{i.our_position}</p>
                  </div>
                )}
                {i.their_position && (
                  <div className={styles.detailField}>
                    <span className={styles.detailLabel}>Their position</span>
                    <p className={styles.detailText}>{i.their_position}</p>
                  </div>
                )}
                {!i.our_position && !i.their_position && (
                  <p className={styles.detailText}>No positions recorded yet — add them with Edit.</p>
                )}
                <button type="button" className={styles.editLink} onClick={() => startIssueEdit(i)}>
                  Edit issue
                </button>
              </>
            )}
          </section>

          {/* Status (DD-65). */}
          <section className={styles.resolveStatus}>
            <span className={styles.detailLabel}>Status</span>
            <div className={styles.statusToggle} role="radiogroup" aria-label="Issue status">
              {STATUS_ORDER.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={status === s}
                  disabled={statusBusy}
                  className={[
                    styles.statusToggleOption,
                    status === s
                      ? s === "open"
                        ? styles.statusToggleOpen
                        : styles.statusToggleClosed
                      : "",
                  ].join(" ")}
                  onClick={() => {
                    if (status !== s) changeStatus(i.id, s);
                  }}
                >
                  {STATUS_LABEL[s]}
                </button>
              ))}
            </div>
            {statusBusy && <span className={styles.statusBusy}>Saving…</span>}
          </section>

          {/* F11 resolution layer — Donna's live recommendation for this issue. */}
          {renderRecCard(i.id)}

          {/* DD-73: a transient note after closing a brainstorm — what was saved, or that
              nothing substantive came of it. Dismissible; auto-clears on the next open. */}
          {brainstormNotice && brainstorm === null && (
            <div
              className={
                brainstormNotice.kind === "saved" ? styles.bsNoticeSaved : styles.bsNoticeEmpty
              }
              role="status"
            >
              <span className={styles.bsNoticeText}>
                {brainstormNotice.kind === "saved"
                  ? "Saved to this issue's brainstorm history below."
                  : "Nothing substantive to save — the brainstorm was discarded."}
              </span>
              <button
                type="button"
                className={styles.bsNoticeClose}
                aria-label="Dismiss"
                onClick={() => setBrainstormNotice(null)}
              >
                ×
              </button>
            </div>
          )}

          {/* DD-77: this issue's preserved brainstorm reasoning, newest first. */}
          {renderBrainstormHistory(i.id)}
        </div>
      </div>
    );
  };

  return (
    <div className={styles.screen}>
      <header className={styles.topbar}>
        <div className={styles.identity}>
          <Link
            href="/"
            className={styles.brand}
            aria-label="donna.ai home"
            style={{ textDecoration: "none", color: "inherit" }}
          >
            donna<span className={styles.dot}>.</span>ai
          </Link>
        </div>

        <div className={styles.jump}>
          <div className={styles.jumpField}>
            <span className={styles.jumpGlyph} aria-hidden>
              §
            </span>
            <input
              ref={jumpRef}
              className={styles.jumpInput}
              value={jumpVal}
              onChange={(e) => onJumpChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onJumpEnter();
              }}
              placeholder="Jump to clause 7.2 — or type a keyword"
              aria-label="Jump to clause number or keyword"
              autoFocus
            />
            {keywordMatches.length > 0 && (
              <div className={styles.jumpResults}>
                <span className={styles.jumpCount}>
                  {keywordMatches.length} result{keywordMatches.length === 1 ? "" : "s"}
                </span>
                {keywordMatches.length > 1 && (
                  <>
                    <button
                      type="button"
                      className={styles.jumpArrow}
                      aria-label="Previous match"
                      onClick={() => stepMatch(-1)}
                    >
                      ‹
                    </button>
                    <span className={styles.jumpIndex}>
                      {matchIdx + 1} / {keywordMatches.length}
                    </span>
                    <button
                      type="button"
                      className={styles.jumpArrow}
                      aria-label="Next match"
                      onClick={() => stepMatch(1)}
                    >
                      ›
                    </button>
                  </>
                )}
              </div>
            )}
            <span className={styles.jumpKbd} aria-hidden>
              /
            </span>
          </div>
          {jumpVal.trim() && (
            <div className={styles.jumpHint}>
              {isNumericQuery(jumpVal.trim()) ? (
                jumpMatch ? (
                  <>
                    <span className={styles.jumpHintNum}>§{jumpMatch.number}</span>
                    <span className={styles.jumpHintText}>{jumpMatch.text || "(no text)"}</span>
                  </>
                ) : (
                  <span className={styles.jumpHintMiss}>No clause {jumpVal.trim()} — press Enter for the nearest</span>
                )
              ) : aiSearch.kind === "searching" ? (
                <>
                  <span className={styles.jumpHintSpinner} aria-hidden />
                  <span className={styles.jumpHintText}>Searching by meaning…</span>
                </>
              ) : aiSearch.kind === "conceptual" ? (
                <>
                  <span className={styles.jumpHintBadge}>Semantic match</span>
                  <span className={styles.jumpHintConceptual}>
                    No literal hit for “{aiSearch.query}” — closest by meaning
                  </span>
                </>
              ) : aiSearch.kind === "none" ? (
                <span className={styles.jumpHintMiss}>No match for “{aiSearch.query}”</span>
              ) : aiSearch.kind === "error" ? (
                <span className={styles.jumpHintError}>{aiSearch.message}</span>
              ) : keywordMatches.length > 0 ? (
                <>
                  <span className={[styles.jumpHintBadge, styles.jumpHintBadgeExact].join(" ")}>
                    Exact
                  </span>
                  <span className={styles.jumpHintText}>
                    {rowById.get(keywordMatches[matchIdx])?.text || "(no text)"}
                  </span>
                </>
              ) : (
                <span className={styles.jumpHintMiss}>
                  No literal match — press Enter for a semantic search
                </span>
              )}
            </div>
          )}
        </div>

        <div className={styles.right}>
          {/* F27: persistent lifecycle badge — always visible, colour-keyed by
              label. Clicking opens the lineage drawer (version/snapshot history). */}
          {lineage && (
            <div className={styles.lineageWrap} ref={lineageRef}>
              <button
                type="button"
                className={[styles.lifecycleBadge, badgeTone(lineage.badge.label)].join(" ")}
                aria-haspopup="dialog"
                aria-expanded={lineageOpen}
                title="Version history"
                onClick={() => setLineageOpen((v) => !v)}
              >
                <span className={styles.lifecycleDot} aria-hidden />
                {badgeText(lineage.badge)}
                <span className={styles.lifecycleCaret} aria-hidden>
                  ▾
                </span>
              </button>
              {lineageOpen && (
                <div className={styles.lineageDrawer} role="dialog" aria-label="Version history">
                  <div className={styles.lineageHead}>
                    <span className={styles.lineageHeadTitle}>Version history</span>
                    <button
                      type="button"
                      className={styles.lineageClose}
                      aria-label="Close version history"
                      onClick={() => setLineageOpen(false)}
                    >
                      ×
                    </button>
                  </div>

                  {/* DD-85/DD-87 delete confirm — appears once the preview loads. */}
                  {deleteVersion && (
                    <div
                      className={styles.deleteVersionPanel}
                      role="alertdialog"
                      aria-label={`Delete v${deleteVersion.version}`}
                    >
                      <p className={styles.deleteVersionTitle}>
                        Delete v{deleteVersion.version}?
                      </p>
                      {deleteVersion.preview.warnings.map((w, i) => (
                        <p key={i} className={styles.deleteVersionWarn}>
                          {w}
                        </p>
                      ))}
                      {deleteVersion.preview.will_rollback && (
                        <p className={styles.deleteVersionWarnStrong}>
                          This discards your current working-copy content and any unsent
                          edits, rolling back to v{deleteVersion.preview.rollback_to_version}.
                        </p>
                      )}
                      {deleteVersion.preview.sent_record && (
                        <p className={styles.deleteVersionWarnStrong}>
                          This erases the record of what was sent to{" "}
                          {deleteVersion.preview.sent_record.party} on{" "}
                          {deleteVersion.preview.sent_record.date} and rolls the redline
                          baseline back.
                        </p>
                      )}
                      {deleteError && (
                        <p className={styles.deleteVersionError}>{deleteError}</p>
                      )}
                      <div className={styles.deleteVersionActions}>
                        <button
                          type="button"
                          className={styles.deleteVersionPrimary}
                          disabled={deleteBusy}
                          onClick={() => void confirmDeleteVersion()}
                        >
                          {deleteBusy ? "Deleting…" : "Delete version"}
                        </button>
                        <button
                          type="button"
                          className={styles.deleteVersionSecondary}
                          disabled={deleteBusy}
                          onClick={cancelDeleteVersion}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                  {/* Preview-stage failure (404/409) — no dialog opened. */}
                  {deleteError && !deleteVersion && (
                    <p className={styles.deleteVersionError}>{deleteError}</p>
                  )}

                  {/* Working copy — pinned, never a numbered version. */}
                  <div className={[styles.lineageRow, styles.lineageWorking].join(" ")}>
                    <div className={styles.lineageRowMain}>
                      <span className={styles.lineageWorkLabel}>{lineage.working_copy.label}</span>
                      <span className={styles.lineageTagMuted}>Working copy — not a version</span>
                    </div>
                    {lineage.working_copy.diverged_since_last_send && lineage.badge.based_on && (
                      <span className={styles.lineageDrift}>
                        edited since {lineage.badge.based_on}
                      </span>
                    )}
                  </div>

                  {/* Timeline v1…vN — each row opens its snapshot read-only. */}
                  {lineage.timeline.length === 0 ? (
                    <p className={styles.lineageEmpty}>
                      No versions yet — Mark as sent to record the first.
                    </p>
                  ) : (
                    lineage.timeline.map((t) => (
                      // Row-button and Delete are SIBLINGS in a wrapper div — never
                      // nested <button>s (invalid HTML). The trash opens the
                      // DD-85/DD-87 delete preview, not the read-only snapshot view.
                      <div key={t.snapshot_id} className={styles.lineageRowWrap}>
                        <button
                          type="button"
                          className={[
                            styles.lineageRow,
                            styles.lineageRowClickable,
                            snapshotView?.snapshotId === t.snapshot_id
                              ? styles.lineageRowActive
                              : "",
                          ].join(" ")}
                          onClick={() => void openSnapshot(t)}
                        >
                          <div className={styles.lineageRowMain}>
                            <span className={styles.lineageVer}>v{t.version}</span>
                            <span className={styles.lineageDir}>
                              {t.direction === "sent" ? "→ Sent to" : "← Received from"} {t.party}
                            </span>
                            <span className={styles.lineageDate}>{lineageDate(t.created_at)}</span>
                          </div>
                          <div className={styles.lineageTags}>
                            {t.is_current_baseline && (
                              <span className={styles.lineageBaseline}>current baseline</span>
                            )}
                            {t.pointer_labels.map((p) => (
                              <span key={p} className={styles.lineageTag}>
                                {friendlyPointer(p)}
                              </span>
                            ))}
                          </div>
                        </button>
                        <button
                          type="button"
                          className={styles.lineageDelete}
                          aria-label={`Delete v${t.version}`}
                          title={`Delete v${t.version}`}
                          disabled={deletePreviewFor !== null || deleteBusy}
                          onClick={(e) => {
                            e.stopPropagation();
                            void startDeleteVersion(t);
                          }}
                        >
                          {deletePreviewFor === t.snapshot_id ? (
                            <span className={styles.lineageDeleteSpin} aria-hidden>
                              …
                            </span>
                          ) : (
                            <svg
                              width="14"
                              height="14"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden
                            >
                              <path d="M3 6h18" />
                              <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                              <path d="M10 11v6" />
                              <path d="M14 11v6" />
                            </svg>
                          )}
                        </button>
                      </div>
                    ))
                  )}

                  {/* Reserved slots — placeholders, greyed/disabled. */}
                  {lineage.reserved.map((r, i) => (
                    <div
                      key={`reserved-${i}`}
                      className={[styles.lineageRow, styles.lineageReserved].join(" ")}
                      aria-disabled
                    >
                      <div className={styles.lineageRowMain}>
                        <span className={styles.lineageDir}>{r.label}</span>
                      </div>
                      <span className={styles.lineageTagMuted}>arrives with revision import</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <a className={styles.navLink} href="/contracts">
            ← All contracts
          </a>
          <div className={styles.exportWrap} ref={exportRef}>
            <button
              type="button"
              className={[styles.exportBtn, exportOpen ? styles.exportBtnOn : ""].join(" ")}
              aria-haspopup="menu"
              aria-expanded={exportOpen}
              onClick={() => (exportOpen ? closeExport() : setExportOpen(true))}
            >
              Export{" "}
              <span className={styles.exportCaret} aria-hidden>
                ▾
              </span>
            </button>
            {exportOpen && (
              <div className={styles.exportMenu} role="menu">
                <button
                  type="button"
                  role="menuitem"
                  className={[styles.menuItem, styles.exportItem].join(" ")}
                  disabled={exportBusy === "clean"}
                  onClick={() => void runCleanCopy()}
                >
                  <span className={styles.exportItemLabel}>Clean copy</span>
                  <span className={styles.exportItemMeta}>
                    {exportBusy === "clean" ? "Exporting…" : ".docx"}
                  </span>
                </button>
                <div className={styles.exportRedline}>
                  <button
                    type="button"
                    role="menuitem"
                    className={[styles.menuItem, styles.exportItem].join(" ")}
                    disabled={exportBusy === "redline"}
                    onClick={() => void runRedline()}
                  >
                    <span className={styles.exportItemLabel}>Redline</span>
                    <span className={styles.exportItemMeta}>
                      {exportBusy === "redline" ? "Exporting…" : ".docx"}
                    </span>
                  </button>
                  {redlineNoBaseline && (
                    <p className={styles.exportRedlineHint}>
                      Available after you Mark as sent — that sets the baseline.
                    </p>
                  )}
                </div>
                <div className={styles.menuSep} />
                <button
                  type="button"
                  role="menuitem"
                  className={[styles.menuItem, styles.exportItem].join(" ")}
                  disabled={exportBusy === "issues"}
                  onClick={() => void runIssueList()}
                >
                  <span className={styles.exportItemLabel}>Issue list</span>
                  <span className={styles.exportItemMeta}>
                    {exportBusy === "issues" ? "Exporting…" : ".docx"}
                  </span>
                </button>
                {exportError && <p className={styles.exportError}>{exportError}</p>}
              </div>
            )}
          </div>

          {/* Import revision (F03b, Mode B): upload a counterparty/legal revision →
              F03c review. Hidden input shared by both source choices. */}
          <input
            ref={revFileRef}
            type="file"
            accept=".docx"
            style={{ display: "none" }}
            onChange={onRevisionFile}
            aria-hidden
          />
          <div className={styles.markWrap} ref={importRef}>
            <button
              type="button"
              className={[styles.exportBtn, importOpen ? styles.exportBtnOn : ""].join(" ")}
              aria-haspopup="menu"
              aria-expanded={importOpen}
              disabled={importBusy}
              onClick={() => (importOpen ? closeImport() : setImportOpen(true))}
            >
              {importBusy ? "Importing…" : "Import new version"}{" "}
              <span className={styles.exportCaret} aria-hidden>
                ▾
              </span>
            </button>
            {importOpen && (
              <div className={styles.exportMenu} role="menu">
                <div className={styles.exportSubHead}>Whose revision is this?</div>
                {(["counterparty", "legal"] as RevisionSource[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="menuitem"
                    className={[styles.menuItem, styles.exportItem].join(" ")}
                    disabled={importBusy}
                    onClick={() => armImport(s)}
                  >
                    <span className={styles.exportItemLabel}>
                      {s === "counterparty" ? "From counterparty" : "From legal"}
                    </span>
                    <span className={styles.exportItemMeta}>
                      {importBusy && importSource === s ? "Importing…" : ".docx"}
                    </span>
                  </button>
                ))}
                {importBusy && (
                  <div className={styles.progressTrack} style={{ width: "auto", margin: "6px 8px" }}>
                    <div className={styles.progressBar} />
                  </div>
                )}
                {importError && <p className={styles.exportError}>{importError}</p>}
              </div>
            )}
          </div>

          {/* Mark as sent (DD-71): the boundary event, separate from Export. */}
          <div className={styles.markWrap} ref={markRef}>
            <button
              type="button"
              className={[styles.markBtn, markOpen ? styles.markBtnOn : ""].join(" ")}
              aria-haspopup="menu"
              aria-expanded={markOpen}
              onClick={() => (markOpen ? closeMark() : (setMarkDone(null), setMarkOpen(true)))}
            >
              Mark as sent{" "}
              <span className={styles.exportCaret} aria-hidden>
                ▾
              </span>
            </button>
            {markOpen && (
              <div className={styles.exportMenu} role="menu">
                {markDrift ? (
                  <div className={styles.markDrift} role="alert">
                    <p className={styles.markDriftText}>
                      You&apos;ve edited since your last export
                      {markDrift.lastExportAt
                        ? ` (${new Date(markDrift.lastExportAt).toLocaleString()})`
                        : ""}
                      . Marking now records your <strong>current</strong> version (v
                      {markDrift.version}) as sent to {MARK_LABEL[markDrift.recipient]}.
                    </p>
                    <div className={styles.markDriftActions}>
                      <button
                        type="button"
                        className={styles.markDriftPrimary}
                        disabled={markBusy !== null}
                        onClick={() => void runMarkSent(markDrift.recipient, true)}
                      >
                        {markBusy ? "Marking…" : "Mark anyway"}
                      </button>
                      <button
                        type="button"
                        className={styles.markDriftSecondary}
                        disabled={markBusy !== null || exportBusy !== null}
                        onClick={() => void reExportFromDrift()}
                      >
                        Re-export
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {(["counterparty", "legal", "both"] as MarkSentRecipient[]).map((r) => (
                      <button
                        key={r}
                        type="button"
                        role="menuitem"
                        className={[styles.menuItem, styles.exportItem].join(" ")}
                        disabled={markBusy !== null}
                        onClick={() => void runMarkSent(r)}
                      >
                        <span className={styles.exportItemLabel}>{MARK_LABEL[r]}</span>
                        <span className={styles.exportItemMeta}>
                          {markBusy === r ? "Marking…" : "→"}
                        </span>
                      </button>
                    ))}
                  </>
                )}
                {markError && <p className={styles.exportError}>{markError}</p>}
              </div>
            )}
            {/* The transient "Sent to…" toast is replaced by the persistent
                lifecycle badge (F27), which the Mark-as-sent refreshes. */}
          </div>
        </div>
      </header>

      {/* F03c: persistent Resume affordance. While a 'reviewing' session is open the
          operator can back out of the review page; without this strip there's no way
          back in (and a re-import is blocked, 409). Visible until the session applies. */}
      {openRevision && (
        <div className={styles.resumeBar} role="status">
          <span className={styles.resumeDot} aria-hidden />
          <div className={styles.resumeText}>
            <span className={styles.resumeTitle}>Revision review in progress</span>
            <span className={styles.resumeMeta}>
              {openRevision.source === "legal_team" ? "From legal" : "From counterparty"}
              {lineage?.badge.version != null ? ` · v${lineage.badge.version}` : ""} ·{" "}
              {openRevision.pending_changes} pending
            </span>
          </div>
          <button
            type="button"
            className={styles.resumeBtn}
            onClick={() => router.push(`/contracts/${id}/revisions/${openRevision.id}`)}
          >
            Resume review
          </button>
        </div>
      )}

      {state.kind === "loading" ? (
        <div className={styles.center}>
          <div className={styles.progressTrack} role="progressbar" aria-label="Loading contract">
            <div className={styles.progressBar} />
          </div>
          <p className={styles.phase}>{state.phase}…</p>
        </div>
      ) : state.kind === "error" ? (
        <div className={styles.center}>
          <p className={styles.centerTitle}>Couldn&apos;t open this contract</p>
          <p className={styles.error}>{state.message}</p>
          <button className={styles.retry} onClick={() => setReloadKey((k) => k + 1)}>
            Try again
          </button>
        </div>
      ) : rows.length === 0 ? (
        <div className={styles.center}>
          <p className={styles.centerTitle}>No clauses yet</p>
          <p className={styles.centerHint}>
            This contract has no committed clause tree — import it first, then come back to run the call.
          </p>
          <a className={styles.retry} href="/">
            Go to import
          </a>
        </div>
      ) : (
        <>
        <div className={styles.panels}>
          <section className={styles.tree}>
            <div className={styles.panelHead}>
              Clauses
              <span className={styles.panelHint}>
                {snapshotView
                  ? `viewing v${snapshotView.version} — read-only`
                  : rearranging
                    ? "drag to reorder or nest"
                    : "click to anchor an issue · press / to jump"}
              </span>
              {!snapshotView && presentRegions.size > 0 && (
                <div className={styles.headTools}>
                  <button
                    className={[styles.rearrangeBtn, rearranging ? styles.rearrangeOn : ""].join(" ")}
                    aria-pressed={rearranging}
                    onClick={() => setRearranging((v) => !v)}
                  >
                    {rearranging ? "✓ Done" : "⇅ Rearrange"}
                  </button>
                  {!rearranging && (
                    <button className={styles.collapseAll} onClick={toggleCollapseAll}>
                      {allCollapsed ? "⊞ Expand all" : "⊟ Collapse all"}
                    </button>
                  )}
                </div>
              )}
            </div>
            {/* Read mode: three region sections in document order, each with a
                clickable header. Rearrange mode swaps the body (clause) tree for
                the lazily-loaded sortable RearrangeTree; front/back-matter are out
                of drag scope for v1 and simply not shown while rearranging.
                Rearrange is fed `visibleBody` (collapse-respecting), so a collapsed
                section is ONE draggable row whose hidden sub-tree moves with it —
                "Collapse all" then Rearrange gives a short top-level list. The
                twirl toggles the same `collapsed` set, so collapse state is shared
                with the read tree and survives leaving Rearrange. */}
            {/* F27: read-only banner shown while viewing a past snapshot. */}
            {snapshotView && (
              <div className={styles.snapshotBanner} role="status">
                <span className={styles.snapshotBannerText}>
                  Viewing v{snapshotView.version} ({snapshotView.direction} {snapshotView.party},{" "}
                  {snapshotView.date}) — read-only
                </span>
                <button
                  type="button"
                  className={styles.snapshotReturn}
                  onClick={closeSnapshot}
                >
                  Return to working copy
                </button>
              </div>
            )}
            <div className={styles.rows}>
              {snapshotView ? (
                snapshotView.loading ? (
                  <p className={styles.snapshotLoading}>Loading v{snapshotView.version}…</p>
                ) : snapshotView.error ? (
                  <p className={styles.snapshotError}>{snapshotView.error}</p>
                ) : (
                  snapshotView.rows.map(renderSnapshotRow)
                )
              ) : rearranging ? (
                <RearrangeTree
                  contractId={id}
                  rows={visibleBody}
                  parentIds={allParents}
                  collapsed={collapsed}
                  onToggleCollapse={toggleCollapse}
                  selectedId={selectedId}
                  flashId={flashId}
                  onSelect={setSelectedId}
                  onMoved={handleNodeMoved}
                />
              ) : (
                <>
                  {renderRegion("front", visibleFront)}
                  {renderRegion("body", visibleBody)}
                  {renderRegion("back", visibleBack)}
                </>
              )}
            </div>
          </section>

          <section
            className={[styles.rail, !resolvingIssue && railTab === "donna" ? styles.railChat : ""].join(" ")}
          >
            {resolvingIssue ? (
              renderResolveView(resolvingIssue)
            ) : (
            <>
            <div className={styles.panelHead}>
              <div className={styles.railTabs} role="tablist" aria-label="View issues, the current clause, or ask Donna">
                <button
                  type="button"
                  role="tab"
                  aria-selected={railTab === "issues"}
                  className={[styles.railTab, railTab === "issues" ? styles.railTabActive : ""].join(" ")}
                  onClick={() => setRailTab("issues")}
                >
                  Issues
                  {openIssueCount > 0 && <span className={styles.railTabCount}>{openIssueCount}</span>}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={railTab === "clause"}
                  className={[styles.railTab, railTab === "clause" ? styles.railTabActive : ""].join(" ")}
                  onClick={() => setRailTab("clause")}
                >
                  Current Clause
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={railTab === "donna"}
                  className={[styles.railTab, railTab === "donna" ? styles.railTabActive : ""].join(" ")}
                  onClick={() => setRailTab("donna")}
                >
                  Donna
                </button>
              </div>
            </div>

            {railTab === "clause" ? (
              !selectedRow ? (
                <div className={styles.clauseEmpty}>
                  <p className={styles.clauseEmptyTitle}>Select a clause from the tree</p>
                  <p className={styles.clauseEmptyLead}>
                    Pick a clause on the left to read it, raise an issue on it, and see what&apos;s
                    already contested there.
                  </p>
                </div>
              ) : (
              <>
                <div className={styles.selCard}>
              {selectedRow ? (
                <>
                  <div className={styles.selHead}>
                    <span className={styles.anchorNum}>
                      {selectedRow.number ? `§${selectedRow.number}` : titleCase(selectedRow.role)}
                    </span>
                    {!editingSelected && !deletingSelected && (
                      <div className={styles.selActions}>
                        {canEditSelected && (
                          <button
                            type="button"
                            className={styles.selIcon}
                            aria-label="Edit clause text"
                            title="Edit text"
                            onClick={() => startEdit(selectedRow)}
                          >
                            {EditIcon}
                          </button>
                        )}
                        <button
                          type="button"
                          className={[styles.selIcon, styles.selIconDanger].join(" ")}
                          aria-label="Delete clause"
                          title="Delete clause"
                          onClick={() => startDelete(selectedRow)}
                        >
                          {DeleteIcon}
                        </button>
                      </div>
                    )}
                  </div>

                  {editingSelected && editing ? (
                    <div className={styles.selBody}>
                      {renderEditor({
                        draft: editing.draft,
                        saving: editing.saving,
                        error: editing.error,
                        busyLabel: "Saving…",
                        saveLabel: "Save",
                        onChange: (v) => setEditing((s) => (s ? { ...s, draft: v } : s)),
                        onSave: saveEdit,
                        onCancel: cancelEdit,
                      })}
                    </div>
                  ) : deletingSelected && deleteState ? (
                    <div className={styles.selBody}>
                      <div className={styles.confirm}>
                        <p className={styles.confirmText}>
                          {deleteState.descendantCount > 0
                            ? `Delete this clause and its ${deleteState.descendantCount} sub-clause${
                                deleteState.descendantCount === 1 ? "" : "s"
                              }? This can't be undone here.`
                            : "Delete this clause? This can't be undone here."}
                        </p>
                        <div className={styles.confirmBar}>
                          <button
                            type="button"
                            className={styles.confirmDelete}
                            disabled={deleteState.deleting}
                            onClick={() => void confirmDelete()}
                          >
                            {deleteState.deleting ? "Deleting…" : "Delete"}
                          </button>
                          <button
                            type="button"
                            className={styles.confirmCancel}
                            disabled={deleteState.deleting}
                            onClick={() => setDeleteState(null)}
                          >
                            Cancel
                          </button>
                        </div>
                        {deleteState.error && <p className={styles.confirmError}>{deleteState.error}</p>}
                      </div>
                    </div>
                  ) : (
                    <p className={styles.selPreview}>{selectedRow.text || "(no text)"}</p>
                  )}
                  {!editingSelected && !deletingSelected && (
                    <button
                      type="button"
                      className={styles.askClauseBtn}
                      onClick={() => askDonnaAboutClause(selectedRow.id)}
                      title="Ask Donna for help grounded on this clause"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                        <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 3v-3H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
                        <path d="M9 10h6M9 13h4" />
                      </svg>
                      Ask Donna about this clause
                    </button>
                  )}
                </>
              ) : (
                <p className={styles.selHint}>
                  Select a clause in the tree to raise an issue — or raise a contract-level issue below.
                </p>
              )}
            </div>

            <form className={styles.raise} onSubmit={onSubmit}>
              <p className={styles.raiseTitle}>Raise issue</p>
              <div className={styles.anchor}>
                {selectedRow ? (
                  <>
                    <span className={styles.anchorNum}>
                      {selectedRow.number ? `§${selectedRow.number}` : titleCase(selectedRow.role)}
                    </span>
                    <span className={styles.anchorText}>{selectedRow.text || "(no text)"}</span>
                  </>
                ) : (
                  <span className={styles.anchorNone}>No clause selected — raised against the contract</span>
                )}
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="issue-description">
                  Description
                </label>
                <textarea
                  id="issue-description"
                  className={[styles.control, styles.note].join(" ")}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What's the issue?"
                  required
                />
              </div>

              <div className={styles.field}>
                <span className={styles.fieldLabel}>Who raised it</span>
                <div className={styles.segment} role="group" aria-label="Who raised this issue">
                  <button
                    type="button"
                    className={[styles.segmentBtn, initiator === "operator" ? styles.segmentUs : ""].join(" ")}
                    aria-pressed={initiator === "operator"}
                    onClick={() => setInitiator("operator")}
                  >
                    Us
                  </button>
                  <button
                    type="button"
                    className={[styles.segmentBtn, initiator === "counterparty" ? styles.segmentCp : ""].join(" ")}
                    aria-pressed={initiator === "counterparty"}
                    onClick={() => setInitiator("counterparty")}
                  >
                    Counterparty
                  </button>
                </div>
              </div>

              <button className={styles.submit} type="submit" disabled={!description.trim() || submitting}>
                {submitting ? "Raising…" : "Raise issue"}
              </button>
              {formError && <p className={styles.formError}>{formError}</p>}
            </form>

            <div className={styles.clauseIssues}>
              <div className={styles.issuesHead}>
                <span>Open issues on this clause</span>
                {clauseOpenIssues.length > 0 && (
                  <span className={styles.issuesCount}>{clauseOpenIssues.length}</span>
                )}
              </div>
              {clauseOpenIssues.length === 0 ? (
                <p className={styles.centerHint} style={{ textAlign: "left", padding: "4px 2px" }}>
                  No open issues on this clause yet. Raise one above.
                </p>
              ) : (
                clauseOpenIssues.map((i) => renderIssueCard(i, "clause"))
              )}
            </div>
              </>
              )
            ) : railTab === "issues" ? (
            <div className={styles.issues}>
              <div className={styles.issuesHead}>
                <span>Open issues{openIssues.length > 0 ? ` (${openIssues.length})` : ""}</span>
                <button
                  type="button"
                  className={styles.newIssueBtn}
                  aria-expanded={freeFormOpen}
                  onClick={() => setFreeFormOpen((v) => !v)}
                >
                  {freeFormOpen ? "Cancel" : "+ new issue"}
                </button>
              </div>

              {freeFormOpen && (
                <form className={styles.raise} onSubmit={submitFreeIssue}>
                  <p className={styles.raiseTitle}>Contract-level issue</p>
                  <div className={styles.anchor}>
                    <span className={styles.anchorNone}>
                      Not tied to a clause — raised against the contract
                    </span>
                  </div>
                  <div className={styles.field}>
                    <label className={styles.fieldLabel} htmlFor="free-issue-description">
                      Description
                    </label>
                    <textarea
                      id="free-issue-description"
                      className={[styles.control, styles.note].join(" ")}
                      value={freeDescription}
                      onChange={(e) => setFreeDescription(e.target.value)}
                      placeholder="What's the issue?"
                      required
                    />
                  </div>
                  <div className={styles.field}>
                    <span className={styles.fieldLabel}>Who raised it</span>
                    <div className={styles.segment} role="group" aria-label="Who raised this issue">
                      <button
                        type="button"
                        className={[styles.segmentBtn, freeInitiator === "operator" ? styles.segmentUs : ""].join(" ")}
                        aria-pressed={freeInitiator === "operator"}
                        onClick={() => setFreeInitiator("operator")}
                      >
                        Us
                      </button>
                      <button
                        type="button"
                        className={[styles.segmentBtn, freeInitiator === "counterparty" ? styles.segmentCp : ""].join(" ")}
                        aria-pressed={freeInitiator === "counterparty"}
                        onClick={() => setFreeInitiator("counterparty")}
                      >
                        Counterparty
                      </button>
                    </div>
                  </div>
                  <button
                    className={styles.submit}
                    type="submit"
                    disabled={!freeDescription.trim() || freeSubmitting}
                  >
                    {freeSubmitting ? "Raising…" : "Raise issue"}
                  </button>
                  {freeError && <p className={styles.formError}>{freeError}</p>}
                </form>
              )}

              {openIssues.length === 0 ? (
                <p className={styles.centerHint} style={{ textAlign: "left", padding: "4px 2px" }}>
                  No open issues. Select a clause to raise one, or add a contract-level issue above.
                </p>
              ) : (
                openIssues.map((i) => renderIssueCard(i, "issues"))
              )}

              {closedIssues.length > 0 && (
                <div className={styles.closedSection}>
                  <button
                    type="button"
                    className={styles.closedToggle}
                    aria-expanded={showClosed}
                    onClick={() => setShowClosed((v) => !v)}
                  >
                    <span className={styles.closedCaret} aria-hidden>
                      {showClosed ? "▾" : "▸"}
                    </span>
                    Closed ({closedIssues.length})
                  </button>
                  {showClosed && closedIssues.map((i) => renderIssueCard(i, "issues"))}
                </div>
              )}
            </div>
            ) : (
              <div className={styles.donnaPanel}>
                <div className={styles.donnaScroll} ref={donnaScrollRef}>
                  {donnaLoading ? (
                    <div className={styles.donnaLoad}>
                      <div className={styles.progressTrack} role="progressbar" aria-label="Opening thread">
                        <div className={styles.progressBar} />
                      </div>
                      <p className={styles.phase}>Opening your thread with Donna…</p>
                    </div>
                  ) : donnaError && !donnaMessages ? (
                    <div className={styles.donnaLoad}>
                      <p className={styles.threadError}>{donnaError}</p>
                      <button type="button" className={styles.retry} onClick={() => setDonnaError(null)}>
                        Try again
                      </button>
                    </div>
                  ) : (!donnaMessages || donnaMessages.length === 0) && !asking ? (
                    <div className={styles.donnaEmpty}>
                      <p className={styles.donnaEmptyTitle}>Ask Donna about this contract</p>
                      <p className={styles.donnaEmptyLead}>
                        She reads the clauses and the open issues, and points you to where the answer
                        lives. Donna explains — she doesn&apos;t give legal advice.
                      </p>
                      <div className={styles.exampleChips}>
                        {DONNA_EXAMPLES.map((q) => (
                          <button
                            key={q}
                            type="button"
                            className={styles.exampleChip}
                            onClick={() => void sendDonna(q)}
                          >
                            {q}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <>
                      {(donnaMessages ?? []).map((m, i) =>
                        m.role === "user" ? (
                          <div key={i} className={[styles.msg, styles.msgUser].join(" ")}>
                            <div className={styles.bubbleUser}>{m.content}</div>
                          </div>
                        ) : (
                          <div key={i} className={[styles.msg, styles.msgDonna].join(" ")}>
                            <span className={styles.donnaEyebrow}>
                              Donna
                              {/* live mode tags (F10b) */}
                              {m.mode === "advise" && (
                                <span className={styles.adviseTag}>recommendation</span>
                              )}
                              {m.mode === "draft" && <span className={styles.draftTag}>draft</span>}
                              {m.mode === "legal_referral" && (
                                <span className={styles.scopeTag}>get a lawyer</span>
                              )}
                              {m.mode === "need_context" && (
                                <span className={styles.needTag}>needs a clause</span>
                              )}
                              {/* persisted-kind tags (reloaded thread turns) */}
                              {!m.mode && m.kind === "deflected" && (
                                <span className={styles.scopeTag}>scoped to reading</span>
                              )}
                              {!m.mode && m.kind === "not_found" && (
                                <span className={styles.notFoundTag}>not in this contract</span>
                              )}
                            </span>
                            <div className={[styles.bubble, donnaBubbleClass(m)].join(" ")}>
                              {m.citations && m.citations.length > 0 && (
                                <div className={[styles.cites, styles.citesTop].join(" ")}>
                                  {m.citations.map((c, ci) =>
                                    c.kind === "clause" ? (
                                      <button
                                        key={ci}
                                        type="button"
                                        className={styles.cite}
                                        title="Jump to this clause"
                                        onClick={() => jumpTo(c.nodeId)}
                                      >
                                        <span className={styles.citeArrow} aria-hidden>
                                          ↳
                                        </span>
                                        {c.label}
                                      </button>
                                    ) : (
                                      <span key={ci} className={[styles.cite, styles.citeIssue].join(" ")}>
                                        {c.label}
                                      </span>
                                    ),
                                  )}
                                </div>
                              )}
                              <div className={styles.bubbleText}>{renderDonnaMarkdown(m.content)}</div>

                              {/* draft mode: the clause language to apply, set apart, with a
                                  "Use this language" action that routes into an existing
                                  confirmed apply surface (issue rec-edit or clause editor). */}
                              {m.mode === "draft" && m.draftLanguage?.trim() && (
                                <div className={styles.draftTurn}>
                                  <span className={styles.draftTurnLabel}>Draft language</span>
                                  <p className={[styles.detailText, styles.recLanguage].join(" ")}>
                                    {m.draftLanguage}
                                  </p>
                                  <div className={styles.draftTurnBar}>
                                    {donnaContext?.issueId ||
                                    (donnaContext?.nodeIds.length ?? 0) > 0 ? (
                                      <button
                                        type="button"
                                        className={styles.recBtnPrimary}
                                        onClick={() => void applyDraftLanguage(m.draftLanguage ?? "")}
                                      >
                                        Use this language
                                      </button>
                                    ) : (
                                      <>
                                        <button
                                          type="button"
                                          className={styles.recBtnGhost}
                                          onClick={() => void applyDraftLanguage(m.draftLanguage ?? "")}
                                        >
                                          {copiedDraft ? "Copied ✓" : "Copy"}
                                        </button>
                                        <span className={styles.draftHint}>
                                          Select a clause or open an issue to apply it directly.
                                        </span>
                                      </>
                                    )}
                                  </div>
                                </div>
                              )}

                              {/* legal_referral: Donna won't opine — route to a lawyer. */}
                              {m.mode === "legal_referral" && (
                                <p className={styles.deflectFoot}>
                                  This calls for a legal judgment — get a lawyer. Donna won&apos;t
                                  opine on it.
                                </p>
                              )}

                              {/* deflected (reloaded F10 turn) keeps its original footer. */}
                              {!m.mode && m.kind === "deflected" && (
                                <p className={styles.deflectFoot}>
                                  For a position or advice, raise an issue — or get a lawyer for a
                                  legal judgment.
                                </p>
                              )}
                            </div>
                          </div>
                        ),
                      )}
                      {asking && (
                        <div className={[styles.msg, styles.msgDonna].join(" ")}>
                          <span className={styles.donnaEyebrow}>Donna</span>
                          <div className={[styles.bubble, styles.bubbleThinking].join(" ")}>
                            <span className={styles.thinkingDots} aria-hidden>
                              <i />
                              <i />
                              <i />
                            </span>
                            <span className={styles.thinkingLabel}>{DONNA_PHASES[phaseIdx]}</span>
                          </div>
                        </div>
                      )}
                      {askError && <p className={styles.askError}>{askError}</p>}
                    </>
                  )}
                </div>

                {donnaMessages && donnaMessages.length > 0 && (
                  <div className={styles.clearFloat}>
                    <button
                      type="button"
                      className={styles.clearFloatBtn}
                      title="Clear the view — Donna keeps the conversation"
                      onClick={clearDonnaView}
                    >
                      Clear chat
                    </button>
                  </div>
                )}

                {/* F10b context chip — the clause/issue Donna is grounded on. [×]
                    clears it, reverting to open read-and-explain. */}
                {donnaContext && (
                  <div className={styles.ctxChip}>
                    <span className={styles.ctxChipLabel}>Discussing</span>
                    <span className={styles.ctxChipBody}>{donnaContextLabel}</span>
                    <button
                      type="button"
                      className={styles.ctxChipClear}
                      aria-label="Clear context — go back to open questions"
                      title="Clear context"
                      onClick={() => setDonnaContext(null)}
                    >
                      ×
                    </button>
                  </div>
                )}

                <form
                  className={styles.composer}
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendDonna(donnaInput);
                  }}
                >
                  <input
                    ref={donnaInputRef}
                    className={styles.composerInput}
                    value={donnaInput}
                    onChange={(e) => setDonnaInput(e.target.value)}
                    placeholder={
                      donnaContext
                        ? `Ask about ${donnaContextLabel}…`
                        : "Ask about this contract…"
                    }
                    aria-label="Ask Donna about this contract"
                    disabled={donnaLoading}
                  />
                  <button
                    type="submit"
                    className={styles.composerSend}
                    aria-label="Send question"
                    disabled={!donnaInput.trim() || asking || donnaLoading}
                  >
                    ↵
                  </button>
                </form>
                <p className={styles.donnaGuard}>
                  {donnaContext
                    ? "Donna's advising on your selected context — grounded in this contract, but your call to apply."
                    : "Donna reads & explains this contract — give her a clause or issue for advice."}
                </p>
              </div>
            )}
            </>
            )}
          </section>
        </div>

        {/* Persistent "ask Donna" affordance: a floating chat button anchored to the
            screen's bottom-right. It opens Donna's tab and focuses her composer. Hidden
            while already on Donna's tab — there the composer's own send button owns this
            corner, so the FAB would clash + duplicate it. */}
        {railTab !== "donna" && !resolvingId && (
          <button
            type="button"
            className={styles.donnaFab}
            aria-label="Ask Donna about this contract"
            title="Ask Donna"
            onClick={openDonna}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 3v-3H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
              <path d="M9 10h6M9 13h4" />
            </svg>
          </button>
        )}
        </>
      )}

      {termPopover && (
        <div
          className={styles.termPopover}
          style={{ top: termPopover.top, left: termPopover.left }}
          role="tooltip"
        >
          <span className={styles.termPopoverTerm}>{termPopover.entry.term}</span>
          {termPopover.entry.definition ? (
            <span className={styles.termPopoverDef}>{termPopover.entry.definition}</span>
          ) : (
            <span className={styles.termPopoverEmpty}>Defined term — no definition captured</span>
          )}
        </div>
      )}

      {/* F10b / DD-73 / DD-77 — Brainstorm overlay. Ephemeral by construction: the transcript
          lives only in `brainstorm` state, never persisted. The scrim dims (but doesn't hide)
          the document tree so citation chips still jump+flash behind it; closing only happens
          on an explicit action, never a scrim click, so the exploration can't be lost by
          accident. Reuses the Donna-chat bubble/citation/composer language. */}
      {brainstorm && (
        <div className={styles.bsScrim}>
          <section
            className={styles.bsPanel}
            role="dialog"
            aria-modal="true"
            aria-label={`Brainstorm issue #${issueNumberById.get(brainstorm.issueId) ?? "—"} with Donna`}
          >
            <header className={styles.bsHead}>
              <div className={styles.bsHeadTitle}>
                <span className={styles.bsHeadMark} aria-hidden>
                  ✦
                </span>
                <span>
                  Brainstorm
                  <span className={styles.bsHeadIssue}>
                    Issue #{issueNumberById.get(brainstorm.issueId) ?? "—"}
                  </span>
                </span>
              </div>
              <button
                type="button"
                className={styles.bsHeadClose}
                aria-label="Close and save summary"
                title="Close & save summary"
                disabled={brainstormClosing}
                onClick={() => void closeBrainstormOverlay()}
              >
                ×
              </button>
            </header>

            <div className={styles.bsScroll} ref={brainstormScrollRef}>
              {brainstorm.transcript.map((m, i) =>
                m.role === "user" ? (
                  <div key={i} className={[styles.msg, styles.msgUser].join(" ")}>
                    <div className={styles.bubbleUser}>{m.content}</div>
                  </div>
                ) : (
                  <div key={i} className={[styles.msg, styles.msgDonna].join(" ")}>
                    <span className={styles.donnaEyebrow}>
                      Donna
                      {m.mode === "advise" && <span className={styles.adviseTag}>recommendation</span>}
                    </span>
                    <div className={[styles.bubble, donnaBubbleClass(m)].join(" ")}>
                      {m.citations && m.citations.length > 0 && (
                        <div className={[styles.cites, styles.citesTop].join(" ")}>
                          {m.citations.map((c, ci) =>
                            c.kind === "clause" ? (
                              <button
                                key={ci}
                                type="button"
                                className={styles.cite}
                                title="Jump to this clause"
                                onClick={() => jumpTo(c.nodeId)}
                              >
                                <span className={styles.citeArrow} aria-hidden>
                                  ↳
                                </span>
                                {c.label}
                              </button>
                            ) : (
                              <span key={ci} className={[styles.cite, styles.citeIssue].join(" ")}>
                                {c.label}
                              </span>
                            ),
                          )}
                        </div>
                      )}
                      <div className={styles.bubbleText}>{renderDonnaMarkdown(m.content)}</div>
                    </div>
                  </div>
                ),
              )}
              {brainstormBusy && (
                <div className={[styles.msg, styles.msgDonna].join(" ")}>
                  <span className={styles.donnaEyebrow}>Donna</span>
                  <div className={[styles.bubble, styles.bubbleThinking].join(" ")}>
                    <span className={styles.thinkingDots} aria-hidden>
                      <i />
                      <i />
                      <i />
                    </span>
                    <span className={styles.thinkingLabel}>Donna&apos;s thinking it through…</span>
                  </div>
                </div>
              )}
              {brainstormError && <p className={styles.askError}>{brainstormError}</p>}
            </div>

            <form
              className={styles.composer}
              onSubmit={(e) => {
                e.preventDefault();
                void sendBrainstorm(brainstormInput);
              }}
            >
              <input
                ref={brainstormInputRef}
                className={styles.composerInput}
                value={brainstormInput}
                onChange={(e) => setBrainstormInput(e.target.value)}
                placeholder="Push back, ask for a sharper line, try another angle…"
                aria-label="Brainstorm with Donna"
                disabled={brainstormClosing}
              />
              <button
                type="submit"
                className={styles.composerSend}
                aria-label="Send"
                disabled={!brainstormInput.trim() || brainstormBusy || brainstormClosing}
              >
                ↵
              </button>
            </form>

            <div className={styles.bsFoot}>
              {brainstormClosing ? (
                <div className={styles.bsClosing}>
                  <div className={styles.progressTrack} role="progressbar" aria-label="Saving summary">
                    <div className={styles.progressBar} />
                  </div>
                  <span className={styles.bsClosingLabel}>Distilling the summary…</span>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    className={styles.bsCloseBtn}
                    onClick={() => void closeBrainstormOverlay()}
                  >
                    Close &amp; save summary
                  </button>
                  <p className={styles.bsFootNote}>
                    This conversation is scratch — it isn&apos;t saved. On close, Donna keeps a short
                    summary on the issue; the back-and-forth is discarded.
                  </p>
                </>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
