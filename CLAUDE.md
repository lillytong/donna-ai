# donna.ai — Project Engineering Rules

Extends the AI Projects standards in `../CLAUDE.md`. Those rules are in force;
this file records the **project-specific decisions and deviations** only.

## Source of truth

- **`SPEC.md`** is the hub; **`DESIGN_DECISIONS.md`** holds the ADR log (DD-NN).
  Read both end-to-end before building. If code and spec conflict, spec wins.
- **`db/schema.sql` is the canonical data model.** The DB is the source of truth
  for contract content (principle §2.3); Word is an export artifact.

## Tracking built features (the convention — so build state is never lost to chat)

- **SPEC §5 feature registry `Status` column is the canonical at-a-glance tracker** of what's built. **`DEV_TODO.md` → Completed** is the detailed build log.
- **Every finished build does both:** flip the feature's §5 `Status` (`planned` → `backend done` / `built`) **and** add a `DEV_TODO` Completed line (what + gate result). A build isn't done until both are recorded. This is how a fresh engineering session knows where to pick up — it reads §5 + `DEV_TODO` in its pre-build gate.
- **New feature gaps found mid-build are never decided ad hoc.** Engineering gaps → an ADR in `DESIGN_DECISIONS.md`. Feature/product gaps → queued to `PM_TODO.md`; **only product writes features into the §5 registry** (after vetting), keeping it the single source of feature truth.

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

No client/counterparty/deal/person names, contract content, or parameter values
in the repo. All logic parameterized; prompts use variables; seed data is
gitignored. Run the name pass before the first commit.
