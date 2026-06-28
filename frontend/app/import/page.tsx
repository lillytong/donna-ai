"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./review.module.css";
import ContextStep, { type ContractContext } from "./ContextStep";
import ImportTopBar, { type ImportStep } from "./ImportTopBar";
import { deriveNumbers, deriveParents } from "../lib/numbering";
import { renderRich } from "../lib/richText";
import {
  commitTree,
  previewDocx,
  type ApiCandidateNode,
  type ImportResult,
  type NodeRow,
  type Role,
  type TrackedChangeReport,
} from "../lib/api";

interface Row {
  index: number;
  number: string;
  text: string;
  depth: number;
  typeLabel: string;
  uncertain: boolean;
  role: Role;
  hasPlaceholder: boolean;
  // The original parsed node, kept verbatim so commit can send faithful NodeRows
  // (body / table_data / plain_text / heading) — the display fields above are only
  // the editable overlay and drop most of the persistable payload.
  node: ApiCandidateNode;
}

// Content type is heading / body / table — a node's *structural kind*, distinct
// from its role (appendix is a role, set via the role selector, never a type).
const TYPE_CYCLE = ["Heading", "Body", "Table"];

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

const ROLE_LABEL: Record<Role, string> = {
  title: "Title",
  date: "Date",
  parties: "Parties",
  recital: "Recital",
  agreement_statement: "Agreement statement",
  clause: "Clause",
  appendix: "Appendix",
  appendix_title: "Appendix title",
  signature_block: "Signature block",
  drafting_note: "Drafting note",
};

// Insertion order = front-matter → clause → back-matter → note; drives both the
// per-node role selector and the bulk role picker.
const ROLE_OPTIONS = Object.keys(ROLE_LABEL) as Role[];

function toRow(n: ApiCandidateNode): Row {
  const text = n.heading ?? n.body ?? n.plain_text ?? "";
  const typeLabel = n.content_type === "table" ? "Table" : n.heading && !n.body ? "Heading" : "Body";
  return {
    index: n.index,
    number: n.number,
    text,
    depth: n.depth,
    typeLabel,
    uncertain: n.uncertain,
    role: n.role,
    hasPlaceholder: n.has_placeholder,
    node: n,
  };
}

// Map the operator-corrected rows back to persistable NodeRows (CommitRequest).
// Full node data comes from each row's retained original node; parent_index and
// order_index are re-derived from the (corrected) depth sequence so promote/demote
// edits persist structurally (mirrors backend tree_builder; lossless for untouched
// nodes). Clause numbers are derived on render (DD-02) and never stored.
//
// SEQUENCE comes from the rows ARRAY ORDER — the same order that drives rendering —
// not from a sort on `index`. `index` stays each node's stable identity (it maps to
// its source block and is the value written to parent_index); array position now
// determines both parent_index (via deriveParents) and order_index. This is what
// makes a Move ↑/↓ reorder persist. INVARIANT: a freshly parsed, untouched import
// already arrives in index order, so array order === index order and this is a
// no-op — deriveParents reproduces the backend parent_index for every node exactly,
// identical to the prior sort-by-index behavior.
function buildCommitNodes(rows: Row[]): NodeRow[] {
  const ordered = rows;
  const parents = deriveParents(ordered.map((r) => ({ index: r.index, depth: r.depth })));
  const slot = new Map<number | null, number>();
  return ordered.map((r, i) => {
    const parentIndex = parents[i];
    const order = (slot.get(parentIndex) ?? 0) + 1;
    slot.set(parentIndex, order);
    const n = r.node;
    // Content type follows the operator's (corrected) typeLabel, not the original
    // parse — so a heading↔body re-type round-trips to commit (the text moves
    // between the heading and body fields). A Table keeps its structured cells;
    // a table re-typed to prose falls back to its flattened plain_text.
    if (r.typeLabel === "Table") {
      return {
        index: r.index,
        parent_index: parentIndex,
        order_index: order * 100,
        content_type: "table" as const,
        heading: null,
        body: null,
        table_data: n.table_data,
        plain_text: n.plain_text,
        uncertain: r.uncertain,
        role: r.role,
        has_placeholder: r.hasPlaceholder,
      };
    }
    const text = r.text || n.plain_text || "";
    const isHeading = r.typeLabel === "Heading";
    return {
      index: r.index,
      parent_index: parentIndex,
      order_index: order * 100,
      content_type: "prose" as const,
      heading: isHeading ? text : null,
      body: isHeading ? null : text,
      table_data: null,
      plain_text: text,
      uncertain: r.uncertain,
      role: r.role,
      has_placeholder: r.hasPlaceholder,
    };
  });
}

// Numbers follow clause position only (DD-02 / DD-54): non-clause roles consume
// no position, so the operative tree re-derives from the first real clause.
function renumber(rows: Row[]): Row[] {
  const clauseDepths = rows.filter((r) => r.role === "clause").map((r) => r.depth);
  const numbers = deriveNumbers(clauseDepths);
  let ci = 0;
  return rows.map((r) => (r.role === "clause" ? { ...r, number: numbers[ci++] } : { ...r, number: "" }));
}

// The contiguous subtree of the row at array position `p`: itself plus every
// following row deeper than it (descendants always follow their parent in document
// order). Returns the half-open end index `e` (first j>p with depth <= d).
function subtreeEnd(rows: Row[], p: number): number {
  const d = rows[p].depth;
  let e = p + 1;
  while (e < rows.length && rows[e].depth > d) e++;
  return e;
}

// Whether the selected clause can move up / down among its siblings (same depth,
// same parent, same clause region). Drives the disabled state of the move buttons;
// the actual reorder is in moveSubtree. A non-clause row never moves (front/back-
// matter keep their existing behavior).
function siblingMoves(rows: Row[], index: number): { up: boolean; down: boolean } {
  const p = rows.findIndex((r) => r.index === index);
  if (p === -1 || rows[p].role !== "clause") return { up: false, down: false };
  const d = rows[p].depth;
  const e = subtreeEnd(rows, p);
  const down = e < rows.length && rows[e].depth === d && rows[e].role === "clause";
  let up = false;
  for (let i = p - 1; i >= 0; i--) {
    if (rows[i].depth < d) break; // crossed up out of this parent — no prior sibling
    if (rows[i].depth === d) {
      up = rows[i].role === "clause";
      break;
    }
  }
  return { up, down };
}

