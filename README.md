# donna.ai

Named after Donna Paulsen from Suits.

donna.ai is an open-source, AI-native system of record for legal contract review and
negotiation management — built for founders and business-development leads who
run their own legal work without in-house counsel.

It replaces the degrading "Word + tracked changes + comments + email" workflow:
contracts are imported as **structured data**, issues are tracked **per clause**,
an AI assistant (**Donna**) brainstorms and explains **grounded in the actual
text**, and clean redlines are exported back to Word on demand.

> Status: early build. Architecture and data model are locked (`SPEC.md`,
> `DESIGN_DECISIONS.md`). The Phase 0 import spine — parse → classify roles →
> review → commit — and its import-review UI are **built and working**; issue
> tracking, Donna's surfaces, and export are next.

## What it does

- **Import** a `.docx` into a structured clause tree (the DB, not Word, is canonical).
- **Track issues** per clause — raise, brainstorm with Donna, resolve, log the decision.
- **Donna** answers and drafts grounded in the contract, cited to the clause, and
  says "get a lawyer" rather than bluffing past her limits.
- **Export** a counterparty-ready redline (`.docx` tracked changes) with verified
  round-trip content fidelity.

## Architecture

Database-centric: every transform reads from and writes to Postgres. Word is an
artifact at the edges, never the source of truth.

```
                  ┌─────────────────────────── Next.js frontend ───────────────────────────┐
                  │   clause tree · issue cockpit · Donna panel · (v1.1) principal portal    │
                  └───────────────────────────────────┬─────────────────────────────────────┘
                                                       │  HTTP (JSON)
                  ┌────────────────────────────────────▼─────────────────────────────────────┐
                  │  FastAPI (async)   api/ thin routes → services/ business logic            │
                  │                                                                            │
   .docx  ──IMPORT──►  parse (python-docx + OOXML) → node tree → detect refs/terms/params      │
                  │        → import-review UI → COMMIT                                          │
                  │                                                                            │
                  │   services/donna/  ── grounded Q&A · issue recs · revision review ──►  Claude
                  │        (LiteLLM wrapper; tiered context injection; cited answers)      (Anthropic API)
                  │                                                                            │
                  │   EXPORT  ◄── snapshot → regenerate .docx via style_config → tracked changes
                  └────────────────────────────────────┬─────────────────────────────────────┘
                                                       │
                  ┌────────────────────────────────────▼─────────────────────────────────────┐
                  │  PostgreSQL + pgvector   nodes · issues · snapshots · embeddings (Phase 2) │
                  │  db/schema.sql is the canonical data model                                 │
                  └────────────────────────────────────────────────────────────────────────────┘
```

**Local-first:** the whole stack runs on one machine. "Local" means the *app*
runs locally and calls Claude over HTTPS — not a local model. Requires outbound
internet + an Anthropic API key.

## Run it locally

Prerequisites: Docker, Python 3.12, [uv](https://docs.astral.sh/uv/), Node 20+ (frontend).

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY (DATABASE_URL has a local default)
docker compose up -d db       # Postgres + pgvector; auto-loads db/schema.sql + db/seed.sql
uv sync                       # backend deps
uv run uvicorn backend.main:app --reload   # API → localhost:8000
curl localhost:8000/health    # {"status":"ok","db":"ok"}
```

Frontend, in a second terminal:

```bash
cd frontend && npm install && npm run dev   # UI → localhost:3000
```

Your data is yours and stays local: the public repo carries only schema + seed
(`db/schema.sql`, `db/seed.sql` — no rows). Every clone runs its own Postgres;
contracts you import live only in your machine's database.

## Project map

| Path | What |
|------|------|
| `SPEC.md` | The hub — overview, workflow, data model, phased build plan |
| `DESIGN_DECISIONS.md` | ADR log (DD-NN), indexed in SPEC §8 |
| `CLAUDE.md` | Project engineering rules + stack deviations |
| `db/schema.sql` · `db/seed.sql` | Canonical data model + generic defaults (skeleton only — no data) |
| `backend/` | FastAPI app — `api/` · `services/` (incl. `services/donna/`) · `models/` · `prompts/` · `config/` |
| `frontend/` | Next.js UI — the import-review screen (parse → review → commit) is built |
| `evals/` | AI output-quality harnesses (separate from `tests/`) |

## License

Open source. No client names, contract content, or deal specifics in the repo —
all logic is parameterized.
