"use client";

import { useRef, useState } from "react";
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

const TYPE_CYCLE = ["Heading", "Body", "Table", "Appendix"];

const FRONT_MATTER: ReadonlySet<Role> = new Set<Role>([
  "title",
  "date",
  "parties",
  "recital",
  "agreement_statement",
]);
const BACK_MATTER: ReadonlySet<Role> = new Set<Role>(["appendix", "signature_block"]);

const ROLE_LABEL: Record<Role, string> = {
  title: "Title",
  date: "Date",
  parties: "Parties",
  recital: "Recital",
  agreement_statement: "Agreement statement",
  clause: "Clause",
  appendix: "Appendix",
  signature_block: "Signature block",
  drafting_note: "Drafting note",
};

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
    return {
      index: r.index,
      parent_index: parentIndex,
      order_index: order * 100,
      content_type: n.content_type === "table" ? "table" : "prose",
      heading: n.heading,
      body: n.body,
      table_data: n.table_data,
      plain_text: n.plain_text,
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

export default function ImportReview() {
  const [ctx, setCtx] = useState<ContractContext | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [tracked, setTracked] = useState<TrackedChangeReport | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [committing, setCommitting] = useState(false);
  const [committed, setCommitted] = useState<ImportResult | null>(null);

  const sourceRefs = useRef(new Map<number, HTMLParagraphElement>());

  const remaining = rows.filter((r) => r.uncertain).length;

  const preamble = rows.filter((r) => FRONT_MATTER.has(r.role));
  const backmatter = rows.filter((r) => BACK_MATTER.has(r.role));
  const body = rows.filter((r) => r.role === "clause" || r.role === "drafting_note");

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const res = await previewDocx(file);
      const mapped = res.nodes.map(toRow);
      setRows(mapped);
      setTotal(mapped.filter((r) => r.uncertain).length);
      setTracked(res.tracked_changes);
      setSelected(null);
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

  function selectRow(index: number) {
    const willSelect = selected !== index;
    setSelected(willSelect ? index : null);
    if (willSelect) scrollToSource(index);
  }

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

  return (
    <div className={styles.screen}>
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
          <label className={styles.upload}>
            {rows.length ? "Re-upload" : "Upload .docx"}
            <input type="file" accept=".docx" className={styles.fileInput} onChange={onFile} />
          </label>
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
              <label className={styles.uploadBig}>
                Choose a .docx
                <input type="file" accept=".docx" className={styles.fileInput} onChange={onFile} />
              </label>
            </>
          )}
        </div>
      ) : (
        <div className={styles.panels}>
          <section className={styles.tree}>
            <div className={styles.panelHead}>
              Structure
              <span className={styles.panelHint}>verify regions and clauses — fix the flagged rows</span>
            </div>

            {preamble.length > 0 && (
              <div className={styles.region}>
                <div className={styles.regionHead}>
                  Preamble / front-matter<span className={styles.regionHint}>not numbered</span>
                </div>
                {preamble.map((r) => (
                  <FrontBlock
                    key={r.index}
                    row={r}
                    selected={selected === r.index}
                    onSelect={() => selectRow(r.index)}
                    onConfirm={() => confirm(r.index)}
                  />
                ))}
              </div>
            )}

            <div className={styles.rows}>
              {body.map((r) =>
                r.role === "drafting_note" ? (
                  <DraftingNote
                    key={r.index}
                    row={r}
                    selected={selected === r.index}
                    onSelect={() => selectRow(r.index)}
                    onConfirm={() => confirm(r.index)}
                  />
                ) : (
                  <ClauseRow
                    key={r.index}
                    row={r}
                    selected={selected === r.index}
                    onSelect={() => selectRow(r.index)}
                    onLevel={(d) => changeLevel(r.index, d)}
                    onCycleType={() => cycleType(r.index)}
                    onConfirm={() => confirm(r.index)}
                  />
                ),
              )}
            </div>

            {backmatter.length > 0 && (
              <div className={styles.region}>
                <div className={styles.regionHead}>
                  Back-matter<span className={styles.regionHint}>not numbered</span>
                </div>
                {backmatter.map((r) => (
                  <FrontBlock
                    key={r.index}
                    row={r}
                    selected={selected === r.index}
                    onSelect={() => selectRow(r.index)}
                    onConfirm={() => confirm(r.index)}
                  />
                ))}
              </div>
            )}
          </section>

          <section className={styles.source}>
            <div className={styles.panelHead}>
              Source<span className={styles.panelHint}>parsed content — accepted state</span>
            </div>
            <div className={styles.doc}>
              {rows.map((r) => (
                <p
                  key={r.index}
                  ref={(el) => {
                    if (el) sourceRefs.current.set(r.index, el);
                    else sourceRefs.current.delete(r.index);
                  }}
                  className={[styles.sPara, flash === r.index ? styles.sFlash : ""].join(" ")}
                >
                  <span className={styles.sNum}>{r.number || (r.role !== "clause" ? ROLE_LABEL[r.role] : "")}</span>
                  <span>{r.text}</span>
                </p>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function PlaceholderTag() {
  return <span className={styles.placeholder}>incomplete field</span>;
}

function ClauseRow({
  row,
  selected,
  onSelect,
  onLevel,
  onCycleType,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  onSelect: () => void;
  onLevel: (delta: number) => void;
  onCycleType: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className={[styles.row, row.uncertain ? styles.uncertain : "", selected ? styles.selected : ""].join(" ")}
      style={{ paddingLeft: 14 + row.depth * 22 }}
      onClick={onSelect}
    >
      <span className={styles.flag}>{row.uncertain ? "⚠" : "✓"}</span>
      <span className={styles.num}>{row.number}</span>
      <span className={styles.text}>{row.text}</span>
      {row.hasPlaceholder && <PlaceholderTag />}
      <span className={styles.badge}>{row.typeLabel}</span>

      {selected && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <button className={styles.lvl} onClick={() => onLevel(-1)} title="Promote — up a level">◄</button>
          <button className={styles.lvl} onClick={() => onLevel(1)} title="Demote — down a level">►</button>
          <button onClick={onCycleType}>Type: {row.typeLabel}</button>
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
        </div>
      )}
    </div>
  );
}

function DraftingNote({
  row,
  selected,
  onSelect,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  onSelect: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className={[styles.note, row.uncertain ? styles.uncertain : "", selected ? styles.selected : ""].join(" ")}
      style={{ marginLeft: 14 + row.depth * 22 }}
      onClick={onSelect}
    >
      <span className={styles.flag}>{row.uncertain ? "⚠" : "✓"}</span>
      <span className={styles.noteLabel}>Internal note — not exported</span>
      <span className={styles.noteText}>{row.text}</span>
      {row.hasPlaceholder && <PlaceholderTag />}

      {selected && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
        </div>
      )}
    </div>
  );
}

function FrontBlock({
  row,
  selected,
  onSelect,
  onConfirm,
}: {
  row: Row;
  selected: boolean;
  onSelect: () => void;
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
      onClick={onSelect}
    >
      <div className={styles.blockHead}>
        <span className={styles.flag}>{row.uncertain ? "⚠" : "✓"}</span>
        <span className={styles.roleLabel}>{ROLE_LABEL[row.role]}</span>
        {row.hasPlaceholder && <PlaceholderTag />}
      </div>
      <div className={isTitle ? styles.titleText : styles.blockText}>{row.text}</div>

      {selected && (
        <div className={styles.tools} onClick={(e) => e.stopPropagation()}>
          <button className={styles.ok} onClick={onConfirm}>Looks right ✓</button>
        </div>
      )}
    </div>
  );
}
