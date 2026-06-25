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
      <div className={styles.right}>{children}</div>
    </header>
  );
}
