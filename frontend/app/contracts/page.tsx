"use client";

// All contracts — the whole book of business, grouped by the counterparty each
// contract is with (clients sorted A→Z). Each contract renders as a browse card
// mirroring the home "Jump back in" cards (status badge, contract type, last
// activity, open-issue count) and opens its cockpit on click. A live search
// narrows by counterparty and/or contract type; a large set paginates so the
// page never becomes one endless scroll. Per-contract open-issue counts are
// sourced exactly as home does — one listIssues call per contract, counting
// status === "open".

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import styles from "./contracts-list.module.css";
import {
  listContracts,
  listClients,
  listContractTypes,
  listIssues,
  updateContract,
  deleteContract,
  type StoredContract,
  type StoredClient,
  type StoredContractType,
} from "../lib/api";

const PAGE_SIZE = 12;

type StatusKey = "drafting" | "under negotiation" | "signed";

const STATUS_CONFIG: Record<StatusKey, { spine: string; badge: string; label: string }> = {
  drafting: { spine: styles.spineDraft, badge: styles.badgeDraft, label: "Drafting" },
  "under negotiation": {
    spine: styles.spineNegotiating,
    badge: styles.badgeNegotiating,
    label: "In negotiation",
  },
  signed: { spine: styles.spineSigned, badge: styles.badgeSigned, label: "Signed" },
};

function statusConfig(status: string) {
  return STATUS_CONFIG[status as StatusKey] ?? STATUS_CONFIG.drafting;
}

interface CardContract {
  id: string;
  name: string;
  clientName: string;
  typeName: string;
  status: string;
  openIssues: number;
  lastActivity: string;
  createdAt: string;
}

// created_at (ISO) -> compact relative string for the card's "last activity".
// Same treatment home uses so the two surfaces read identically.
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day === 1) return "yesterday";
  if (day < 7) return `${day}d ago`;
  const wk = Math.round(day / 7);
  if (wk < 5) return `${wk}w ago`;
  return new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

interface ClientGroup {
  client: string;
  contracts: CardContract[];
}

// The flat list arrives sorted by client name, so contracts for one counterparty
// are already contiguous — grouping is a single linear pass, and it stays correct
// even after the list is sliced for the current page.
function groupByClient(items: CardContract[]): ClientGroup[] {
  const groups: ClientGroup[] = [];
  for (const c of items) {
    const last = groups[groups.length - 1];
    if (last && last.client === c.clientName) last.contracts.push(c);
    else groups.push({ client: c.clientName, contracts: [c] });
  }
  return groups;
}

function SearchIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.2-3.2" />
    </svg>
  );
}

