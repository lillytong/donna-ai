# evals/

AI **output-quality** harnesses (CLAUDE.md mandate). Evals are **not tests**:

- They measure *quality* (does the model pick the right answer), not *correctness*
  of code paths. A drop here is a regression in model behaviour, not a broken build.
- They make **real LLM calls** and need a live `ANTHROPIC_API_KEY` (loaded from
  `.env` via `config/settings`, the same path prod uses).
- They live here, **separate from `tests/`**, and do **not** run in CI `pytest`
  by default (`pyproject.toml` `testpaths = ["tests"]`). Run them on demand.

## clause_search — F05b conceptual-match

Measures **TOP-1 accuracy** of the conceptual clause-search surface: given a
plain-language concept (e.g. "secrecy", "what happens if the company is bought")
and a fixed candidate clause list, does the LOW-tier (Haiku) model return the
clause node a human expects — including returning `null` when nothing reasonably
matches?

### What it reuses (vs. reimplements)

It exercises the **production** surface, not a copy:

- prompt: the versioned `backend/prompts/clause_search_v1.txt` via `render`;
- candidate block: prod's `build_candidate_block` (`id :: role :: heading :: snippet`);
- parsing + hallucinated-id guard: prod's `_parse_match` + the candidate-id check;
- LLM call: prod's `backend.services.llm.complete` (LOW tier, `json_response`),
  which sources model id / temperature / api key from `config/settings`.

Only the harness loop, scoring, and the synthetic dataset are local. No DB is
touched (the candidate list comes from `dataset.json`, not `fetch_nodes`).

### Dataset

`clause_search/dataset.json` is **fully synthetic**: an invented generic Master
Services Agreement with ~20 candidate clauses and ~20 plain-language eval cases
(two are `expected_id: null` to test the "no reasonable match" path). It contains
**no** real client / counterparty / deal / person names or real contract content
(privacy hard rule).

### Run

From the repo root (so `.env` is found):

```bash
python -m evals.clause_search.run_eval
```

Cost: ~20 cheap LOW-tier calls with a tiny `max_tokens` answer (`{"node_id": ...}`).
Requires `ANTHROPIC_API_KEY` set in `.env`.
