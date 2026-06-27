# donna.ai — Project Engineering Rules

Extends the AI Projects standards in `../CLAUDE.md`. Those rules are in force;
this file records the **project-specific decisions and deviations** only.

## Source of truth

- **`SPEC.md`** is the hub; **`DESIGN_DECISIONS.md`** holds the ADR log (DD-NN).
  Read both end-to-end before building. If code and spec conflict, spec wins.
- **`db/schema.sql` is the canonical data model.** The DB is the source of truth
  for contract content (principle §2.3); Word is an export artifact.

## Tracking built features (the convention — so build state is never lost to chat)

- **SPEC §5 `Status` is the canonical record of what's built;** git history + ADRs are the detailed record. `PM_TODO.md` (product) and `DEV_TODO.md` (engineering) are **open-only** working lists — neither has a Completed / build-log section.
- **A build is done when its §5 `Status` is flipped (+ committed) and the item is deleted from the working list** — promote anything load-bearing (a gotcha, a follow-up) to §5 / an ADR / a new `## Open` item first. A fresh session picks up from §5 + `## Open` + git, never a Completed log.
- **Only product writes features into §5.** Engineering gaps → an ADR in `DESIGN_DECISIONS.md`; product/feature gaps → `PM_TODO.md`.
- Per-persona list mechanics (engineering reconciliation sweep, SPEC health tracker, item formats) live in the `software-engineer` / `product-manager` skills — this section defines the convention; the personas enact it.

## Stack deviations from the default

| Area | Default | Here | Why |
|------|---------|------|-----|
| Orchestration | LangGraph/LangChain | **No LangGraph** in v1 | Flows are linear/single-branch (DD-52). `agents/ nodes/ tools/ memory/` are not scaffolded. Donna's retrieval + surfaces live in `backend/services/donna/`. Re-examine if a single-shot AI surface underperforms in evals. |
| DB access | (ORM common) | **Raw SQL + asyncpg** | Schema is owned by `db/schema.sql`, not an ORM — avoids a second source of schema truth. Repositories in `services/` issue parameterized SQL; Pydantic models validate at the app layer. |
| Retrieval | LangChain + pgvector | pgvector via SQL; LangChain added at **Phase 2** | Phase 0 import spine needs no embeddings; keeps the lockfile lean. |
| Auth | (app-level authz) | **None in v1** | Single-operator, local (DD-53). No `users` table; `actor` is a value, not an FK. Identity + auth arrive together at the v1.1 principal portal. |

## Layout (per SPEC §17)

```
backend/  api/ (thin routes) · services/ (logic, incl. services/donna/) ·
          models/ (Pydantic) · prompts/ (versioned + utils.py) · config/
db/schema.sql · frontend/ (Next.js) · evals/ · tests/{unit,integration,system}
```

## In force from the global standards (do not restate, do follow)

- Prompts in `prompts/` as versioned files; rendered only via `prompts/utils.py`.
  Never inline prompt strings in services.
- All I/O is `async`. Every LLM call logs model, token counts, latency, caller.
- Model names/temps/limits come from `config/` only — never hardcoded (DD-35).
- Structured outputs over free-text parsing. Sliding-window context, never full
  history (Donna conversations: last 10 turns + rolling summary, DD-40).
- `PM_TODO.md`, `DEV_TODO.md`, `spikes/`, `.env`, `*.bak` are gitignored.

## Privacy (open-source repo, hard rule)

No client/counterparty/deal names, **company names of any firm specific to the real
contracts** (a SugarCo partner, a counterparty org, etc.), **legal-firm names (our
outside counsel or a counterparty's — never name a real firm; use a generic label like
"[Counsel Note: …]" or a clearly fictional firm if an example is needed)**, contract
content, or parameter values in the repo. All logic parameterized; prompts use
variables; seed data is gitignored. The name pass treats legal-firm names as a
flagged category; run it before any commit that will be pushed.

**Explicitly ALLOWED in the public repo (Lilly, 2026-06-26):** the operator's own first
name ("Lilly") and the AI persona names ("Katrina", "Kevin", "Agnes", "Donna"). These
are NOT a privacy category here — do not scrub them and do not flag them in audits. The
hard rule is scoped to *firm/client/counterparty/deal identifiers and real contract
data*, never personal/persona names.
