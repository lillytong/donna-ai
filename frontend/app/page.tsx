"use client";

// donna.ai home ("Jump back in"). Wired to real data: contracts joined with
// clients + deals for labels, and live open-issue counts per contract. No
// snapshot/pointer data exists yet (Phase 1), so every contract's status is the
// neutral "In flight" — richer statuses (your-move / sent / signed) arrive with
// Phase 2/3, at which point the hero section below lights up on its own.

import { useEffect, useState } from "react";
import Link from "next/link";
import styles from "./home.module.css";
import {
  listContracts,
  listClients,
  listDeals,
  listIssues,
  type StoredContract,
  type StoredClient,
  type StoredDeal,
} from "./lib/api";

type Status =
  | "In flight"
  | "Sent to counterparty"
  | "Received from counterparty"
  | "Sent to legal"
  | "Signed";

interface ResumeContract {
  id: string;
  name: string;
  client: string;
  deal: string;
  status: Status;
  openIssues: number;
  lastActivity: string;
}

// status -> spine class, badge class+label, and a plain-language cue of what's owed
const STATUS_CONFIG: Record<
  Status,
  { spine: string; badge: string; label: string; cue: (n: number) => string }
> = {
  "Received from counterparty": {
    spine: styles.move,
    badge: styles.badgeMove,
    label: "Your move",
    cue: (n) => (n === 1 ? "1 issue needs your response" : `${n} issues need your response`),
  },
  "In flight": {
    spine: styles.working,
    badge: styles.badgeWorking,
    label: "In flight",
    cue: () => "Draft in progress",
  },
  "Sent to counterparty": {
    spine: styles.sentCp,
    badge: styles.badgeSentCp,
    label: "Sent · awaiting reply",
    cue: () => "Waiting on the counterparty",
  },
  "Sent to legal": {
    spine: styles.sentLegal,
    badge: styles.badgeSentLegal,
    label: "With legal",
    cue: () => "In legal review",
  },
  Signed: {
    spine: styles.signed,
    badge: styles.badgeSigned,
    label: "Signed",
    cue: () => "Fully executed",
  },
};

// created_at (ISO) -> compact relative string for the card's "last activity".
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

function ResumeCard({
  c,
  hero,
  delay,
}: {
  c: ResumeContract;
  hero: boolean;
  delay: number;
}): React.JSX.Element {
  const cfg = STATUS_CONFIG[c.status];
  const manyIssues = c.openIssues > 1;
  const hot = c.openIssues > 0 && c.status === "Received from counterparty";
  const issuesClass = manyIssues ? styles.issuesRed : hot ? styles.issuesHot : "";
  const cardClass = [styles.card, cfg.spine, hero ? styles.hero : "", styles.reveal]
    .filter(Boolean)
    .join(" ");

  return (
    <Link href={`/contracts/${c.id}`} className={cardClass} style={{ animationDelay: `${delay}ms` }}>
      <div className={styles.cardTop}>
        <span className={`${styles.badge} ${cfg.badge}`}>{cfg.label}</span>
        <span className={styles.cue}>{cfg.cue(c.openIssues)}</span>
        <span className={styles.activity}>{c.lastActivity}</span>
      </div>

      <div className={styles.name}>{c.name}</div>
      <div className={styles.meta}>
        {c.client}
        <span className={styles.metaSep}>·</span>
        {c.deal}
      </div>

      <div className={styles.cardFoot}>
        <span className={`${styles.issues} ${issuesClass}`}>
          <span className={styles.issuesNum}>{c.openIssues}</span> open{" "}
          {c.openIssues === 1 ? "issue" : "issues"}
        </span>
        <span className={styles.resume}>
          {hero ? "Resume" : "Open"}
          <span className={styles.resumeArrow} aria-hidden>
            →
          </span>
        </span>
      </div>
    </Link>
  );
}

function EmptyState(): React.JSX.Element {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyMark} aria-hidden>
        +
      </div>
      <div className={styles.emptyTitle}>Nothing in flight yet</div>
      <p className={styles.emptyHint}>
        Import a contract and donna.ai builds its clause tree — then it lands here, ready to resume.
      </p>
      <Link href="/import" className={styles.emptyCta}>
        Import your first contract
      </Link>
    </div>
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

export default function Home(): React.JSX.Element {
  const [contracts, setContracts] = useState<ResumeContract[] | null>(null);
  const [error, setError] = useState(false);

  function load(): void {
    setContracts(null);
    setError(false);

    void (async () => {
      try {
        const [rawContracts, clients, deals] = await Promise.all([
          listContracts(),
          listClients(),
          listDeals(),
        ]);

        const clientName = new Map<string, string>(
          clients.map((c: StoredClient) => [c.id, c.name]),
        );
        const dealName = new Map<string, string>(deals.map((d: StoredDeal) => [d.id, d.name]));

        const sorted = [...rawContracts].sort(
          (a: StoredContract, b: StoredContract) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );

        const openCounts = await Promise.all(
          sorted.map((c) =>
            listIssues(c.id)
              .then((issues) => issues.filter((i) => i.status === "open").length)
              .catch(() => 0),
          ),
        );

        const resume: ResumeContract[] = sorted.map((c, i) => ({
          id: c.id,
          name: c.name,
          client: clientName.get(c.client_id) ?? "Unknown client",
          deal: dealName.get(c.deal_id) ?? "Unknown deal",
          status: "In flight",
          openIssues: openCounts[i],
          lastActivity: relativeTime(c.created_at),
        }));

        setContracts(resume);
      } catch {
        setError(true);
      }
    })();
  }

  useEffect(load, []);

  let content: React.JSX.Element;
  if (error) {
    content = <ErrorState onRetry={load} />;
  } else if (contracts === null) {
    content = <Loading />;
  } else if (contracts.length === 0) {
    content = <EmptyState />;
  } else {
    const yourMove = contracts.filter((c) => c.status === "Received from counterparty");
    const rest = contracts.filter((c) => c.status !== "Received from counterparty");
    const moveCount = yourMove.length;

    content = (
      <>
        <div className={styles.eyebrow}>
          <span className={styles.eyebrowDot} aria-hidden />
          {moveCount > 0 ? (
            <span className={styles.eyebrowMove}>
              {moveCount} {moveCount === 1 ? "contract" : "contracts"} waiting on you
            </span>
          ) : (
            <span>All caught up</span>
          )}
        </div>
        <h1 className={styles.title}>Jump back in</h1>
        <p className={styles.lead}>
          Pick up where you left off. The deals the counterparty handed back are floated to the top
          — those are the moves you owe.
        </p>

        {yourMove.length > 0 && (
          <div className={styles.list}>
            {yourMove.map((c, i) => (
              <ResumeCard key={c.id} c={c} hero delay={i * 60} />
            ))}
          </div>
        )}

        {rest.length > 0 && (
          <>
            <div className={styles.sectionLabel}>In flight</div>
            <div className={styles.list}>
              {rest.map((c, i) => (
                <ResumeCard key={c.id} c={c} hero={false} delay={(yourMove.length + i) * 60} />
              ))}
            </div>
          </>
        )}
      </>
    );
  }

  return (
    <div className={styles.screen}>
      <main className={styles.body}>{content}</main>
    </div>
  );
}