function PencilIcon(): React.JSX.Element {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function TrashIcon(): React.JSX.Element {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

type CardMode = "idle" | "editing" | "confirm";

function ContractCard({
  c,
  delay,
  onRenamed,
  onDeleted,
}: {
  c: CardContract;
  delay: number;
  onRenamed: (id: string, name: string) => void;
  onDeleted: (id: string) => void;
}): React.JSX.Element {
  const cfg = statusConfig(c.status);
  const issuesClass = c.openIssues > 1 ? styles.issuesRed : c.openIssues === 1 ? styles.issuesHot : "";
  const cardClass = [styles.card, cfg.spine, styles.reveal].join(" ");

  const [mode, setMode] = useState<CardMode>("idle");
  const [draft, setDraft] = useState(c.name);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function saveRename(): Promise<void> {
    const next = draft.trim();
    if (next === "" || next === c.name) {
      setMode("idle");
      setDraft(c.name);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const updated = await updateContract(c.id, { name: next });
      onRenamed(c.id, updated.name);
      setMode("idle");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't rename. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function runDelete(): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      await deleteContract(c.id);
      onDeleted(c.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't delete. Try again.");
      setBusy(false);
    }
  }

  return (
    <article className={cardClass} style={{ animationDelay: `${delay}ms` }}>
      {mode === "idle" && (
        <Link
          href={`/contracts/${c.id}`}
          className={styles.cardHit}
          aria-label={`Open ${c.name}`}
        />
      )}

      <div className={styles.cardTop}>
        <span className={`${styles.badge} ${cfg.badge}`}>{cfg.label}</span>
        <span className={styles.type}>{c.typeName}</span>
        <span className={styles.activity}>{c.lastActivity}</span>
        {mode === "idle" && (
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.iconBtn}
              aria-label={`Rename ${c.name}`}
              onClick={() => {
                setDraft(c.name);
                setErr(null);
                setMode("editing");
              }}
            >
              <PencilIcon />
            </button>
            <button
              type="button"
              className={`${styles.iconBtn} ${styles.iconBtnDanger}`}
              aria-label={`Delete ${c.name}`}
              onClick={() => {
                setErr(null);
                setMode("confirm");
              }}
            >
              <TrashIcon />
            </button>
          </div>
        )}
      </div>

      {mode === "editing" ? (
        <div className={styles.renameRow}>
          <input
            className={styles.renameInput}
            value={draft}
            autoFocus
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void saveRename();
              else if (e.key === "Escape") {
                setMode("idle");
                setDraft(c.name);
              }
            }}
            aria-label="Contract name"
          />
          <button
            type="button"
            className={styles.renameSave}
            disabled={busy}
            onClick={() => void saveRename()}
          >
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            className={styles.renameCancel}
            disabled={busy}
            onClick={() => {
              setMode("idle");
              setDraft(c.name);
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className={styles.name}>{c.name}</div>
      )}

      {mode === "confirm" ? (
        <div className={styles.confirm} role="alertdialog" aria-label={`Delete ${c.name}?`}>
          <span className={styles.confirmText}>
            Delete this contract and everything in it? This can&apos;t be undone.
          </span>
          <button
            type="button"
            className={styles.confirmDelete}
            disabled={busy}
            onClick={() => void runDelete()}
          >
            {busy ? "Deleting…" : "Delete"}
          </button>
          <button
            type="button"
            className={styles.confirmCancel}
            disabled={busy}
            onClick={() => setMode("idle")}
          >
            Keep
          </button>
        </div>
      ) : (
        <div className={styles.cardFoot}>
          <span className={`${styles.issues} ${issuesClass}`}>
            <span className={styles.issuesNum}>{c.openIssues}</span> open{" "}
            {c.openIssues === 1 ? "issue" : "issues"}
          </span>
          <span className={styles.open}>
            Open
            <span className={styles.openArrow} aria-hidden>
              →
            </span>
          </span>
        </div>
      )}

      {err && (
        <div className={styles.cardError} role="alert">
          {err}
        </div>
      )}
    </article>
  );
}

function Loading(): React.JSX.Element {
  return (
    <div className={styles.loading} role="status" aria-live="polite">
      <div className={styles.loadingTrack} aria-hidden>
        <div className={styles.loadingFill} />
      </div>
      <span className={styles.loadingLabel}>Loading your contracts…</span>
    </div>
  );
}

function EmptyState(): React.JSX.Element {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyMark} aria-hidden>
        +
      </div>
      <div className={styles.emptyTitle}>No contracts yet</div>
      <p className={styles.emptyHint}>
        Import a contract and donna.ai builds its clause tree — then it lands here, filed under its
        counterparty.
      </p>
      <Link href="/import" className={styles.emptyCta}>
        Import your first contract
      </Link>
    </div>
  );
}

function NoMatches({ query, onClear }: { query: string; onClear: () => void }): React.JSX.Element {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyTitle}>No contracts match “{query}”</div>
      <p className={styles.emptyHint}>
        Nothing filed under a counterparty or contract type by that name. Try a shorter term.
      </p>
      <button type="button" className={styles.emptyCta} onClick={onClear}>
        Clear search
      </button>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }): React.JSX.Element {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyTitle}>Couldn&apos;t load your contracts</div>
      <p className={styles.emptyHint}>
        donna.ai couldn&apos;t reach the workspace. Check the backend is running, then try again.
      </p>
      <button type="button" className={styles.emptyCta} onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

