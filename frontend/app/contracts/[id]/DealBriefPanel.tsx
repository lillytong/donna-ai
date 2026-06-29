"use client";

// F37 / DD-95 — Deal-brief slide-over panel.
//
// Donna distils a per-contract brief from one whole-contract read at import
// (~50s, background job). The operator can review it in read view, edit it
// (F32 editor pattern: draft/saved/dirty state, dirty-gated [Save]), or
// force a re-distil ([Refresh] -> confirm -> POST .../refresh -> staged loading).
//
// Because the backend has no "job running" status field (v1 known limitation —
// see DD-95), an empty brief at drawer-open is approximated as "pending": we
// poll GET up to 3 times (~15s) before giving up and showing the absent state.
//
// Open/close primitive: fixed overlay backdrop (click = close) + Escape keydown.
// This is the same mechanism as the lineage drawer, adapted for a full-height panel.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type DealBrief,
  getDealBrief,
  saveDealBrief,
  refreshDealBrief,
} from "../../lib/api";
import styles from "./DealBriefPanel.module.css";

// Phase labels cycled while the ~50s re-distillation runs.
const DISTILL_PHASES = [
  "Donna is re-reading the whole contract...",
  "Identifying parties and interests...",
  "Mapping the economic spine...",
  "Almost ready (~50s)...",
];

// ---- Minimal markdown-ish renderer ----------------------------------------
// Handles **bold**, *italic*, unordered list items (- / *), and section
// headings (**Heading** alone on a line). No external dependency.
// Same approach as renderDonnaMarkdown in cockpit page.tsx (local to that file).

function renderInline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  // Match **bold** before *italic* to avoid greedy single-star matches.
  const regex = /(\*\*(.+?)\*\*|\*(.+?)\*)/g;
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  let k = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(<span key={k++}>{text.slice(lastIdx, match.index)}</span>);
    }
    if (match[0].startsWith("**")) {
      parts.push(<strong key={k++}>{match[2]}</strong>);
    } else {
      parts.push(<em key={k++}>{match[3]}</em>);
    }
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < text.length) {
    parts.push(<span key={k++}>{text.slice(lastIdx)}</span>);
  }
  return parts;
}

function renderBriefContent(text: string): React.ReactNode {
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  const listItems: string[] = [];
  let key = 0;

  function flushList() {
    if (listItems.length === 0) return;
    nodes.push(
      <ul key={key++} className={styles.briefList}>
        {listItems.map((item, i) => (
          <li key={i}>{renderInline(item)}</li>
        ))}
      </ul>
    );
    listItems.length = 0;
  }

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (line === "") {
      flushList();
      continue; // blank lines become inter-paragraph gap via CSS margin
    }

    // Unordered list item: "- " or "* " prefix
    if (/^[-*] /.test(line)) {
      listItems.push(line.slice(2));
      continue;
    }

    flushList();

    // Section heading: **Heading** alone on a line (optional trailing colon)
    const headingMatch = line.match(/^\*\*(.+?)\*\*\s*:?\s*$/);
    if (headingMatch) {
      nodes.push(
        <h3 key={key++} className={styles.briefHeading}>
          {headingMatch[1]}
        </h3>
      );
      continue;
    }

    nodes.push(
      <p key={key++} className={styles.briefPara}>
        {renderInline(line)}
      </p>
    );
  }

  flushList();
  return <>{nodes}</>;
}

// ---- Timestamp formatting --------------------------------------------------

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

// ---- CheckIcon (same dimensions as in settings/page.tsx) ------------------

function CheckIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="#15803d"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="2 6 5 9 10 3" />
    </svg>
  );
}

// ---- Panel -----------------------------------------------------------------

interface Props {
  contractId: string;
  onClose: () => void;
}

