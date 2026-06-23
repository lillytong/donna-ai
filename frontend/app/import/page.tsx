"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./review.module.css";
import ContextStep, { type ContractContext } from "./ContextStep";
import { deriveNumbers, deriveParents } from "./lib/numbering";
import {
  commitTree,
  previewDocx,
  type ApiCandidateNode,
  type ImportResult,
  type NodeRow,
  type Role,
  type TrackedChangeReport,
} from "./lib/api";

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
function buildCommitNodes(rows: Row[]): NodeRow[] {
  const ordered = [...rows].sort((a, b) => a.index - b.index);
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

// Source-panel rich text: bold quoted defined terms ("Agreement") everywhere, and
// — in front/back matter — bold ALL-CAPS connective/legal words (WHEREAS, AND,
// THEREFORE, party names). Mirrors the legal-doc style guide so the rendered
// source reads like the finished document and mis-tags stand out.
const QUOTED = "[“”\"][^“”\"]+[“”\"]";
const CAPS = "\\b[A-Z]{2,}\\b";
function renderRich(text: string, capsBold: boolean): React.ReactNode {
  const re = new RegExp(capsBold ? `${QUOTED}|${CAPS}` : QUOTED, "g");
  const out: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <strong key={key++} className={styles.sBold}>
        {m[0]}
      </strong>,
    );
    last = m.index + m[0].length;
    if (m.index === re.lastIndex) re.lastIndex++; // guard against a zero-length match
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Numbers follow clause position only (DD-02 / DD-54): non-clause roles consume
// no position, so the operative tree re-derives from the first real clause.
function renumber(rows: Row[]): Row[] {
  const clauseDepths = rows.filter((r) => r.role === "clause").map((r) => r.depth);
  const numbers = deriveNumbers(clauseDepths);
  let ci = 0;
  return rows.map((r) => (r.role === "clause" ? { ...r, number: numbers[ci++] } : { ...r, number: "" }));
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [committing, setCommitting] = useState(false);
  const [committed, setCommitted] = useState<ImportResult | null>(null);

  const sourceRefs = useRef(new Map<number, HTMLParagraphElement>());
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
  // Collapse-all / expand-all over every node that has children (clauses + appendix).
  const allParents = new Set<number>([...bodyChildren, ...backChildren]);
  const allCollapsed = allParents.size > 0 && [...allParents].every((i) => collapsed.has(i));
  const toggleAll = () => setCollapsed(allCollapsed ? new Set() : new Set(allParents));
  // Visible document order across all three regions — the axis shift-range walks.
  // A collapsed region contributes nothing, so a range never spans hidden rows.
  const visibleOrder = [
    ...(frontCollapsed ? [] : preamble),
    ...visibleBody,
    ...(backCollapsed ? [] : visibleBack),
  ].map((r) => r.index);

  const single = selected.size === 1;

  // Clear selection on Escape — the deselect-all gesture (bulk bar also has Clear).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelected(new Set());
        setAnchor(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlash(index);
    window.setTimeout(() => setFlash((f) => (f === index ? null : f)), 1200);
  }

  // The three selection gestures. Plain click = navigate (replace + scroll);
  // Shift = contiguous range from the anchor along the visible order (no scroll);
  // Cmd/Ctrl = toggle one in/out (no scroll). Modifiers move the anchor so a
  // following shift extends from the last touched row.
  function onRowClick(index: number, e: React.MouseEvent) {
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
  const cycleType = (index: number) =>
    patch(index, (r) => ({
      ...r,
      typeLabel: TYPE_CYCLE[(TYPE_CYCLE.indexOf(r.typeLabel) + 1) % TYPE_CYCLE.length],
    }));
  const confirm = (index: number) => patch(index, (r) => r);
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
  const activeStep = committed ? 3 : rows.length > 0 ? 2 : 1;
  const steps = ["Context", "Parse", "Review", "Commit"];

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
      <header className={styles.topbar}>
        <div className={styles.brand}>
          donna<span className={styles.dot}>.</span>ai
        </div>
        <ol className={styles.steps}>
          {steps.map((label, i) => (
            <li key={label} className={i === activeStep ? styles.stepActive : ""}>
              {label}
            </li>
          ))}
        </ol>
        <div className={styles.right}>
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
        </div>
      </header>

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
            <p>Parsing…</p>
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
                  click to browse · shift-click for a range · ⌘/ctrl-click to add
                </span>
                {allParents.size > 0 && (
                  <button className={styles.collapseAll} onClick={toggleAll}>
                    {allCollapsed ? "⊞ Expand all" : "⊟ Collapse all"}
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
                        showTools={showTools(r.index)}
                        onClick={onRowClick}
                        onSetRole={(role) => setRole(r.index, role)}
                        onConfirm={() => confirm(r.index)}
                      />
                    ))}
                </div>
              )}

              <div className={styles.rows}>
                {visibleBody.map((r) =>
                  r.role === "drafting_note" ? (
                    <DraftingNote
                      key={r.index}
                      row={r}
                      selected={selected.has(r.index)}
                      showTools={showTools(r.index)}
                      onClick={onRowClick}
                      onSetRole={(role) => setRole(r.index, role)}
                      onConfirm={() => confirm(r.index)}
                    />
                  ) : (
                    <TreeRow
                      key={r.index}
                      row={r}
                      numbered
                      selected={selected.has(r.index)}
                      showTools={showTools(r.index)}
                      hasChildren={bodyChildren.has(r.index)}
                      collapsed={collapsed.has(r.index)}
                      onClick={onRowClick}
                      onToggleCollapse={() => toggleCollapse(r.index)}
                      onLevel={(d) => changeLevel(r.index, d)}
                      onCycleType={() => cycleType(r.index)}
                      onSetRole={(role) => setRole(r.index, role)}
                      onConfirm={() => confirm(r.index)}
                    />
                  ),
                )}
              </div>

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
                        showTools={showTools(r.index)}
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
                        showTools={showTools(r.index)}
                        hasChildren={backChildren.has(r.index)}
                        collapsed={collapsed.has(r.index)}
                        onClick={onRowClick}
                        onToggleCollapse={() => toggleCollapse(r.index)}
                        onLevel={(d) => changeLevel(r.index, d)}
                        onCycleType={() => cycleType(r.index)}
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
                        {renderRich(r.text, capsBold)}
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
  showTools,
  hasChildren,
  collapsed,
  onClick,
  onToggleCollapse,
  onLevel,
  onCycleType,
  onSetRole,
  onConfirm,
}: {
  row: Row;
  numbered: boolean;
  selected: boolean;
  showTools: boolean;
  hasChildren: boolean;
  collapsed: boolean;
  onClick: (index: number, e: React.MouseEvent) => void;
  onToggleCollapse: () => void;
  onLevel: (delta: number) => void;
  onCycleType: () => void;
  onSetRole: (role: Role) => void;
  onConfirm: () => void;
}) {
  const isApxTitle = row.role === "appendix_title";
  const isHeading = row.typeLabel === "Heading";
  const textClass = isApxTitle ? styles.appendixTitle : isHeading ? styles.headingText : "";
  return (
    <div
      className={[styles.row, row.uncertain ? styles.uncertain : "", selected ? styles.selected : ""].join(" ")}
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

      {showTools && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <button className={styles.lvl} onClick={() => onLevel(-1)} title="Promote — up a level">‹</button>
          <button className={styles.lvl} onClick={() => onLevel(1)} title="Demote — down a level">›</button>
          <button onClick={onCycleType}>Type: {row.typeLabel}</button>
          <RoleSelect value={row.role} onChange={onSetRole} />
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
        </div>
      )}
    </div>
  );
}

function DraftingNote({
  row,
  selected,
  showTools,
  onClick,
  onSetRole,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  showTools: boolean;
  onClick: (index: number, e: React.MouseEvent) => void;
  onSetRole: (role: Role) => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className={[styles.note, row.uncertain ? styles.uncertain : "", selected ? styles.selected : ""].join(" ")}
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
  showTools,
  onClick,
  onSetRole,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  showTools: boolean;
  onClick: (index: number, e: React.MouseEvent) => void;
  onSetRole: (role: Role) => void;
  onConfirm: () => void;
}) {
  const isTitle = row.role === "title";
  return (
    <div
      className={[
        styles.block,
        isTitle ? styles.titleBlock : "",
        row.uncertain ? styles.uncertain : "",
        selected ? styles.selected : "",
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