// Whether the selected clause can fold into its previous sibling ("Merge up").
// Stricter than siblingMoves.up: both the selected node and the kept sibling must
// be prose clauses, never tables — a table commits from its structured cells /
// flattened plain_text (buildCommitNodes), so it would silently drop the merged
// prose. Guarding here keeps the no-data-loss invariant intact.
function canMergeUp(rows: Row[], index: number): boolean {
  const p = rows.findIndex((r) => r.index === index);
  if (p === -1 || rows[p].role !== "clause" || rows[p].typeLabel === "Table") return false;
  const d = rows[p].depth;
  for (let i = p - 1; i >= 0; i--) {
    if (rows[i].depth < d) return false; // crossed up out of this parent — no prior sibling
    if (rows[i].depth === d) return rows[i].role === "clause" && rows[i].typeLabel !== "Table";
  }
  return false;
}

// Placeholder (incomplete-field) markers a split half might carry: a bracketed
// blank ("[insert date]", "[ ]") or a run of underscores. Used to recompute each
// half's flag after a split, since the original's flag no longer describes either.
const PLACEHOLDER_PATTERN = /\[[^\]]*\]|_{2,}/;
function detectPlaceholder(text: string): boolean {
  return PLACEHOLDER_PATTERN.test(text);
}

// Where a split defaults to: just after the first sentence/clause boundary, else
// the midpoint. Only a hint — the operator repositions the caret in the editor.
function defaultSplitPos(text: string): number {
  const m = text.match(/[.;:]\s+/);
  if (m && m.index !== undefined) {
    const pos = m.index + m[0].length;
    if (pos > 0 && pos < text.length) return pos;
  }
  return Math.floor(text.length / 2);
}

// A one-line caption (short, not a colon-terminated chapeau). Used to tell a
// sub-heading parent ("8.2 Packaging and delivery.") from an operative lead-in
// ("8.2 The Supplier shall:").
function isCaptionText(text: string): boolean {
  const t = text.trim();
  return t.length > 0 && t.length <= 70 && !t.endsWith(":");
}

// Default a parent clause that is a short caption over sub-clauses to a numbered
// sub-heading (content-type Heading → bold) while keeping its clause number — a
// chapeau parent (colon / long lead-in) stays body. Applied once at load; the
// operator can flip any via the Type tool and it round-trips to commit.
function applyCaptionSubheadings(rows: Row[]): Row[] {
  const clauses = rows.filter((r) => r.role === "clause");
  const hasSubclause = new Set<number>();
  for (let i = 0; i < clauses.length - 1; i++) {
    if (clauses[i + 1].depth > clauses[i].depth) hasSubclause.add(clauses[i].index);
  }
  return rows.map((r) =>
    r.role === "clause" &&
    r.typeLabel !== "Table" &&
    hasSubclause.has(r.index) &&
    isCaptionText(r.text)
      ? { ...r, typeLabel: "Heading" }
      : r,
  );
}

// Default the back-matter hierarchy by category (DD-56): an appendix title sits at
// level 0, a heading one level under it, body one under that (body before any
// heading sits directly under the title). Applied once at load; depth round-trips
// to commit (deriveParents) and the operator can re-level via the tools.
function applyAppendixLeveling(rows: Row[]): Row[] {
  let sawHeadingSinceTitle = false;
  return rows.map((r) => {
    if (r.role === "appendix_title") {
      sawHeadingSinceTitle = false;
      return { ...r, depth: 0 };
    }
    if (r.role === "appendix") {
      if (r.typeLabel === "Heading") {
        sawHeadingSinceTitle = true;
        return { ...r, depth: 1 };
      }
      return { ...r, depth: sawHeadingSinceTitle ? 2 : 1 };
    }
    if (r.role === "signature_block") return { ...r, depth: 0 };
    return r;
  });
}

// The label shown in the SOURCE gutter / tree badge: clause → number; an appendix
// heading/body shows its kind (not a flat "Appendix"); everything else its role.
function categoryLabel(r: Row): string {
  if (r.role === "clause") return r.number;
  if (r.role === "appendix") return r.typeLabel; // Heading / Body / Table
  return ROLE_LABEL[r.role];
}

// Rows in a region that are currently visible: a collapsed node hides its whole
// descendant run (every following row deeper than it, until depth returns to its
// level or shallower). Mirrors what the operator sees, so range-select and
// keyboard nav operate on the visible order, never on hidden descendants.
function regionVisible(region: Row[], collapsed: ReadonlySet<number>): Row[] {
  const out: Row[] = [];
  let hideDeeperThan = Infinity;
  for (const r of region) {
    if (r.depth > hideDeeperThan) continue; // hidden descendant of a collapsed node
    hideDeeperThan = Infinity;
    out.push(r);
    if (collapsed.has(r.index)) hideDeeperThan = r.depth;
  }
  return out;
}

// Indices of region nodes that have at least one child — children always follow
// their parent immediately in document order, so a node has children iff the next
// row in the region is deeper. Drives the collapse twirl (computed over the full
// region, so the twirl still shows on a collapsed node).
function childrenSet(region: Row[]): Set<number> {
  const s = new Set<number>();
  for (let i = 0; i < region.length - 1; i++) {
    if (region[i + 1].depth > region[i].depth) s.add(region[i].index);
  }
  return s;
}

// Indeterminate parse has no server-streamed progress, so the label advances on a
// timer to mirror the backend pipeline stages (read_docx → classify → build_tree
// → derive). Motion + stage, never a frozen word.
const PARSE_PHASES = [
  "Reading document",
  "Extracting clauses",
  "Classifying roles",
  "Building tree",
] as const;