export function DealBriefPanel({ contractId, onClose }: Props) {
  // Panel load state (isolated from the cockpit page bundle)
  const [panelStatus, setPanelStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  // loadKey increments to re-trigger the load effect (e.g., after a retry)
  const [loadKey, setLoadKey] = useState(0);
  // True after all poll attempts exhausted with still-empty content
  const [pollDone, setPollDone] = useState(false);

  // Brief data — F32 pattern: saved = server copy, draft = textarea buffer
  const [brief, setBrief] = useState<DealBrief | null>(null);
  const [saved, setSaved] = useState("");
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Refresh (POST .../refresh — ~50s Opus distillation)
  const [refreshConfirm, setRefreshConfirm] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [phaseIdx, setPhaseIdx] = useState(0);

  // ---- Escape to close (same pattern as lineage drawer) ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // ---- Load the brief; poll if empty (approximates the pending seed job) ----
  // Known v1 limitation: no backend status field distinguishes "distillation
  // pending" from "absent", so we poll GET up to 3 times (5s apart, ~15s total).
  // A clean fix (status column + status endpoint) is a Part-A follow-up (DD-95).
  useEffect(() => {
    let cancelled = false;
    setPanelStatus("loading");
    setLoadError(null);
    setPollDone(false);
    setBrief(null);

    async function load(attemptsLeft: number): Promise<void> {
      if (cancelled) return;
      try {
        const b = await getDealBrief(contractId);
        if (cancelled) return;
        if (b.content || attemptsLeft <= 0) {
          // Either we have content, or we have exhausted our polls
          setBrief(b);
          setSaved(b.content);
          setDraft(b.content);
          setPollDone(!b.content); // true = poll exhausted with no content
          setPanelStatus("ready");
        } else {
          // Empty brief — likely still being seeded; wait 5s and retry
          await new Promise<void>((res) => {
            const t = setTimeout(res, 5000);
            // store the timer so we can clear it if the component unmounts mid-poll
            if (cancelled) clearTimeout(t);
          });
          return load(attemptsLeft - 1);
        }
      } catch (e) {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : "Could not load the deal brief.");
        setPanelStatus("error");
      }
    }

    void load(3); // up to 3 polls (~15s total) before showing the absent state

    return () => {
      cancelled = true;
      if (savedTimer.current) clearTimeout(savedTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contractId, loadKey]);

  // ---- Cycle distillation phase labels while refresh runs ----
  useEffect(() => {
    if (!refreshing) {
      setPhaseIdx(0);
      return;
    }
    const t = window.setInterval(
      () => setPhaseIdx((p) => (p + 1) % DISTILL_PHASES.length),
      2000
    );
    return () => window.clearInterval(t);
  }, [refreshing]);

  // ---- F32 save pattern (dirty-gated, 4s "Saved" flash) ----
  const dirty = draft !== saved;

  const handleSave = useCallback(async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setSaveError(null);
    setJustSaved(false);
    if (savedTimer.current) clearTimeout(savedTimer.current);
    try {
      const updated = await saveDealBrief(contractId, draft);
      setSaved(updated.content);
      setDraft(updated.content);
      setBrief(updated); // provenance flips: operator_edited=true, updated_at refreshed
      setJustSaved(true);
      savedTimer.current = setTimeout(() => setJustSaved(false), 4000);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Could not save the deal brief.");
    } finally {
      setSaving(false);
    }
  }, [contractId, draft, dirty, saving]);

  const handleCancelEdit = useCallback(() => {
    setDraft(saved); // discard unsaved changes
    setEditing(false);
    setSaveError(null);
  }, [saved]);

  // ---- Refresh (force re-distil) ----
  const handleRefresh = useCallback(async () => {
    setRefreshConfirm(false);
    setRefreshing(true);
    setRefreshError(null);
    setEditing(false);
    setSaveError(null);
    try {
      const newBrief = await refreshDealBrief(contractId);
      setBrief(newBrief);
      setSaved(newBrief.content);
      setDraft(newBrief.content);
      setPollDone(false);
      setPanelStatus("ready");
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : "Refresh failed. Please try again.");
    } finally {
      setRefreshing(false);
    }
  }, [contractId]);

  // ---- Provenance label (operator_edited drives the copy) ----
  function provenanceLabel(b: DealBrief): string {
    if (b.operator_edited) {
      const ts = b.updated_at ? formatTs(b.updated_at) : "";
      return ts ? `Edited by you · ${ts}` : "Edited by you";
    }
    const ts = b.generated_at ? formatTs(b.generated_at) : "";
    return ts ? `Drafted by Donna · ${ts}` : "Drafted by Donna";
  }

  // ---- Body content ----
  function renderBody(): React.ReactNode {
    // Error state with retry
    if (panelStatus === "error") {
      return (
        <div className={styles.errorState}>
          <p className={styles.errorMsg}>{loadError ?? "Could not load the deal brief."}</p>
          <button
            type="button"
            className={styles.btnGhost}
            onClick={() => setLoadKey((k) => k + 1)}
          >
            Retry
          </button>
        </div>
      );
    }

    // Initial load / polling for pending seed
    if (panelStatus === "loading") {
      return (
        <div className={styles.loadingState}>
          <span className={styles.loadingSpinner} aria-hidden="true" />
          <p className={styles.loadingLabel}>Donna is building the deal brief...</p>
          <p className={styles.loadingHint}>(~50s on first import)</p>
        </div>
      );
    }

    // Refresh in-flight — phase-cycling spinner (same pattern as Donna chat)
    if (refreshing) {
      return (
        <div className={styles.loadingState}>
          <span className={styles.loadingSpinner} aria-hidden="true" />
          <p className={styles.loadingLabel}>{DISTILL_PHASES[phaseIdx]}</p>
          <p className={styles.loadingHint}>(~50s)</p>
        </div>
      );
    }

    // Confirm dialog replaces body while waiting for the operator to confirm
    if (refreshConfirm) {
      return (
        <div className={styles.confirmBox} role="alertdialog" aria-label="Confirm refresh">
          <p className={styles.confirmText}>
            This replaces the current brief, including your edits. Continue?
          </p>
          <div className={styles.confirmActions}>
            <button
              type="button"
              className={styles.btnGhost}
              onClick={() => setRefreshConfirm(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className={styles.btnDanger}
              onClick={handleRefresh}
            >
              Replace
            </button>
          </div>
        </div>
      );
    }

    // Absent — poll exhausted or brief is genuinely empty
    if (pollDone || !brief?.content) {
      return (
        <div className={styles.absentState}>
          <p className={styles.absentCopy}>
            No deal brief yet. Donna builds one automatically at import (~50s);
            generate it now if it did not run.
          </p>
          <button
            type="button"
            className={styles.btnGhost}
            onClick={handleRefresh}
          >
            Generate deal brief
          </button>
        </div>
      );
    }

    // Edit view — F32 textarea
    if (editing) {
      return (
        <textarea
          className={styles.editTextarea}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Deal brief content"
          disabled={saving}
        />
      );
    }

    // Read view — formatted prose
    return (
      <div className={styles.briefContent}>
        {renderBriefContent(brief.content)}
      </div>
    );
  }

  // ---- Footer actions (mirrors FirmProfileSection) ----
  function renderFooter(): React.ReactNode {
    // No footer during loading, error, pending, refresh, or confirm
    if (
      panelStatus !== "ready" ||
      refreshing ||
      refreshConfirm ||
      pollDone ||
      !brief?.content
    ) {
      return null;
    }

    if (editing) {
      return (
        <div className={styles.footer}>
          {saveError && (
            <p className={styles.errorInline} role="alert">
              {saveError}
            </p>
          )}
          <div className={styles.actions}>
            <div className={styles.statusLine} aria-live="polite">
              {saving ? (
                <span className={styles.statusSaving}>
                  <span className={styles.statusSpinner} aria-hidden="true" />
                  Saving...
                </span>
              ) : justSaved ? (
                <span className={styles.statusSaved}>
                  <CheckIcon />
                  Saved
                </span>
              ) : dirty ? (
                <span className={styles.statusDirty}>Unsaved changes</span>
              ) : null}
            </div>
            <button
              type="button"
              className={styles.btnGhost}
              onClick={handleCancelEdit}
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleSave}
              disabled={!dirty || saving}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      );
    }

    // Read-mode footer
    return (
      <div className={styles.footer}>
        {refreshError && (
          <p className={styles.errorInline} role="alert">
            {refreshError}
          </p>
        )}
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.btnGhost}
            onClick={() => setRefreshConfirm(true)}
          >
            Refresh
          </button>
          <button
            type="button"
            className={styles.btnGhost}
            onClick={() => {
              setEditing(true);
              setJustSaved(false);
            }}
          >
            Edit
          </button>
        </div>
      </div>
    );
  }

  // ---- Panel render ----
  return (
    // Backdrop: click on the dark overlay closes the panel (outside-click).
    // onMouseDown avoids edge cases where a drag starting inside ends outside.
    <div
      className={styles.backdrop}
      onMouseDown={onClose}
      aria-hidden="true"
    >
      {/* Panel: stopPropagation so clicks inside do not bubble to the backdrop */}
      <div
        className={styles.panel}
        role="dialog"
        aria-label="Deal brief"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className={styles.header}>
          <h2 className={styles.headerTitle}>Deal brief</h2>
          <button
            type="button"
            className={styles.closeBtn}
            onClick={onClose}
            aria-label="Close deal brief"
          >
            &times;
          </button>
        </div>

        {/* Provenance line — visible in read view only */}
        {brief && brief.content && !editing && !refreshing && !refreshConfirm && panelStatus === "ready" && (
          <p
            className={[
              styles.provenance,
              brief.operator_edited ? styles.provenanceEdited : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {provenanceLabel(brief)}
          </p>
        )}

        {/* Scrollable body */}
        <div className={styles.body}>{renderBody()}</div>

        {/* Footer actions */}
        {renderFooter()}
      </div>
    </div>
  );
}