export default function AllContracts(): React.JSX.Element {
  const [cards, setCards] = useState<CardContract[] | null>(null);
  const [error, setError] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  function load(): void {
    setCards(null);
    setError(false);

    void (async () => {
      try {
        const [contracts, clients, types] = await Promise.all([
          listContracts(),
          listClients(),
          listContractTypes(),
        ]);

        const clientName = new Map<string, string>(
          clients.map((c: StoredClient) => [c.id, c.name]),
        );
        const typeName = new Map<string, string>(
          types.map((t: StoredContractType) => [t.id, t.name]),
        );

        const openCounts = await Promise.all(
          contracts.map((c: StoredContract) =>
            listIssues(c.id)
              .then((issues) => issues.filter((i) => i.status === "open").length)
              .catch(() => 0),
          ),
        );

        const built: CardContract[] = contracts.map((c: StoredContract, i: number) => ({
          id: c.id,
          name: c.name,
          clientName: clientName.get(c.client_id) ?? "Unknown counterparty",
          typeName: typeName.get(c.contract_type_id) ?? "Contract",
          status: c.status,
          openIssues: openCounts[i],
          lastActivity: relativeTime(c.created_at),
          createdAt: c.created_at,
        }));

        // Counterparty A→Z; within a counterparty, most recent first.
        built.sort(
          (a, b) =>
            a.clientName.localeCompare(b.clientName) ||
            new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
        );

        setCards(built);
      } catch {
        setError(true);
      }
    })();
  }

  useEffect(load, []);

  function handleRenamed(id: string, name: string): void {
    setCards((prev) => (prev ? prev.map((c) => (c.id === id ? { ...c, name } : c)) : prev));
  }
  function handleDeleted(id: string): void {
    setCards((prev) => (prev ? prev.filter((c) => c.id !== id) : prev));
  }

  const filtered = useMemo(() => {
    if (cards === null) return [];
    const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return cards;
    return cards.filter((c) => {
      const hay = `${c.clientName} ${c.typeName} ${c.name}`.toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
  }, [cards, query]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(pageStart, pageStart + PAGE_SIZE);
  const groups = groupByClient(pageItems);

  function onSearch(value: string): void {
    setQuery(value);
    setPage(1);
  }

  let body: React.JSX.Element;
  if (error) {
    body = <ErrorState onRetry={load} />;
  } else if (cards === null) {
    body = <Loading />;
  } else if (cards.length === 0) {
    body = <EmptyState />;
  } else if (filtered.length === 0) {
    body = <NoMatches query={query.trim()} onClear={() => onSearch("")} />;
  } else {
    const counterpartyCount = new Set(filtered.map((c) => c.clientName)).size;
    const rangeEnd = pageStart + pageItems.length;
    let runningDelay = 0;

    body = (
      <>
        <p className={styles.summary}>
          Showing <strong>{pageStart + 1}–{rangeEnd}</strong> of {filtered.length}{" "}
          {filtered.length === 1 ? "contract" : "contracts"} ·{" "}
          {counterpartyCount} {counterpartyCount === 1 ? "counterparty" : "counterparties"}
        </p>

        {groups.map((g) => (
          <section key={g.client} className={styles.group}>
            <div className={styles.groupHeader}>
              <h2 className={styles.groupName}>{g.client}</h2>
              <span className={styles.groupCount}>{g.contracts.length}</span>
              <span className={styles.groupRule} aria-hidden />
            </div>
            <div className={styles.list}>
              {g.contracts.map((c) => (
                <ContractCard
                  key={c.id}
                  c={c}
                  delay={runningDelay++ * 45}
                  onRenamed={handleRenamed}
                  onDeleted={handleDeleted}
                />
              ))}
            </div>
          </section>
        ))}

        {totalPages > 1 && (
          <nav className={styles.pager} aria-label="Contract pages">
            <button
              type="button"
              className={styles.pageBtn}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
            >
              ← Prev
            </button>
            <span className={styles.pageInfo}>
              Page {currentPage} of {totalPages}
            </span>
            <button
              type="button"
              className={styles.pageBtn}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
            >
              Next →
            </button>
          </nav>
        )}
      </>
    );
  }

  const showSearch = cards !== null && !error && cards.length > 0;

  return (
    <div className={styles.screen}>
      <main className={styles.body}>
        <div className={styles.eyebrow}>
          <span className={styles.eyebrowDot} aria-hidden />
          Counterparty book
        </div>
        <h1 className={styles.title}>All contracts</h1>
        <p className={styles.lead}>
          Every contract on the desk, filed under the counterparty it&apos;s with. Open one to run
          the call.
        </p>

        {showSearch && (
          <div className={styles.searchBar}>
            <span className={styles.searchIcon} aria-hidden>
              <SearchIcon />
            </span>
            <input
              className={styles.searchInput}
              type="search"
              value={query}
              onChange={(e) => onSearch(e.target.value)}
              placeholder="Search by counterparty or contract type…"
              aria-label="Search contracts by counterparty or contract type"
            />
            {query !== "" && (
              <button
                type="button"
                className={styles.searchClear}
                onClick={() => onSearch("")}
                aria-label="Clear search"
              >
                ×
              </button>
            )}
          </div>
        )}

        {body}
      </main>
    </div>
  );
}
