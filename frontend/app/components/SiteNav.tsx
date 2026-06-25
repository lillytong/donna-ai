"use client";

// Persistent top bar shared across the app shell (home, import, contracts list,
// settings). The logo returns to home from anywhere; the right-side links carry
// the active-route marker so the operator always knows where they are.
//
// The contract cockpit (/contracts/[id]) is an immersive working surface with
// its own chrome (the clause-jump field is its hero) — the global bar steps
// aside there so it keeps the full viewport. That route owns its own header.

import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./site-nav.module.css";

interface NavItem {
  href: string;
  label: string;
}

const NAV: NavItem[] = [
  { href: "/import", label: "Import" },
  { href: "/contracts", label: "Contracts" },
  { href: "/settings", label: "Settings" },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/contracts" ? pathname.startsWith("/contracts") : pathname === href;
}

export default function SiteNav(): React.JSX.Element | null {
  const pathname = usePathname();
  // The cockpit and the import flow are focused, self-contained surfaces that own
  // their own top bar (ImportTopBar on /import) — the global bar steps aside so it
  // never doubles up. Every other route keeps the persistent SiteNav.
  if (/^\/contracts\/.+/.test(pathname) || pathname === "/import") return null;

  return (
    <header className={styles.bar}>
      <Link href="/" className={styles.brand} aria-label="donna.ai home">
        donna<span className={styles.dot}>.</span>ai
      </Link>
      <nav className={styles.nav} aria-label="Primary">
        {NAV.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={[styles.link, active ? styles.active : ""].join(" ")}
              aria-current={active ? "page" : undefined}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
