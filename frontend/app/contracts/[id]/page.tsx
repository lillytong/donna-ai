"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import styles from "../cockpit.module.css";
import { deriveNumbers } from "../../lib/numbering";
import {
  addComment,
  createIssue,
  getContractTree,
  listComments,
  listIssues,
  updateIssueStatus,
  type Initiator,
  type IssueStatus,
  type NodeTreeItem,
  type Role,
  type StoredComment,
  type StoredIssue,
} from "../../lib/api";

// A clause/region in document order, with its derived outline number (clauses
// only — DD-02/DD-54). This is the read-only spine the operator navigates.
interface FlatNode {
  id: string;
  depth: number;
  role: Role;
  text: string;
  isHeading: boolean;
  number: string; // "" for non-clause roles
}

// Depth-first walk = document order; children arrive pre-sorted by order_index.
function flatten(nodes: NodeTreeItem[]): Omit<FlatNode, "number">[] {
  const out: Omit<FlatNode, "number">[] = [];
  const walk = (n: NodeTreeItem, depth: number) => {
    const text = n.heading ?? n.body ?? n.plain_text ?? "";
    out.push({ id: n.id, depth, role: n.role, text, isHeading: !!n.heading && !n.body });
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

// Issue lifecycle (F07). Order drives the status control; labels are operator-facing.
const STATUS_ORDER: IssueStatus[] = ["open", "agreed", "deferred", "kicked", "dismissed"];
const STATUS_LABEL: Record<IssueStatus, string> = {
  open: "Open",
  agreed: "Agreed",
  deferred: "Deferred",
  kicked: "Kicked up",
  dismissed: "Dismissed",
};
const STATUS_CLASS: Record<IssueStatus, string> = {
  open: "statusOpen",
  agreed: "statusAgreed",
  deferred: "statusDeferred",
  kicked: "statusKicked",
  dismissed: "statusDismissed",
};
function asStatus(s: string): IssueStatus {
  return (STATUS_ORDER as string[]).includes(s) ? (s as IssueStatus) : "open";
}

function commentAuthor(actor: string): string {
  return actor === "user" ? "You" : actor === "ai" ? "Donna" : actor === "principal" ? "Principal" : actor;
}
function shortTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Fetch stages so the wait shows motion + a sense of progress, never a frozen word.
type LoadState =
  | { kind: "loading"; phase: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; rows: FlatNode[]; issues: StoredIssue[] };

export default function Cockpit({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [state, setState] = useState<LoadState>({ kind: "loading", phase: "Loading contract" });
  const [reloadKey, setReloadKey] = useState(0);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [jumpVal, setJumpVal] = useState("");
  const [flashId, setFlashId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Issue detail (F07/F09): one expanded issue at a time shows its status control
  // + comment thread. Comments are fetched lazily for the open issue.
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [statusBusyId, setStatusBusyId] = useState<string | null>(null);
  const [comments, setComments] = useState<StoredComment[]>([]);
  const [commentsState, setCommentsState] = useState<"idle" | "loading" | "error">("idle");
  const [commentDraft, setCommentDraft] = useState("");
  const [addingComment, setAddingComment] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [noteText, setNoteText] = useState("");
  const [initiator, setInitiator] = useState<Initiator>("operator");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

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
        const rows = withNumbers(flatten(tree.nodes));
        setState({ kind: "ready", rows, issues });
      } catch (e) {
        if (live) setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
      }
    })();
    return () => {
      live = false;
    };
  }, [id, reloadKey]);

  const ready = state.kind === "ready" ? state : null;
  const rows = ready?.rows ?? [];
  const issues = ready?.issues ?? [];

  // Lookups: clause number → id (jump), id → row (anchor labels + tree badges).
  const clauseByNumber = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of rows) if (r.number && !m.has(r.number)) m.set(r.number, r.id);
    return m;
  }, [rows]);
  const rowById = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const issuesByNode = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of issues) if (i.node_id) m.set(i.node_id, (m.get(i.node_id) ?? 0) + 1);
    return m;
  }, [issues]);
  const sortedIssues = useMemo(
    () => [...issues].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [issues],
  );
  const parentIds = useMemo(() => childIds(rows), [rows]);
  const visible = useMemo(() => visibleRows(rows, collapsed), [rows, collapsed]);

  // Lazily load the open issue's comment thread; clearing on collapse keeps the
  // thread scoped to the expanded card.
  useEffect(() => {
    if (!expandedId) {
      setComments([]);
      setCommentsState("idle");
      return;
    }
    let live = true;
    setCommentsState("loading");
    setCommentError(null);
    (async () => {
      try {
        const cs = await listComments(expandedId);
        if (live) {
          setComments(cs);
          setCommentsState("idle");
        }
      } catch {
        if (live) setCommentsState("error");
      }
    })();
    return () => {
      live = false;
    };
  }, [expandedId]);

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
    requestAnimationFrame(() => flashRow(nodeId));
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

  async function submitComment(e: React.FormEvent) {
    e.preventDefault();
    const content = commentDraft.trim();
    if (!content || !expandedId || addingComment) return;
    setAddingComment(true);
    setCommentError(null);
    try {
      const added = await addComment(expandedId, { actor: "user", content });
      setComments((cs) => [...cs, added]);
      setCommentDraft("");
    } catch (err) {
      setCommentError(err instanceof Error ? err.message : "Couldn't add the comment");
    } finally {
      setAddingComment(false);
    }
  }

  // Live jump: an exact clause-number match navigates as the operator types the
  // number the counterparty just said. Enter falls back to the first prefix match.
  function onJumpChange(v: string) {
    setJumpVal(v);
    const exact = clauseByNumber.get(v.trim());
    if (exact) jumpTo(exact);
  }
  function onJumpEnter() {
    const q = jumpVal.trim();
    if (!q) return;
    const exact = clauseByNumber.get(q);
    if (exact) return jumpTo(exact);
    const prefix = rows.find((r) => r.number && r.number.startsWith(q));
    if (prefix) jumpTo(prefix.id);
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

  const selectedRow = selectedId ? rowById.get(selectedId) ?? null : null;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const issue = await createIssue(id, {
        node_id: selectedId,
        title: trimmed,
        our_position: noteText.trim() || null,
        initiator,
      });
      setState((s) => (s.kind === "ready" ? { ...s, issues: [issue, ...s.issues] } : s));
      setTitle("");
      setNoteText("");
      setInitiator("operator");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Couldn't raise the issue");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.screen}>
      <header className={styles.topbar}>
        <div className={styles.identity}>
          <div className={styles.brand}>
            donna<span className={styles.dot}>.</span>ai
          </div>
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
              placeholder="Jump to clause — type 7.2"
              aria-label="Jump to clause number"
              inputMode="decimal"
              autoFocus
            />
            <span className={styles.jumpKbd} aria-hidden>
              /
            </span>
          </div>
          {jumpVal.trim() && (
            <div className={styles.jumpHint}>
              {jumpMatch ? (
                <>
                  <span className={styles.jumpHintNum}>§{jumpMatch.number}</span>
                  <span className={styles.jumpHintText}>{jumpMatch.text || "(no text)"}</span>
                </>
              ) : (
                <span className={styles.jumpHintMiss}>No clause {jumpVal.trim()} — press Enter for the nearest</span>
              )}
            </div>
          )}
        </div>

        <div className={styles.right}>
          <a className={styles.navLink} href="/contracts">
            ← All contracts
          </a>
        </div>
      </header>

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
        <div className={styles.panels}>
          <section className={styles.tree}>
            <div className={styles.panelHead}>
              Clauses
              <span className={styles.panelHint}>click to anchor an issue · press / to jump</span>
            </div>
            <div className={styles.rows}>
              {visible.map((r) => {
                const isClause = r.role === "clause";
                const count = issuesByNode.get(r.id) ?? 0;
                const hasChildren = parentIds.has(r.id);
                const isCollapsed = collapsed.has(r.id);
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
                    onClick={() => setSelectedId(r.id)}
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
                      <span className={styles.roleLabel}>{titleCase(r.role)}</span>
                    )}
                    <span className={[styles.text, r.isHeading ? styles.headingText : ""].join(" ")}>
                      {r.text || <em>(no text)</em>}
                    </span>
                    {count > 0 && (
                      <span className={styles.rowIssues} title={`${count} issue${count === 1 ? "" : "s"} raised here`}>
                        {count}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          <section className={styles.rail}>
            <div className={styles.panelHead}>
              Capture
              <span className={styles.panelCount}>{issues.length} raised</span>
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
                <label className={styles.fieldLabel} htmlFor="issue-title">
                  Title
                </label>
                <input
                  id="issue-title"
                  className={styles.control}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="What's the issue?"
                  required
                />
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="issue-note">
                  Note <span style={{ fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>(optional)</span>
                </label>
                <textarea
                  id="issue-note"
                  className={[styles.control, styles.note].join(" ")}
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  placeholder="Our position, or what they're asking for"
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

              <button className={styles.submit} type="submit" disabled={!title.trim() || submitting}>
                {submitting ? "Raising…" : "Raise issue"}
              </button>
              {formError && <p className={styles.formError}>{formError}</p>}
            </form>

            <div className={styles.issues}>
              <div className={styles.issuesHead}>Raised this call</div>
              {sortedIssues.length === 0 ? (
                <p className={styles.centerHint} style={{ textAlign: "left", padding: "4px 2px" }}>
                  Nothing yet. Anchor a clause and raise the first issue.
                </p>
              ) : (
                sortedIssues.map((i) => {
                  const anchor = i.node_id ? rowById.get(i.node_id) ?? null : null;
                  const isCp = i.initiator === "counterparty";
                  const isDonna = i.initiator === "donna";
                  const status = asStatus(i.status);
                  const isExpanded = expandedId === i.id;
                  const statusBusy = statusBusyId === i.id;
                  return (
                    <div key={i.id} className={[styles.issueCard, isExpanded ? styles.issueCardOpen : ""].join(" ")}>
                      <div className={styles.issueTop}>
                        <span
                          className={[styles.issueAnchor, i.node_id ? "" : styles.issueAnchorNone].join(" ")}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (i.node_id) jumpTo(i.node_id);
                          }}
                          style={{ cursor: i.node_id ? "pointer" : "default" }}
                          title={i.node_id ? "Jump to clause" : undefined}
                        >
                          {anchor ? (anchor.number ? `§${anchor.number}` : titleCase(anchor.role)) : "Contract"}
                        </span>
                        <span className={[styles.status, styles[STATUS_CLASS[status]]].join(" ")}>
                          {STATUS_LABEL[status]}
                        </span>
                        <span
                          className={[styles.who, isCp ? styles.whoCp : isDonna ? styles.whoDonna : styles.whoUs].join(
                            " ",
                          )}
                        >
                          {isCp ? "Counterparty" : isDonna ? "Donna" : "Us"}
                        </span>
                      </div>

                      <button
                        type="button"
                        className={styles.issueBody}
                        aria-expanded={isExpanded}
                        onClick={() => setExpandedId((cur) => (cur === i.id ? null : i.id))}
                      >
                        <span className={styles.issueTitle}>{i.title}</span>
                        {i.our_position && <span className={styles.issueNote}>{i.our_position}</span>}
                        <span className={styles.issueExpand}>
                          {isExpanded ? "Hide detail ▾" : "Detail & comments ▸"}
                        </span>
                      </button>

                      {isExpanded && (
                        <div className={styles.issueDetail}>
                          <div className={styles.statusRow}>
                            <label className={styles.detailLabel} htmlFor={`status-${i.id}`}>
                              Status
                            </label>
                            <select
                              id={`status-${i.id}`}
                              className={styles.statusSelect}
                              value={status}
                              disabled={statusBusy}
                              onChange={(e) => changeStatus(i.id, e.target.value as IssueStatus)}
                            >
                              {STATUS_ORDER.map((s) => (
                                <option key={s} value={s}>
                                  {STATUS_LABEL[s]}
                                </option>
                              ))}
                            </select>
                            {statusBusy && <span className={styles.statusBusy}>Saving…</span>}
                          </div>

                          {i.their_position && (
                            <div className={styles.detailField}>
                              <span className={styles.detailLabel}>Their position</span>
                              <p className={styles.detailText}>{i.their_position}</p>
                            </div>
                          )}

                          <div className={styles.thread}>
                            <div className={styles.detailLabel}>Comments</div>
                            {commentsState === "loading" ? (
                              <p className={styles.threadMuted}>Loading comments…</p>
                            ) : commentsState === "error" ? (
                              <p className={styles.threadError}>Couldn&apos;t load comments.</p>
                            ) : comments.length === 0 ? (
                              <p className={styles.threadMuted}>No comments yet. Add the first below.</p>
                            ) : (
                              <ul className={styles.commentList}>
                                {comments.map((c) => (
                                  <li
                                    key={c.id}
                                    className={[styles.comment, c.actor === "user" ? styles.commentUser : ""].join(" ")}
                                  >
                                    <div className={styles.commentMeta}>
                                      <span className={styles.commentAuthor}>{commentAuthor(c.actor)}</span>
                                      <span className={styles.commentTime}>{shortTime(c.created_at)}</span>
                                    </div>
                                    <p className={styles.commentBody}>{c.content}</p>
                                  </li>
                                ))}
                              </ul>
                            )}

                            <form className={styles.commentForm} onSubmit={submitComment}>
                              <textarea
                                className={[styles.control, styles.commentInput].join(" ")}
                                value={commentDraft}
                                onChange={(e) => setCommentDraft(e.target.value)}
                                placeholder="Add a comment…"
                                rows={2}
                              />
                              <button
                                className={styles.commentSubmit}
                                type="submit"
                                disabled={!commentDraft.trim() || addingComment}
                              >
                                {addingComment ? "Adding…" : "Add comment"}
                              </button>
                              {commentError && <p className={styles.formError}>{commentError}</p>}
                            </form>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