export default function ImportReview() {
  const [ctx, setCtx] = useState<ContractContext | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [tracked, setTracked] = useState<TrackedChangeReport | null>(null);
  // Selection is a set; `anchor` is the pivot for shift-range. A plain click
  // (navigate) replaces the set with one row and scrolls the source to it — the
  // only gesture that animates the right panel. Shift/Cmd never scroll.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  // Whole-region collapse (front-matter / back-matter), independent of per-node
  // collapse — keyed "front" / "back".
  const [collapsedRegions, setCollapsedRegions] = useState<Set<string>>(new Set());
  const [flash, setFlash] = useState<number | null>(null);
  // Transient attention cue on the LEFT tree row that Prev/Next lands on — the
  // selection highlight stays put, this pulses once so the eye finds the jump.
  const [treeFlash, setTreeFlash] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [parsePhase, setParsePhase] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [committing, setCommitting] = useState(false);
  const [committed, setCommitted] = useState<ImportResult | null>(null);
  // Index of the clause currently open in the split editor, or null. Single-select
  // only; cleared on any selection change / Escape / commit of the split.
  const [splitting, setSplitting] = useState<number | null>(null);

  const sourceRefs = useRef(new Map<number, HTMLParagraphElement>());
  // Per-index refs for the LEFT structure-tree rows (mirrors sourceRefs). Prev/Next
  // scrolls the selected node's tree row into view in the left panel, so the next
  // clause-to-review is brought to center where the operator is correcting.
  const treeRefs = useRef(new Map<number, HTMLElement>());
  // One hidden file input, opened via fileRef.current.click() from the upload
  // buttons. Programmatic .click() on a hidden input is more reliable than
  // label-for forwarding (which went stale across Fast Refresh / long-lived tabs).
  const fileRef = useRef<HTMLInputElement>(null);
  const pickFile = () => fileRef.current?.click();

  const remaining = rows.filter((r) => r.uncertain).length;

  const preamble = rows.filter((r) => FRONT_MATTER.has(r.role));
  const backmatter = rows.filter((r) => BACK_MATTER.has(r.role));
  const body = rows.filter((r) => r.role === "clause" || r.role === "drafting_note");

  // Collapse + visibility, derived each render from the role partitions.
  const bodyChildren = childrenSet(body);
  const backChildren = childrenSet(backmatter);
  const visibleBody = regionVisible(body, collapsed);
  const visibleBack = regionVisible(backmatter, collapsed);
  const frontCollapsed = collapsedRegions.has("front");
  const backCollapsed = collapsedRegions.has("back");
  const bodyCollapsed = collapsedRegions.has("body");
  // Collapse-all / expand-all spans BOTH axes: every node that has children
  // (clauses + appendix) AND the three region headers. presentRegions is only the
  // regions actually rendered (non-empty partition), keyed as their headers are.
  const allParents = new Set<number>([...bodyChildren, ...backChildren]);
  const presentRegions = new Set<string>();
  if (preamble.length > 0) presentRegions.add("front");
  if (body.length > 0) presentRegions.add("body");
  if (backmatter.length > 0) presentRegions.add("back");
  // "Everything expanded" = no node and no region collapsed; drives the label.
  const everythingExpanded = collapsed.size === 0 && collapsedRegions.size === 0;
  const toggleAll = () => {
    if (everythingExpanded) {
      setCollapsed(new Set(allParents));
      setCollapsedRegions(new Set(presentRegions));
    } else {
      setCollapsed(new Set());
      setCollapsedRegions(new Set());
    }
  };
  // Visible document order across all three regions — the axis shift-range walks.
  // A collapsed region contributes nothing, so a range never spans hidden rows.
  const visibleOrder = [
    ...(frontCollapsed ? [] : preamble),
    ...(bodyCollapsed ? [] : visibleBody),
    ...(backCollapsed ? [] : visibleBack),
  ].map((r) => r.index);

  const single = selected.size === 1;

  // Prev/Next review nav: walk the uncertain rows in document order (the rows array
  // IS document order). "Current" is the single selection or the anchor; a current
  // that isn't itself uncertain still finds the next/prev uncertain by document
  // position. Clamps at the ends — nextReviewTarget returns null past first/last.
  const uncertainIndices = rows.filter((r) => r.uncertain).map((r) => r.index);
  function nextReviewTarget(dir: -1 | 1): number | null {
    if (uncertainIndices.length === 0) return null;
    const posByIndex = new Map(rows.map((r, i) => [r.index, i] as const));
    const cur = single ? [...selected][0] : anchor;
    const curDoc =
      cur != null && posByIndex.has(cur) ? posByIndex.get(cur)! : dir === 1 ? -1 : rows.length;
    if (dir === 1) {
      for (const idx of uncertainIndices) if (posByIndex.get(idx)! > curDoc) return idx;
    } else {
      for (let i = uncertainIndices.length - 1; i >= 0; i--) {
        const idx = uncertainIndices[i];
        if (posByIndex.get(idx)! < curDoc) return idx;
      }
    }
    return null;
  }
  const prevReview = nextReviewTarget(-1);
  const nextReview = nextReviewTarget(1);
  function gotoReview(dir: -1 | 1) {
    const target = dir === 1 ? nextReview : prevReview;
    if (target == null) return;
    setSplitting(null);
    // Expand everything first so a target hidden inside a collapsed region or
    // collapsed parent is rendered before we scroll to it. The scroll is deferred
    // to the next frame because the tree row's ref only exists after React commits
    // the post-expand render.
    setCollapsed(new Set());
    setCollapsedRegions(new Set());
    setSelected(new Set([target]));
    setAnchor(target);
    requestAnimationFrame(() => scrollTreeRow(target));
  }

  // Clear selection on Escape — the deselect-all gesture (bulk bar also has Clear).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelected(new Set());
        setAnchor(null);
        setSplitting(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Advance the parse-phase label while loading; reset when the parse returns.
  // Clamps at the final stage rather than looping, so a slow parse rests on
  // "Building tree" instead of cycling back to the start.
  useEffect(() => {
    if (!loading) {
      setParsePhase(0);
      return;
    }
    const id = window.setInterval(() => {
      setParsePhase((p) => Math.min(p + 1, PARSE_PHASES.length - 1));
    }, 1500);
    return () => window.clearInterval(id);
  }, [loading]);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const res = await previewDocx(file);
      const mapped = applyAppendixLeveling(applyCaptionSubheadings(res.nodes.map(toRow)));
      setRows(mapped);
      setTotal(mapped.filter((r) => r.uncertain).length);
      setTracked(res.tracked_changes);
      setSelected(new Set());
      setAnchor(null);
      setCollapsed(new Set());
      setCollapsedRegions(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    } finally {
      setLoading(false);
      e.target.value = ""; // allow re-selecting the same file
    }
  }

  function scrollToSource(index: number) {
    const el = sourceRefs.current.get(index);
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "auto" });
    setFlash(index);
    window.setTimeout(() => setFlash((f) => (f === index ? null : f)), 1200);
  }

  // Scroll the landed node's row into view in the LEFT structure tree (Prev/Next
  // review nav) and pulse it briefly. The selection highlight (.selected) stays as
  // the persistent cue; this flash is the extra "it landed here" signal, mirroring
  // the source-panel .sFlash. Cleared after ~1.2s unless a newer jump replaced it.
  function scrollTreeRow(index: number) {
    const el = treeRefs.current.get(index);
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "auto" });
    setTreeFlash(index);
    window.setTimeout(() => setTreeFlash((f) => (f === index ? null : f)), 1200);
  }

  // The three selection gestures. Plain click = navigate (replace + scroll);
  // Shift = contiguous range from the anchor along the visible order (no scroll);
  // Cmd/Ctrl = toggle one in/out (no scroll). Modifiers move the anchor so a
  // following shift extends from the last touched row.
  function onRowClick(index: number, e: React.MouseEvent) {
    setSplitting(null); // any selection gesture closes an in-progress split
    if (e.shiftKey) {
      if (anchor === null) {
        setSelected(new Set([index]));
        setAnchor(index);
        return;
      }
      const a = visibleOrder.indexOf(anchor);
      const b = visibleOrder.indexOf(index);
      if (a === -1 || b === -1) {
        setSelected(new Set([index]));
        setAnchor(index);
        return;
      }
      const [lo, hi] = a < b ? [a, b] : [b, a];
      setSelected(new Set(visibleOrder.slice(lo, hi + 1)));
    } else if (e.metaKey || e.ctrlKey) {
      setSelected((s) => {
        const n = new Set(s);
        if (n.has(index)) n.delete(index);
        else n.add(index);
        return n;
      });
      setAnchor(index);
    } else {
      setSelected(new Set([index]));
      setAnchor(index);
      scrollToSource(index);
    }
  }

  const toggleCollapse = (index: number) =>
    setCollapsed((c) => {
      const n = new Set(c);
      if (n.has(index)) n.delete(index);
      else n.add(index);
      return n;
    });

  const toggleRegion = (key: string) =>
    setCollapsedRegions((c) => {
      const n = new Set(c);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });

  const patch = (index: number, fn: (r: Row) => Row) =>
    setRows((rs) => renumber(rs.map((r) => (r.index === index ? { ...fn(r), uncertain: false } : r))));

  const changeLevel = (index: number, delta: number) =>
    patch(index, (r) => ({ ...r, depth: Math.max(0, r.depth + delta) }));
  const setType = (index: number, typeLabel: string) =>
    patch(index, (r) => ({ ...r, typeLabel }));
  // Subtree-aware sibling reorder of the selected clause. Moving a parent carries
  // its whole descendant run (rows[p..e)). The reorder swaps the selected subtree
  // with the adjacent same-depth sibling subtree, then renumber() re-derives clause
  // numbers from the new array order (DD-02 — never stored). Sequence is now the
  // source of truth at commit (buildCommitNodes reads array order, not index).
  //
  // REPARENT is the composition of move + the level tools, not its own button: to
  // move e.g. "3.2(a)" out from under 3.1 and under 3.2, the operator outdents it
  // to 3.1/3.2's depth (‹), Moves ↓ past 3.2, then indents it back under (›).
  const moveSubtree = (index: number, dir: -1 | 1) =>
    setRows((rs) => {
      const p = rs.findIndex((r) => r.index === index);
      if (p === -1 || rs[p].role !== "clause") return rs;
      const d = rs[p].depth;
      const e = subtreeEnd(rs, p);
      if (dir === 1) {
        if (e >= rs.length || rs[e].depth !== d || rs[e].role !== "clause") return rs;
        const e2 = subtreeEnd(rs, e);
        return renumber([...rs.slice(0, p), ...rs.slice(e, e2), ...rs.slice(p, e), ...rs.slice(e2)]);
      }
      let q = -1;
      for (let i = p - 1; i >= 0; i--) {
        if (rs[i].depth < d) break;
        if (rs[i].depth === d) {
          q = i;
          break;
        }
      }
      if (q === -1 || rs[q].role !== "clause") return rs;
      return renumber([...rs.slice(0, q), ...rs.slice(p, e), ...rs.slice(q, p), ...rs.slice(e)]);
    });
  // Merge up: fold the selected clause into its previous sibling. The kept
  // sibling's committed body is r.text (buildCommitNodes prose branch), so the
  // selected node's text is appended there — that is where no-data-loss lives.
  // The selected row is then dropped; its descendants stay at their depth and so
  // deriveParents re-parents them under the kept sibling (depth unchanged). Numbers
  // re-derive from array order. canMergeUp has already ruled out table endpoints.
  function mergeUp(index: number) {
    if (!canMergeUp(rows, index)) return;
    const p = rows.findIndex((r) => r.index === index);
    const d = rows[p].depth;
    let q = -1;
    for (let i = p - 1; i >= 0; i--) {
      if (rows[i].depth < d) break;
      if (rows[i].depth === d) {
        q = i;
        break;
      }
    }
    if (q === -1) return;
    const keptIndex = rows[q].index;
    const mergedText = [rows[q].text.trimEnd(), rows[p].text.trim()]
      .filter((t) => t.length > 0)
      .join(" ");
    const next = rows
      .map((r, i) => (i === q ? { ...r, text: mergedText, uncertain: false } : r))
      .filter((_, i) => i !== p);
    setRows(renumber(next));
    setSelected(new Set([keptIndex]));
    setAnchor(keptIndex);
  }

  // Split: divide the selected clause's body at `pos` into two prose clauses.
  // The original keeps the text BEFORE the cut; a new sibling carrying the text
  // AFTER is inserted just past the original's subtree (== immediately after it for
  // a leaf), so any sub-clauses stay with the original rather than reparenting to
  // the split-off. The new row gets a synthetic index (max existing + 1, unique
  // because it joins `rows` immediately, so the next split sees it). parent_index /
  // order_index are re-derived at commit. INVARIANT: before + after === orig text.
  function splitRow(index: number, pos: number) {
    const p = rows.findIndex((r) => r.index === index);
    if (p === -1) return;
    const orig = rows[p];
    if (orig.typeLabel === "Table" || pos <= 0 || pos >= orig.text.length) return;
    const before = orig.text.slice(0, pos);
    const after = orig.text.slice(pos);
    const newIndex = rows.reduce((m, r) => Math.max(m, r.index), -1) + 1;
    const newNode: ApiCandidateNode = {
      index: newIndex,
      parent_index: null,
      order_index: 0,
      depth: orig.depth,
      number: "",
      content_type: "prose",
      heading: null,
      body: after,
      table_data: null,
      plain_text: after,
      uncertain: false,
      role: orig.role,
      has_placeholder: detectPlaceholder(after),
    };
    const newRow: Row = {
      index: newIndex,
      number: "",
      text: after,
      depth: orig.depth,
      typeLabel: "Body",
      uncertain: false,
      role: orig.role,
      hasPlaceholder: detectPlaceholder(after),
      node: newNode,
    };
    const updatedOrig: Row = {
      ...orig,
      text: before,
      uncertain: false,
      hasPlaceholder: detectPlaceholder(before),
    };
    const at = subtreeEnd(rows, p);
    const next = [
      ...rows.slice(0, p),
      updatedOrig,
      ...rows.slice(p + 1, at),
      newRow,
      ...rows.slice(at),
    ];
    setRows(renumber(next));
    setSplitting(null);
    setSelected(new Set([index]));
    setAnchor(index);
  }

  // "Looks right ✓" clears the row's uncertain flag (via patch) AND dismisses the
  // inline tools by deselecting — single-select drives the tools, so clearing the
  // selection hides the menu bar.
  const confirm = (index: number) => {
    patch(index, (r) => r);
    setSelected(new Set());
    setAnchor(null);
  };
  // Changing a node's role re-buckets it live: the preamble/body/backmatter
  // partitions above are derived from `rows` by role each render, so this single
  // setter moves the node into the correct region and renumber() re-derives the
  // clause tree (DD-02/DD-54). Clears uncertain like every other correction.
  const setRole = (index: number, role: Role) => patch(index, (r) => ({ ...r, role }));

  // Bulk variant of patch — one setRows so renumber runs once for the whole batch.
  const patchMany = (indices: ReadonlySet<number>, fn: (r: Row) => Row) =>
    setRows((rs) =>
      renumber(rs.map((r) => (indices.has(r.index) ? { ...fn(r), uncertain: false } : r))),
    );
  const bulkLevel = (delta: number) =>
    patchMany(selected, (r) => ({ ...r, depth: Math.max(0, r.depth + delta) }));
  const bulkType = (typeLabel: string) => patchMany(selected, (r) => ({ ...r, typeLabel }));
  const bulkRole = (role: Role) => patchMany(selected, (r) => ({ ...r, role }));

  async function onCommit() {
    if (!ctx || remaining > 0) return;
    setCommitting(true);
    setError(null);
    try {
      const result = await commitTree(ctx.contractId, buildCommitNodes(rows));
      setCommitted(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Commit failed");
    } finally {
      setCommitting(false);
    }
  }

  if (!ctx) return <ContextStep onReady={setCtx} />;

  // Step progression: Context done → Parse (awaiting upload) → Review → Commit.
  const activeStep: ImportStep = committed ? "commit" : rows.length > 0 ? "review" : "parse";

  // A node's inline correction tools show only when it is the sole selection.
  const showTools = (index: number) => single && selected.has(index);

  return (
    <div className={styles.screen}>
      {/* single hidden file input, triggered by every upload button via pickFile() */}
      <input
        ref={fileRef}
        type="file"
        accept=".docx"
        className={styles.fileInput}
        onChange={onFile}
      />
      <ImportTopBar active={activeStep}>
          {rows.length > 0 && (
            <span className={styles.counter}>
              {remaining === 0 ? (
                <span className={styles.counterDone}>All {total} reviewed</span>
              ) : (
                <>
                  <strong>{remaining}</strong> of {total} to review
                </>
              )}
            </span>
          )}
          {rows.length > 0 && remaining > 0 && (
            <div className={styles.reviewNav} role="group" aria-label="Step through items to review">
              <button
                type="button"
                className={styles.reviewNavBtn}
                onClick={() => gotoReview(-1)}
                disabled={prevReview == null}
                title="Previous item to review"
              >
                ‹ Prev
              </button>
              <button
                type="button"
                className={styles.reviewNavBtn}
                onClick={() => gotoReview(1)}
                disabled={nextReview == null}
                title="Next item to review"
              >
                Next ›
              </button>
            </div>
          )}
          <button type="button" className={styles.upload} onClick={pickFile}>
            {rows.length ? "Re-upload" : "Upload .docx"}
          </button>
          {rows.length > 0 && !committed && (
            <button
              className={styles.commit}
              disabled={remaining > 0 || committing}
              onClick={onCommit}
            >
              {committing ? "Committing…" : "Commit import →"}
            </button>
          )}
      </ImportTopBar>

      {tracked && tracked.flattened && !committed && (
        <div className={styles.banner}>
          This document had {tracked.insertions + tracked.deletions} tracked change
          {tracked.insertions + tracked.deletions === 1 ? "" : "s"} — imported as the accepted state.
          Re-upload a clean copy if that isn&apos;t what you want.
        </div>
      )}

      {committed ? (
        <div className={styles.empty}>
          <div className={styles.doneMark}>✓</div>
          <p className={styles.emptyTitle}>Contract imported</p>
          <p className={styles.emptyHint}>
            Imported {committed.node_count} node{committed.node_count === 1 ? "" : "s"} under{" "}
            <strong>{ctx.clientLabel}</strong> → <strong>{ctx.dealLabel}</strong> →{" "}
            <strong>{ctx.contractName}</strong>.
          </p>
        </div>
      ) : rows.length === 0 ? (
        <div className={styles.empty}>
          {loading ? (
            <div className={styles.parsing}>
              <p className={styles.emptyTitle}>Parsing contract</p>
              <div
                className={styles.progressTrack}
                role="progressbar"
                aria-label="Parsing contract"
              >
                <div className={styles.progressBar} />
              </div>
              <p className={styles.parsingPhase} aria-live="polite">
                {PARSE_PHASES[parsePhase]}…
              </p>
            </div>
          ) : (
            <>
              <p className={styles.emptyTitle}>Upload a contract to review its parse</p>
              <p className={styles.emptyHint}>
                donna reads the .docx, builds the clause tree, and flags anything it&apos;s unsure of.
              </p>
              {error && <p className={styles.error}>{error}</p>}
              <button type="button" className={styles.uploadBig} onClick={pickFile}>
                Choose a .docx
              </button>
            </>
          )}
        </div>
      ) : (
        <>
          {selected.size >= 2 && (
            <div className={styles.bulkBar}>
              <span className={styles.bulkCount}>{selected.size} selected</span>
              <div className={styles.bulkActions}>
                <span className={styles.bulkLabel}>Level</span>
                <button
                  className={styles.lvl}
                  onClick={() => bulkLevel(-1)}
                  title="Promote all selected — up a level"
                >
                  ‹
                </button>
                <button
                  className={styles.lvl}
                  onClick={() => bulkLevel(1)}
                  title="Demote all selected — down a level"
                >
                  ›
                </button>
                <select
                  className={styles.bulkSelect}
                  value=""
                  onChange={(e) => e.target.value && bulkType(e.target.value)}
                >
                  <option value="" disabled>
                    Set type…
                  </option>
                  {TYPE_CYCLE.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <select
                  className={styles.bulkSelect}
                  value=""
                  onChange={(e) => e.target.value && bulkRole(e.target.value as Role)}
                >
                  <option value="" disabled>
                    Set role…
                  </option>
                  {ROLE_OPTIONS.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
              </div>
              <button
                className={styles.bulkClear}
                onClick={() => {
                  setSelected(new Set());
                  setAnchor(null);
                }}
              >
                Clear
              </button>
            </div>
          )}
          <div className={styles.panels}>
            <section className={styles.tree}>
              <div className={styles.panelHead}>
                Structure
                <span className={styles.panelHint}>
                  Press shift to select a range
                </span>
                {presentRegions.size > 0 && (
                  <button className={styles.collapseAll} onClick={toggleAll}>
                    {everythingExpanded ? "⊟ Collapse all" : "⊞ Expand all"}
                  </button>
                )}
              </div>

              {preamble.length > 0 && (
                <div className={styles.region}>
                  <div
                    className={styles.regionHead}
                    onClick={() => toggleRegion("front")}
                    role="button"
                  >
                    <span className={styles.regionTwirl}>{frontCollapsed ? "▸" : "▾"}</span>
                    Preamble / front-matter<span className={styles.regionHint}>not numbered</span>
                  </div>
                  {!frontCollapsed &&
                    preamble.map((r) => (
                      <FrontBlock
                        key={r.index}
                        row={r}
                        selected={selected.has(r.index)}
                        flash={treeFlash === r.index}
                        showTools={showTools(r.index)}
                        treeRef={(el) => {
                          if (el) treeRefs.current.set(r.index, el);
                          else treeRefs.current.delete(r.index);
                        }}
                        onClick={onRowClick}
                        onSetRole={(role) => setRole(r.index, role)}
                        onConfirm={() => confirm(r.index)}
                      />
                    ))}
                </div>
              )}

              {body.length > 0 && (
                <div
                  className={styles.regionHead}
                  onClick={() => toggleRegion("body")}
                  role="button"
                >
                  <span className={styles.regionTwirl}>{bodyCollapsed ? "▸" : "▾"}</span>
                  Clauses<span className={styles.regionHint}>numbered · the operative tree</span>
                </div>
              )}
              {!bodyCollapsed && (
              <div className={styles.rows}>
                {visibleBody.map((r) => {
                  if (r.role === "drafting_note") {
                    return (
                      <DraftingNote
                        key={r.index}
                        row={r}
                        selected={selected.has(r.index)}
                        flash={treeFlash === r.index}
                        showTools={showTools(r.index)}
                        treeRef={(el) => {
                          if (el) treeRefs.current.set(r.index, el);
                          else treeRefs.current.delete(r.index);
                        }}
                        onClick={onRowClick}
                        onSetRole={(role) => setRole(r.index, role)}
                        onConfirm={() => confirm(r.index)}
                      />
                    );
                  }
                  const moves = showTools(r.index)
                    ? siblingMoves(rows, r.index)
                    : { up: false, down: false };
                  const canMerge = showTools(r.index) && canMergeUp(rows, r.index);
                  return (
                    <TreeRow
                      key={r.index}
                      row={r}
                      numbered
                      selected={selected.has(r.index)}
                      flash={treeFlash === r.index}
                      showTools={showTools(r.index)}
                      hasChildren={bodyChildren.has(r.index)}
                      collapsed={collapsed.has(r.index)}
                      treeRef={(el) => {
                        if (el) treeRefs.current.set(r.index, el);
                        else treeRefs.current.delete(r.index);
                      }}
                      onClick={onRowClick}
                      onToggleCollapse={() => toggleCollapse(r.index)}
                      onLevel={(d) => changeLevel(r.index, d)}
                      onMove={(d) => moveSubtree(r.index, d)}
                      canMoveUp={moves.up}
                      canMoveDown={moves.down}
                      canMerge={canMerge}
                      onMerge={() => mergeUp(r.index)}
                      splitting={splitting === r.index}
                      onStartSplit={() => setSplitting(r.index)}
                      onConfirmSplit={(pos) => splitRow(r.index, pos)}
                      onCancelSplit={() => setSplitting(null)}
                      onSetType={(t) => setType(r.index, t)}
                      onSetRole={(role) => setRole(r.index, role)}
                      onConfirm={() => confirm(r.index)}
                    />
                  );
                })}
              </div>
              )}

              {backmatter.length > 0 && (
                <div className={styles.region}>
                  <div
                    className={styles.regionHead}
                    onClick={() => toggleRegion("back")}
                    role="button"
                  >
                    <span className={styles.regionTwirl}>{backCollapsed ? "▸" : "▾"}</span>
                    Back-matter<span className={styles.regionHint}>not numbered · section / body styling</span>
                  </div>
                  {!backCollapsed &&
                    visibleBack.map((r) =>
                    r.role === "signature_block" ? (
                      <FrontBlock
                        key={r.index}
                        row={r}
                        selected={selected.has(r.index)}
                        flash={treeFlash === r.index}
                        showTools={showTools(r.index)}
                        treeRef={(el) => {
                          if (el) treeRefs.current.set(r.index, el);
                          else treeRefs.current.delete(r.index);
                        }}
                        onClick={onRowClick}
                        onSetRole={(role) => setRole(r.index, role)}
                        onConfirm={() => confirm(r.index)}
                      />
                    ) : (
                      <TreeRow
                        key={r.index}
                        row={r}
                        numbered={false}
                        selected={selected.has(r.index)}
                        flash={treeFlash === r.index}
                        showTools={showTools(r.index)}
                        hasChildren={backChildren.has(r.index)}
                        collapsed={collapsed.has(r.index)}
                        treeRef={(el) => {
                          if (el) treeRefs.current.set(r.index, el);
                          else treeRefs.current.delete(r.index);
                        }}
                        onClick={onRowClick}
                        onToggleCollapse={() => toggleCollapse(r.index)}
                        onLevel={(d) => changeLevel(r.index, d)}
                        onMove={(d) => moveSubtree(r.index, d)}
                        canMoveUp={false}
                        canMoveDown={false}
                        canMerge={false}
                        onMerge={() => mergeUp(r.index)}
                        splitting={splitting === r.index}
                        onStartSplit={() => setSplitting(r.index)}
                        onConfirmSplit={(pos) => splitRow(r.index, pos)}
                        onCancelSplit={() => setSplitting(null)}
                        onSetType={(t) => setType(r.index, t)}
                        onSetRole={(role) => setRole(r.index, role)}
                        onConfirm={() => confirm(r.index)}
                      />
                    ),
                  )}
                </div>
              )}
            </section>

            <section className={styles.source}>
              <div className={styles.panelHead}>
                Source<span className={styles.panelHint}>parsed content — accepted state</span>
              </div>
              <div className={styles.doc}>
                {rows.map((r) => {
                  const isHeading = r.typeLabel === "Heading";
                  const label = categoryLabel(r);
                  const capsBold = FRONT_MATTER.has(r.role) || BACK_MATTER.has(r.role);
                  return (
                    <p
                      key={r.index}
                      ref={(el) => {
                        if (el) sourceRefs.current.set(r.index, el);
                        else sourceRefs.current.delete(r.index);
                      }}
                      className={[styles.sPara, flash === r.index ? styles.sFlash : ""].join(" ")}
                    >
                      <span className={styles.sNum}>{label}</span>
                      <span
                        className={isHeading ? styles.sHeadingText : undefined}
                        style={{ marginLeft: r.depth * 18 }}
                      >
                        {renderRich(r.text, capsBold, styles.sBold)}
                      </span>
                    </p>
                  );
                })}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function PlaceholderTag() {
  return <span className={styles.placeholder}>incomplete field</span>;
}

// Per-node role selector (gap 1) — present on every node in every region.
function RoleSelect({ value, onChange }: { value: Role; onChange: (role: Role) => void }) {
  return (
    <select
      className={styles.roleSelect}
      value={value}
      onChange={(e) => onChange(e.target.value as Role)}
    >
      {ROLE_OPTIONS.map((r) => (
        <option key={r} value={r}>
          {ROLE_LABEL[r]}
        </option>
      ))}
    </select>
  );
}

// The collapse twirl, or a fixed-width spacer so leaf rows stay aligned with
// their expandable siblings.
function Twirl({
  hasChildren,
  collapsed,
  onToggle,
}: {
  hasChildren: boolean;
  collapsed: boolean;
  onToggle: () => void;
}) {
  if (!hasChildren) return <span className={styles.twirlSpace} />;
  return (
    <button
      className={styles.twirl}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      title={collapsed ? "Expand" : "Collapse"}
    >
      {collapsed ? "▸" : "▾"}
    </button>
  );
}

// Clause / appendix row — numbered for clauses, unnumbered for appendix
// (section/body styling, DD-54). Headings render bold; any node with children is
// collapsible; depth drives the indent. Single-selection reveals the inline tools.
function TreeRow({
  row,
  numbered,
  selected,
  flash,
  showTools,
  hasChildren,
  collapsed,
  treeRef,
  onClick,
  onToggleCollapse,
  onLevel,
  onMove,
  canMoveUp,
  canMoveDown,
  canMerge,
  onMerge,
  splitting,
  onStartSplit,
  onConfirmSplit,
  onCancelSplit,
  onSetType,
  onSetRole,
  onConfirm,
}: {
  row: Row;
  numbered: boolean;
  selected: boolean;
  flash: boolean;
  showTools: boolean;
  hasChildren: boolean;
  collapsed: boolean;
  treeRef: (el: HTMLDivElement | null) => void;
  onClick: (index: number, e: React.MouseEvent) => void;
  onToggleCollapse: () => void;
  onLevel: (delta: number) => void;
  onMove: (dir: -1 | 1) => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
  canMerge: boolean;
  onMerge: () => void;
  splitting: boolean;
  onStartSplit: () => void;
  onConfirmSplit: (pos: number) => void;
  onCancelSplit: () => void;
  onSetType: (typeLabel: string) => void;
  onSetRole: (role: Role) => void;
  onConfirm: () => void;
}) {
  const isApxTitle = row.role === "appendix_title";
  const isHeading = row.typeLabel === "Heading";
  const textClass = isApxTitle ? styles.appendixTitle : isHeading ? styles.headingText : "";
  const isClause = row.role === "clause";
  const [splitPos, setSplitPos] = useState(() => defaultSplitPos(row.text));
  // Overflow menu (Move/Merge/Split) collapses whenever this row loses its tools —
  // i.e. on another selection or an Escape clear — so it never re-opens stale.
  const [overflowOpen, setOverflowOpen] = useState(false);
  useEffect(() => {
    if (!showTools) setOverflowOpen(false);
  }, [showTools]);
  return (
    <div
      ref={treeRef}
      className={[
        styles.row,
        row.uncertain ? styles.uncertain : "",
        selected ? styles.selected : "",
        flash ? styles.treeFlash : "",
      ].join(" ")}
      style={{ paddingLeft: 8 + row.depth * 22 }}
      onClick={(e) => onClick(row.index, e)}
    >
      <Twirl hasChildren={hasChildren} collapsed={collapsed} onToggle={onToggleCollapse} />
      <span className={styles.flag}>{row.uncertain ? "⚠" : "✓"}</span>
      {numbered && <span className={styles.num}>{row.number}</span>}
      <span className={[styles.text, textClass].join(" ")}>{row.text}</span>
      {row.hasPlaceholder && <PlaceholderTag />}
      <span className={[styles.badge, isApxTitle ? styles.badgeTitle : ""].join(" ")}>
        {isApxTitle ? "Appendix title" : row.typeLabel}
      </span>

      {showTools && !splitting && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <button className={styles.lvl} onClick={() => onLevel(-1)} title="Promote — up a level">‹</button>
          <button className={styles.lvl} onClick={() => onLevel(1)} title="Demote — down a level">›</button>
          {/* An appendix title is a divider, not a Heading/Body/Table choice — its
              kind is fixed by the role. Showing the type control here reads as
              "this is a heading" and undercuts the Appendix-title identity, so it
              is hidden until the operator re-roles the row away from the title. */}
          {!isApxTitle && (
            <select
              className={styles.roleSelect}
              value={row.typeLabel}
              onChange={(e) => onSetType(e.target.value)}
              title="Content type"
            >
              {TYPE_CYCLE.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          )}
          <RoleSelect value={row.role} onChange={onSetRole} />
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
          {isClause && (
            <div
              className={styles.overflow}
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOverflowOpen(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setOverflowOpen(false);
                  e.stopPropagation();
                }
              }}
            >
              <button
                className={styles.lvl}
                aria-haspopup="true"
                aria-expanded={overflowOpen}
                onClick={() => setOverflowOpen((o) => !o)}
                title="More actions — move, merge, split"
              >
                ⋯
              </button>
              {overflowOpen && (
                <div className={styles.overflowMenu}>
                  <button
                    onClick={() => onMove(-1)}
                    disabled={!canMoveUp}
                    title="Move up — before the previous clause at this level (carries its sub-clauses)"
                  >
                    ↑ Move up
                  </button>
                  <button
                    onClick={() => onMove(1)}
                    disabled={!canMoveDown}
                    title="Move down — after the next clause at this level (carries its sub-clauses)"
                  >
                    ↓ Move down
                  </button>
                  <button
                    onClick={onMerge}
                    disabled={!canMerge}
                    title="Merge up — fold this clause into the one before it (its sub-clauses follow)"
                  >
                    Merge up
                  </button>
                  {row.typeLabel !== "Table" && (
                    <button onClick={onStartSplit} title="Split — divide this clause into two">
                      Split…
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {splitting && (
        <div className={styles.splitPanel} onClick={(e) => e.stopPropagation()}>
          <p className={styles.splitHint}>Click in the text where the clause should divide.</p>
          <textarea
            className={styles.splitField}
            readOnly
            value={row.text}
            onSelect={(e) => setSplitPos(e.currentTarget.selectionStart)}
            onClick={(e) => setSplitPos(e.currentTarget.selectionStart)}
            onKeyUp={(e) => setSplitPos(e.currentTarget.selectionStart)}
          />
          <div className={styles.splitPreview}>
            <div className={styles.splitHalf}>
              <span className={styles.splitTag}>Stays as {row.number || "this clause"}</span>
              <span>
                {row.text.slice(0, splitPos) || <em className={styles.splitEmpty}>(empty)</em>}
              </span>
            </div>
            <div className={styles.splitHalf}>
              <span className={styles.splitTag}>New clause</span>
              <span>
                {row.text.slice(splitPos) || <em className={styles.splitEmpty}>(empty)</em>}
              </span>
            </div>
          </div>
          <div className={styles.splitActions}>
            <button
              className={styles.ok}
              disabled={splitPos <= 0 || splitPos >= row.text.length}
              onClick={() => onConfirmSplit(splitPos)}
            >
              Split into two ✓
            </button>
            <button onClick={onCancelSplit}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function DraftingNote({
  row,
  selected,
  flash,
  showTools,
  treeRef,
  onClick,
  onSetRole,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  flash: boolean;
  showTools: boolean;
  treeRef: (el: HTMLDivElement | null) => void;
  onClick: (index: number, e: React.MouseEvent) => void;
  onSetRole: (role: Role) => void;
  onConfirm: () => void;
}) {
  return (
    <div
      ref={treeRef}
      className={[
        styles.note,
        row.uncertain ? styles.uncertain : "",
        selected ? styles.selected : "",
        flash ? styles.treeFlash : "",
      ].join(" ")}
      style={{ marginLeft: 14 + row.depth * 22 }}
      onClick={(e) => onClick(row.index, e)}
    >
      <span className={styles.flag}>{row.uncertain ? "⚠" : "✓"}</span>
      <span className={styles.noteLabel}>Internal note — not exported</span>
      <span className={styles.noteText}>{row.text}</span>
      {row.hasPlaceholder && <PlaceholderTag />}

      {showTools && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <RoleSelect value={row.role} onChange={onSetRole} />
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
        </div>
      )}
    </div>
  );
}

function FrontBlock({
  row,
  selected,
  flash,
  showTools,
  treeRef,
  onClick,
  onSetRole,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  flash: boolean;
  showTools: boolean;
  treeRef: (el: HTMLDivElement | null) => void;
  onClick: (index: number, e: React.MouseEvent) => void;
  onSetRole: (role: Role) => void;
  onConfirm: () => void;
}) {
  const isTitle = row.role === "title";
  return (
    <div
      ref={treeRef}
      className={[
        styles.block,
        isTitle ? styles.titleBlock : "",
        row.uncertain ? styles.uncertain : "",
        selected ? styles.selected : "",
        flash ? styles.treeFlash : "",
      ].join(" ")}
      onClick={(e) => onClick(row.index, e)}
    >
      <div className={styles.blockHead}>
        <span className={styles.flag}>{row.uncertain ? "⚠" : "✓"}</span>
        <span className={styles.roleLabel}>{ROLE_LABEL[row.role]}</span>
        {row.hasPlaceholder && <PlaceholderTag />}
      </div>
      <div className={isTitle ? styles.titleText : styles.blockText}>{row.text}</div>

      {showTools && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <RoleSelect value={row.role} onChange={onSetRole} />
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
        </div>
      )}
    </div>
  );
}
