"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import styles from "./review.module.css";

export type ImportStep = "context" | "parse" | "review" | "commit";

const STEPS: { key: ImportStep; label: string }[] = [
  { key: "context", label: "Context" },
  { key: "parse", label: "Parse" },
  { key: "review", label: "Review" },
  { key: "commit", label: "Commit" },
];

// Mirrors SiteNav's NAV — SiteNav steps aside on /import, so the primary links
// live here too. "Import" is the active route on every step of this flow.
const NAV: { href: string; label: string; active?: boolean }[] = [
  { href: "/import", label: "Import", active: true },
  { href: "/contracts", label: "Contracts" },
  { href: "/settings", label: "Settings" },
];

// The single top bar for the focused import flow. SiteNav steps aside on /import
// (like the cockpit), so this bar is the sole chrome on every step: the brand
// returns home from anywhere, the step list marks the active phase, and the right
// slot carries each screen's own actions (upload / commit / review counter).
export default function ImportTopBar({
  active,
  children,
}: {
  active: ImportStep;
  children?: ReactNode;
}) {
  return (
    <header className={styles.topbar}>
      <Link
        href="/"
        className={styles.brand}
        aria-label="donna.ai home"
        style={{ textDecoration: "none", color: "inherit" }}
      >
        donna<span className={styles.dot}>.</span>ai
      </Link>
      <ol className={styles.steps}>
        {STEPS.map((s) => (
          <li key={s.key} className={active === s.key ? styles.stepActive : ""}>
            {s.label}
          </li>
        ))}
      </ol>
      <div className={styles.end}>
        {children && <div className={styles.right}>{children}</div>}
        <nav className={styles.primaryNav} aria-label="Primary">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={[styles.navItem, item.active ? styles.navItemActive : ""].join(" ")}
              aria-current={item.active ? "page" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
