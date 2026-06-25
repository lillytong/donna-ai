# donna.ai — Technical Specification

> Living document. Update this **before** changing anything in code.
> It is cheaper to change the spec than to rebuild a feature.
> Last updated: 2026-06-21 (DDs extracted to DESIGN_DECISIONS.md — SPEC is now the hub, design records linked out; latest: DD-48 version pointers, DD-47 external revision engine, DD-46 first import clean-only, DD-45 spike #1)
> Design decisions (DD-NN) live in [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md); §8 holds the index.

---

## 1. Project Overview

donna.ai is an open-source legal contract review and negotiation management tool for founders and business-development leads who run their own legal work without in-house counsel.

It replaces the degrading "Word + tracked changes + comments + email" workflow with an AI-native system of record: contracts are imported as structured data, issues are tracked per clause, an AI assistant ("Donna") brainstorms and explains grounded in the actual text, and clean redlines are exported back to Word on demand.

**Target user (v1):** a single **operator** managing 2–5 concurrent agreements with one counterparty, optionally reporting to a **principal** (an owner/exec) who decides escalated points but is not in the day-to-day. No team collaboration beyond that.

**Open-source commitment (hard rule):** no client names, contract content, parameter values, or deal specifics in the repo. All logic is fully parameterized; prompt templates use variables only; any personal seed data lives in gitignored files. Every example in this spec is generic.

---

## 2. Product Principles

These govern every design decision. When a feature conflicts with a principle, the principle wins.

1. **Content integrity is sacred.** Round-tripping a contract through donna.ai must never alter its *content* — wording, punctuation, numbering, tables, special characters. Formatting may be normalized to a house style; meaning may not drift.
2. **Additive, never a forced switch.** donna.ai rides alongside the existing Word workflow and earns trust incrementally. It must never require abandoning the Word safety net before it has proven itself.
3. **The database is the source of truth.** Word documents are an export artifact, not the canonical source.
4. **Trust over features.** The tool is used to brief a principal and screen-share to a counterparty. A correctness failure costs credibility, not just time. Correctness is the long pole, always.
5. **Donna has a behavioral contract** (§7). She is grounded, honest about her limits, asks before assuming, and advocates for the operator while optimizing for a signable deal.

---

## 3. Core User Workflow

Two negotiation paths, both supported:

**Path A — Operator drives all edits (verbal counterparty input)**
```
First import .docx  →  Review & correct parse  →  Browse clause tree
     →  During negotiation round (live call or async), per clause:
            ├─ raise an issue, or
            └─ make a direct inline edit
     →  Brainstorm open issues with Donna (grounded, cited)
     →  Cut a snapshot ("send to counterparty")
     →  Export redlined .docx
     →  Repeat each round
```

**Path B — Counterparty sends back a revised .docx**
```
Receive counterparty-revised .docx
     →  Counterparty revision import (triggered from within cockpit)
     →  Donna diffs incoming .docx against last snapshot
            ├─ Path A: extract tracked changes from .docx XML directly
            └─ Path B: parse clean .docx → match nodes → text diff against snapshot
     →  Counterparty change review flow (one change at a time):
            ├─ Donna shows change inline as tracked markup (strikethrough/underline)
            ├─ Donna produces verdict + one-line reasoning + exact counter-language
            └─ Operator: Accept theirs | Use Donna's counter | Edit Donna's counter | Keep original
     →  All decisions logged as learning signals for future recommendations
     →  Unresolved changes remain as open issues in cockpit
     →  Cut snapshot, export redline, continue
```

Both paths merge back into the same cockpit. A negotiation round may involve one path, the other, or both. There is no separate "call mode" (DD-08).

**Intended negotiation cadence (DD-39):** When a counterparty round involves both a legal team revision and verbal feedback from the counterparty contact, the intended sequence is: (1) legal team sends their redline first — imported via Mode B, becomes the diff baseline; (2) verbal call with the counterparty contact happens on top of the reviewed version — points from the call are captured as issues additive to the reviewed legal version. The two inputs are never processed in parallel. Parallel processing (legal version + operator's verbally-captured edits produced simultaneously) creates two competing versions of the same clauses with no clean reconciliation path. Three-way merge of parallel versions is deferred to v2 (DD-39).

---

## 4. Personas & Permissions

| Persona | Role | Can do | Cannot do |
|---|---|---|---|
| **Operator** | Runs the negotiation day-to-day | Full edit: import, edit clauses, raise/resolve issues, snapshot, export, ask Donna | — |
| **Principal** | Owner/exec; decides escalated points | **Read everything** (full contract view, all open issues, agreed items, Donna Q&A); **respond to issues** (approve a position, leave a directive) | **Edit clause content** — no path to it; no inline edit, no ⋮ menu, no insert/delete |

Rationale: the principal's role is to decide positions, not draft language. Full read access gives the context to decide well. No edit path means the operator's working document is never mutated without her knowledge. Any principal action (directive, approval) is captured with `actor: principal` + timestamp and immediately flagged to the operator via a visual indicator on the relevant issue. The operator always knows when the principal has weighed in. (DD-15) **Open item (DD-67):** the principal's response channel previously rode the issue comment thread, now removed — the v1.1 principal portal (F22) must re-home the directive/approval mechanism off comments (an `issues.decision` `actor: principal` write, or a dedicated response field); mechanism to be specified at portal build.

**Deployment model — two phases:**
- **Phase 0 (demo):** run locally on operator's laptop via a single Docker Compose startup script. No IT involvement needed. Principal demos in-person or via screen-share. No external access required.
- **Phase 1 (production):** Azure Switzerland North (Zurich) — keeps data in Switzerland, fits existing Microsoft infrastructure. The engineering Docker setup for local runs directly to Azure with minimal changes. IT request made after principal greenlight.

**Data & AI processing:** contract content is sent to Anthropic's API (Claude) for Donna's analysis. This is acceptable under a Data Processing Agreement (DPA) with Anthropic — operator has an existing Anthropic relationship and DPA is the procurement path. DPA must be in place before any production contract data is processed. Local demo with non-confidential or synthetic data does not require the DPA.

The principal portal is built in **v1.1** — ready even if not yet shown to the principal.

---

## 5. Feature Registry

Priority: P0 (MVP) · P1 · P2. Phase maps to the build plan (§13).

| # | Feature | Priority | Phase | Status | Notes |
|---|---------|----------|-------|--------|-------|
| F01 | Client management (Settings → Clients) | P0 | 0 | **built** | Required before first import. Full CRUD: `/settings` UI list + create + **edit/rename + delete** (delete FK-guarded → 409 if referenced; `status` archive separate) |
| F02 | Deal management (Settings → Deals, or inline on import) | P0 | 0 | **built** | Group contracts under one deal; scope for shared parameters & defined terms. Full CRUD in `/settings` (deals grouped under client, `position` captured; edit/delete with FK-guard) |
| F01b | Contract type management (Settings → Contract Types) | P0 | 0 | **built** | User-configurable taxonomy; pre-seeded (seed.sql); never hardcoded. Full CRUD in `/settings` (create/edit/delete) |
| F01c | Style template management (Settings → Style Templates) | P0 | 1 | planned | Reusable formatting configs; contracts inherit + override; eliminates redundant style setup |
| F01d | Home screen — client + deal + contract browser | P0 | 1 | **built** | `/` = home (recent-contract resume cards, live data, status badge + open-issues + recency, empty state); `/contracts` browser + per-contract **edit/delete** (cascade); persistent top nav (Import·Contracts·Settings, logo→home). Deeper client→deal→contract collapsible sidebar is a later refinement |
| F03 | First import — new contract (.docx → structured node tree) | P0 | 0 | **built** | parse→tree→persist + import/get-tree routes; **live-DB verified** on real contracts (413-node round-trip). **DD-54 content-role classification landed** (backend): boundary/front-matter/operative split, TOC dropped, clause-only numbering — boundary validated at 57/41/17 on JVA/OA/TLA. F04 review UI (incl. role-region rendering) is the remaining piece. See §11 |
| F03b | Counterparty revision import (incoming .docx → change list) | P0 | 2 | planned | Triggered from cockpit; diffs against last snapshot; two parse paths (tracked changes vs. clean diff); see §11 |
| F03c | Counterparty change review — accept/reject/modify + Donna counter-language | P0 | 2 | planned | Inline tracked-change rendering; Donna drafts exact counter; four actions: Accept theirs / Use Donna's counter / Edit Donna's counter / Keep original; DD-26, DD-27 |
| F03d | Negotiation decision logging (learning infrastructure) | P0 | 2 | planned | Every accept/reject/modify decision logged with rich context; feeds Phase 2 RAG and v2 pattern learning; DD-29 |
| F03e | Inline enumerator splitting — `(a)(b)(c)` / `(i)(ii)(iii)` → child nodes | P1 | 1 | **built** | **Greenlit by Lilly 2026-06-25, reverses the prior won't-fix-v1** (§6). At parse time, an inline enumerator run inside one paragraph splits into ordered children of its lead-in clause. **Acceptance (met):** a paragraph `lead-in: (a) X (b) Y` parses to **1 parent + 2 children** — the lead-in text is the parent body, each `(a)`/`(b)` an ordered child, **child order preserved**. **Edge rules (§6):** defined-term definitions (`"Term" means (i)… (ii)…`) are **never split** (permanent carve-out); **flat-only in v1** (nested `(a)…(i)…(b)` not recursed); child body **retains its own `(a)`/`(i)` marker** as native text (Donna doesn't re-derive alpha/roman); **new imports only** — committed trees are not retroactively re-split. **Round-trip de-risk gate HELD (2026-06-25):** reassembly oracle byte-identical on the synthetic always-runs fixture AND the real demo contract; split is content-lossless by construction (split only at whitespace-bounded markers on normalised text → join reproduces source exactly), so a mis-detection can only add an operator-correctable node, never corrupt content. Built in `services/import_/inline_split.py` (`split_inline_enumerators`), wired into the import pipeline after role stamping. Full suite 373 green, mypy/ruff clean. See §6 inline-enumeration note |
| F04 | Import-review UI — first import (verify/correct parse before commit) | P0 | 0 | **built** | **Function-first UI built** (Next.js, two-panel tree+source, confidence flags, triage counter, commit gating, live backend) on real parses. **Role-region rendering landed** (DD-54: front-/back-matter as labeled regions, drafting-notes flagged, non-clause rows unnumbered). **Correction tools landed:** inline level/type, **per-node role edit that re-buckets the node live**, **multi-select bulk shift (level/type/role)**, and **Move ↑/↓ (subtree-aware sibling reorder) with the commit keyed to operator sequence — reparent = move + level tools**; uncertain nodes highlighted; never trust parse blindly. (Remaining: split, merge.) **UX pass landed (2026-06-23, verified live on real OA):** appendix renders with section/body styling (bold heading vs body + indent); expand/collapse on any node with children; click=navigate (scrolls source) / shift-click=range / ⌘-click=toggle selection (checkboxes removed); source panel mirrors structure (bold headings + depth indent); collapsible front/back-matter regions; SOURCE style-guide emphasis (bold ALL-CAPS in pre/back-matter, bold quoted defined terms); caption parent clauses auto-default to numbered sub-headings (bold, keep number); **back-matter categorization** (DD-56 + DD-58: known appendix-title designators — Schedule/Annex/Annexure/Exhibit/Appendix — detected **deterministically** as `appendix_title` dividers; a Haiku whole-region pass categorizes the rest semantically into title/heading/body and can promote unseen designators; `appendix_title` divider role + default title>heading>body leveling; SOURCE gutter shows the category); **content-type (Heading/Body/Table) corrections now round-trip to commit** (verified against the DB). **All correction ops complete: Move ↑/↓ + reparent (commit keyed to operator sequence), split, merge — each with no-data-loss guards.** Only a cosmetic visual-identity pass remains (not a correction gap) |
| F05 | Clause tree browser (collapsible, issue badges, term hover) | P0 | 1 | **built** | Cockpit (`app/contracts/[id]`): read-only clause tree + **collapsible nodes** (twirl, jump-expands-ancestors) + issue-count badges + jump-to-clause-by-number. **Defined-term hover now built** (F16 landed): defined terms get a dotted underline + hover card with the definition (null → "no definition captured"); longest-match, empty-registry-safe |
| F05b | Clause search — keyword + conceptual jump (cockpit jump bar) | P1 | 1 | **built** | Extends the F05 jump bar beyond number-jump: (1) exact keyword **substring jump** with a multi-match counter + ‹/› cycling through hits; (2) **conceptual fallback** — on Enter with no exact literal match, `POST /contracts/{id}/clause-search` returns the best-matching clause. **First live-LLM surface in the project** (LiteLLM wrapper in `services/`, LOW/Haiku tier per DD-35, versioned prompt over clause **headings**). Backend + cockpit UI built + verified (quality 5/5 on real data). Grounded jump only — surfaces an *existing* clause, never generates content (§2.4 trust). **Eng note:** conceptual match is a **per-query LLM call over headings, no embeddings**; embeddings/pgvector (F12/F20) remain the Phase-2 retrieval path and could later back this same surface without changing operator-facing behavior. |
| F06 | Issue creation (select node → operator writes summary → open issue) | P0 | 1 | **built** | Operator writes own title + summary; Donna's analysis loads when the issue is opened, not at creation. **Cockpit UI built** (`app/contracts/[id]`): select clause → single **Description** box + **Us/Counterparty who-raised toggle** → create; captures `initiator=operator\|counterparty` (drives DD-50 source-stance). Issue list with who-raised badges. **Field-routing pending (DD-59):** box must write to `our_position`/`their_position` per the toggle with `title` auto-derived — currently maps box→`title` (engineering to wire) |
| F07 | Issue status tracking | P0 | 1 | **built** | Binary `open` \| `closed` (DD-65); the prior 5-value taxonomy collapsed (non-open → `closed`). Set via a horizontal **segmented Open\|Closed toggle** (one click, replaces the dropdown); closing drops the issue from the active list into a collapsed "Closed (N)" section (not deleted); open-count badge counts `open` only. API + cockpit UI; stamps resolved_at on close |
| F08 | Direct-edit path (inline edit without raising an issue) | P0 | 1 | **built** | Versioned + audited + auto-surfaces in redline (DD-13). Backend: `PATCH /contracts/{id}/nodes/{node_id}` `{text}` → edits `body` (else `heading`), one txn writes node + `node_versions(actor=user)` + audit `node_edited`; no-op skips both; non-prose/derived-only → 422; no renumber (DD-02). **Cockpit edit UI built** — inline textarea via the per-clause ⋮ menu, Save→version (tsc-verified) |
| F08b | New node creation mid-negotiation | P0 | 1 | **built** | Add clause/section on the fly; anchors to a parent; gets derived number; surfaces as tracked insertion in next redline. Backend: `POST /contracts/{id}/nodes` (gap-based order_index + no-gap re-space + `before_node_id` prepend; node_versions insertion row; audit). **Cockpit insert UI built** — ⋮ menu insert above/sub/below, inline new-row editor (tsc-verified) |
| F08d | Donna-assisted clause drafting (new clause from description) | P1 | 1 | planned | Operator describes what's missing → Donna drafts complete clause language + heading → operator reviews/edits → commits; offered as "Draft with Donna" option alongside blank insert in ⋮ menu; Donna grounds draft in deal type, surrounding clause context, and live research where applicable |
| F08c | Free-floating issues (contract-level, no node anchor) | P0 | 1 | **built** | General remarks, structural concerns, points not tied to any existing clause. API (`node_id` nullable) + **cockpit UI built** — raising an issue with no clause selected creates a contract-level issue |
| F08e | Direct-edit — delete clause + sub-tree | P0 | 1 | **built** | Soft-delete a clause and its descendants. Backend: `DELETE /contracts/{id}/nodes/{node_id}` → one txn soft-deletes the subtree + a `node_versions` deletion row per node (actor=user) + audit `node_deleted`; surfaces as a tracked deletion in next redline (DD-13); no renumber (numbers re-derive, DD-02). **Cockpit UI built** — ⋮ menu → Delete with subtree-aware confirm. Backend tests green |
| F08f | Direct-edit — move clause (drag reorder + reparent) | P0 | 1 | **built** | Drag-and-drop **reorder and reparent** (indent/outdent) of a clause, carrying its sub-tree, via a **Rearrange mode** in the cockpit (@dnd-kit, lazy-loaded). Backend: general cycle-safe `POST /contracts/{id}/nodes/{node_id}/move` `{parent_id, after_node_id, before_node_id}` → **order-only** (audit `node_moved`, **no** `node_versions` row); **rejects moving a node into its own sub-tree**; no renumber (numbers re-derive, DD-02); surfaces as a tracked move in next redline (DD-13). **Scope: only the operative clause tree is rearrangeable — front-/back-matter are excluded from drag** (§9). Replaces the earlier up/down-only design. Backend tests green |
| F09 | ~~Issue comment thread~~ | P0 | 1 | **removed (DD-67)** | Comment feature dropped entirely (endpoints, models, `issue_comments` table, UI). Superseded by the **editable issue description** — the operator edits `title` + `our_position`/`their_position` in place (`PATCH /issues/{id}`), so a separate annotation thread is redundant |
| F10 | Donna — single-contract grounded Q&A (Donna tab) | P0 | 2 | **built** | **v1 scope locked (DD-62).** Grounded Q&A over **one contract** (the cockpit Donna tab) — three question shapes: **locate** ("where's the liability cap?"), **explain** ("what does clause 12 say?"), **status-briefing** ("what's still open?", "what did we agree?" — over the issue ledger; the most differentiated-vs-Word capability and v1's headline). **Read-and-explain only:** explains the contract, never advises/drafts/takes a position — "should I accept?" / "is this enforceable?" route to the issue surface (F11) or "get a lawyer" (DD-14 rules 1–2). **Cited to clickable clause nodes** (§7): every answer cites the node(s) it drew from, clickable to jump (reuse F05/F05b). **Retrieval via the F05b conceptual lookup — no embeddings**: finds the clause the operator *means* despite phrasing mismatch. Persistent per-contract thread (`donna_conversations`/`donna_messages`), **windowed context** = last 10 turns + rolling summary (DD-40). **Honest failure:** "I don't see anything in this contract about X" — never fabricates. **LATER (out of v1, named so not silently assumed):** position recommendations / drafting → F11; proactive issue flagging → F28; whole-deal / cross-contract Q&A → later (schema already 1-conversation-per-contract); `node_embeddings` implicit semantic search → F12 (Phase 2, backs this same surface invisibly later). See §9 Donna tab. **Backend BUILT + verified:** `services/donna/` (conversation_repo + windowing DD-40 + grounding [F05b clause retrieval + issue ledger] + `qa.py` structured answer at capable tier) + `prompts/donna_qa_v1.txt` + `POST /donna/ask` + `GET /donna/thread`; 24 tests; **grounding eval 4/4** (cites in-contract · honest not_found · deflects advice · status-briefs the ledger). UI: cockpit **Donna tab** (Issues\|Current Clause\|Donna rail, DD-66; chat with citation chips that jump+flash the cited clause; distinct answer / not_found / deflected treatments; read-and-explain guard line; staged loading + example-question empty state). Follow-up: reloaded thread history renders without chips/kind (thread endpoint returns role+content only). |
| F11 | Donna — issue-scoped recommendation + live research | P0 | 2 | **backend done** | Auto-generated when issue detail is opened; grounded in clause context + DD-31 resolution; live research invoked when issue involves market data (pricing, rates, thresholds); cites sources; proposes specific value/language; DD-38. **v1 scope (approved):** auto-on-open grounded recommendation as ONE object `{cited rationale + draft recommended landing + draft counter-language}`, framed on the DD-14 reasonableness spectrum + ask/settle/floor ladder; propose-vs-counter = same engine, different field; reuses Donna v1 infra + F05b grounding (no embeddings). **OUT of v1:** live research (DD-38), the Brainstorm overlay (slice 2), F28 flagging, F12 embeddings — a needed market figure → recommend the STRUCTURE + flag the missing benchmark, never invent a number. **Draft-vs-confirmed (DD-68):** the auto-recommendation is a DRAFT in `donna_recommendations`, never written to `issues.recommended_position`/`donna_counter_language` (F31 exports) until the operator confirms via [Use Donna's language]. **Backend BUILT + verified:** `services/donna/recommendations.py` (capable/high-tier structured rec, hallucinated-id guard + id scrub) + `recommendation_repo.py` (upsert-draft · get-by-issue · confirm = one-txn draft→issues.* copy + audit) + `prompts/donna_recommendation_v1.txt` + `models/recommendations.py` + `POST/GET /contracts/{cid}/issues/{iid}/recommendation` + `POST …/recommendation/confirm` (registered in main.py); migration `0003_donna_recommendations`; 20 tests; **grounding eval 4/4** (grounded+cited · propose · counter · honest missing-benchmark, no fabricated number — real Opus). **UI pending (STEP 3):** the recommendation card in the issue-detail view. |
| F12 | Tiered context injection | P0 | 2 | planned | Explicit links (DB) + intra-contract semantic search (`node_embeddings`) in Phase 2; negotiation-history search over the issue position/decision ledger + `node_versions` (comments removed, DD-67) in Phase 2+ — DD-06, DD-32 |
| F13 | Negotiation-history RAG (scoped to contested nodes) | P0 | 2 | planned | DD-07 |
| F14 | Contract snapshot ("send to counterparty") | P0 | 3 | **backend done** | Immutable point-in-time capture of full tree topology + node bodies (OQ-08); cut **only on a send** (Clean-copy recipient Counterparty / Legal) — Internal / Copy-only grabs cut none (DD-61); a send advances the matching DD-48 `shared` pointer. DD-09. Backend: `services/snapshot.py` — cut (full-tree JSONB dump + stamps pending `node_versions.snapshot_id` = the F15 diff group) + read + DD-48 pointer upsert; audit `snapshot_cut`; 12 tests. Wired into export by F15b (gate the cut on send-vs-grab there) |
| F15b | Clean copy export — DB→.docx regenerate (no markup) | P0 | 3 | **built** | The renderer (built first); regenerates the current contract from the DB through the style config (DD-43). **Send** (Counterparty / Legal) cuts a snapshot + sets the DD-48 `shared` pointer; **grab** (Internal / Copy-only) downloads only, no snapshot/pointer/lineage effect (DD-61). Round-1 send and the foundation F15/F31 build on. §9/§12, DD-60/DD-61. Backend: renderer (`render_docx.py`, round-trip oracle byte-identical on real 413-node contract) + `services/export/clean_copy.py` send/grab branch + `POST /contracts/{id}/export {recipient}`; UI: cockpit **Export ▾** menu (clean-copy + recipient selector) |
| F15 | Redline export — tracked-changes .docx | P0 | 3 | **built** | Diff(baseline snapshot, working copy) → Word `<w:ins>`/`<w:del>` (DD-51); **baseline defaults to `last shared with counterparty`** (DD-48), operator-overridable; the baseline only advances on a real send, so intervening Internal/Copy-only grabs never move it (DD-61); **renumber shifts suppressed**; authored by operator org, never Donna (DD-44/F25); **disabled until a snapshot exists** (409 on click → "send a clean copy first" hint, §9/§12). DD-60/DD-61. Backend: `services/export/redline.py` (node_versions change-set vs baseline) + `render_redline.py` (`w:ins`/`w:del`) + `POST /contracts/{id}/redline-export {snapshot_id?}`; UI: Redline item in the Export ▾ menu. **Moves + table insert/delete now marked** via a structural diff (baseline snapshot tree vs live tree): a moved node renders struck-at-old + inserted-at-new, a table ins/del is row-marked, edited+moved reconciled, pure-renumber not flagged. **Remaining limits (flagged):** move uses the del+ins fallback, NOT native Word `w:moveFrom/To` (reviewer sees the relocation; Word won't label it "moved" — couldn't validate the move-range OOXML safely here); a deleted table's rows can't be shown (snapshot tree dump omits `table_data`); deleted clause struck near old slot without its old number |
| F16 | Defined-terms registry (deal-scoped) | P1 | 2 | **built** | Extracted on import; hover-to-define. Backend: `services/defined_terms.py` — deterministic regex extraction (`"Term" means …`, canonical `("Term")` intro), deal-scoped upsert `UNIQUE(deal_id,term)`, precision-over-recall; `POST /contracts/{id}/defined-terms/extract` + `GET …/defined-terms`; 18 tests. **Auto-extracts on import-commit** (failure-isolated, doesn't fail the import) + **cockpit term-hover built** (F05). Follow-up: bare `("Term")` intros store no definition (precision call) |
| F17 | Cross-references as structured links | P1 | 0/3 | planned | Detected on import; rendered dynamically (DD-11) |
| F18 | Deal parameters + cross-contract consistency flags | P1 | 4 | planned | Shared values defined once; ripple-flagged (DD-12) |
| F19 | Audit log (append-only) | P1 | 1 | **built** | Every mutation; never updated. Service + read API + tests, AND `record_event` wired into all mutation routes (settings creates, import commit, issue lifecycle); `operator_actor` config actor (DD-53). Now logging |
| F20 | Semantic search + knowledge base | P1 | 2+ | planned | pgvector on clause bodies + the issue position/decision ledger (negotiation-history prose; comments removed, DD-67); cross-client pattern queries ("what terms do we typically accept on IP?"); triggered when prose volume exceeds ~100K tokens |
| F21 | Contract version diff (between snapshots) | P1 | 3 | planned | "What changed in §12 between v2 and v3?" |
| F22 | Principal read-only + issue-decision portal | P1 | v1.1 | planned | Built ready; shown when chosen |
| F23 | Granola / transcript ingest → auto-suggest issues | P2 | v2 | backlog | Live typing is enough for v1. **Held off (operator call, 2026-06-23):** operator types fast enough; live STT adds latency + correction overhead + mis-transcription risk → *net-negative* vs typing for a fast typist (shifts effort, doesn't reduce it). Revisit only if that calculus changes (slower typist / better real-time STT). When built: the issue engine with a transcript input + `initiator=donna` + operator-confirm — not new machinery (collapse) |
| F24 | Style-config UI editor (per-contract override panel) | P2 | v2 | backlog | Import-time style detection + accept/adjust is covered under F04 (Phase 0). Dedicated per-contract override panel deferred to v2. |
| F25 | Operator organization identity (Settings → Your Organization) | P0 | 3 | **built** | Configured org name (config value, not a DB entity); used as redline / export author; never "Donna" (DD-44). Backend: `config/settings.py` `operator_org_name` (`DONNA_OPERATOR_ORG_NAME`) → validator wires `redline_author` so the export author always resolves to the org name (or a neutral default), never "Donna"; `GET /organization`; 8 tests. UI: Settings → **Your Organization** (read-only — set via env per DD-44). §9 line ~461 calls it "read/edit"; in-app editing would need a settings-store follow-up (flagged) |
| F26 | External revision sources — legal team / internal review (rides Mode B engine) | P1 | 2 | planned | `revision_session.source`; Donna moderates legal over-reach (DD-47); + `needs_legal_review` issue flag + legal review packet export |
| F27 | Version pointers + lineage view (where-are-we tracking) | P1 | 3 | planned | 4 named snapshot pointers (DD-48); per-source diff baselines; v1→vN lineage view; recipient-driven export sets pointers. **Status taxonomy (the home-card "where are we" badge, derived not manually set):** `Your move` (received from counterparty/legal, untouched — reactive, floats up) → operator engages it → `In flight` (actively being worked: a fresh draft OR an engaged revision; self-paced) → operator sends → `Sent to counterparty` / `Sent to legal` (waiting on them) → returns → `Your move` … → `Signed`. The home card shows this badge + open-issue count (red when >1) + last activity. Full pointer-states need snapshots (Phase 3) + revision import (Phase 2); the Phase-1 home shows `In flight` + open-issues + recency, richer states fill in by phase |
| F28 | First-pass auto-issue detection on import | P1 | 2+ | planned | On import, Donna drafts a ranked issue list (red flags, below-market terms, missing provisions, placeholders, missing exhibits, broken cross-refs) grounded in the F29 knowledge layer + deal `position`. Rides the issue engine (`initiator: donna`); **operator-confirmed, never authoritative, never auto-exported** (correctness, §2.4 — F1 ~0.62). Source-parameterized ranking (DD-50). Sequenced **after** the bulk-surface mechanism (DD-47) so the list is ranked, not a flood. Keep/dismiss logged via F03d/DD-29 from day one. DD-50 |
| F29 | Knowledge layer — market benchmarks + risk taxonomy (reference data) | P1 | 2 | planned | Curated, static seed data: CUAD risk taxonomy (whole) + market-benchmark table + red-flag taxonomy + per-type checklists (Licence / Offtake / JV built fresh, NDA ported; attach to F01b contract types). Derived from CUAD/public sources — **not** a live legal database. Grounds F28 and turns many F11 live-research calls into local lookups. DD-49 |
| F30 | Negotiation insight distillation | P1 | 2+ | planned | After each brainstorm session closes, Donna runs an extraction pass and synthesizes 0–N compact pattern records — not the raw conversation. Merge-first: checks existing patterns before creating; updates evidence count + refines wording on a match. Consolidation pass triggered after deal close or N new patterns added: redundant patterns merged, low-confidence patterns past TTL (3 deals unreinforced) pruned, contradictions surfaced as flags not silent overwrites. Patterns retrieved selectively alongside tiered context when Donna opens an issue. Converges to ~100–200 records across all subjects; never grows unbounded. DD-55 |
| F31 | Issue-list export (.docx) | P1 | 3 | **built** | Unresolved issues (`status='open'` only, DD-65) → constructive .docx table for principal briefing / counterparty walkthrough / operator record. Columns #/Clause/Issue/Status/Raised by/Our position/Their position/Proposed resolution; **`#` = 1..n render sequence (not raw `priority` — that drives the sort but isn't printed; raw priority is an internal triage number)**; priority-desc (ties → document order), free-floating last; no internal fields / Donna attribution / IDs. Rides the F15b renderer. §9/§12, DD-60. Backend: `services/export/issue_export.py` + `GET /contracts/{id}/issue-list/export`; UI: Issue-list item in the Export ▾ menu |
| — | Call mode / negotiation cockpit | — | — | merged | Folded into edit mode (DD-08) |
| — | Separate appendices entity | — | — | dropped | Appendices are branches of the node tree (DD-05) |
| — | Multi-user / team collaboration | — | — | out of scope v1 | Permissions designed for it (§4); not built |
| — | PDF contracts · e-signature · mobile | — | — | out of scope v1 | |

---

## 6. Data Model

Entities and relationships in plain English. Full SQL lives in `db/schema.sql`.

### Entities

**clients** — one row per counterparty organisation. Fields: id, name, relationship_type (counterparty / partner / licensee / other), status (active / archived), notes, created_at. Managed in Settings → Clients.

**deals** — groups the contracts under one negotiation umbrella. The scope boundary for defined terms and deal parameters — both are shared across all contracts in the deal. Fields: id, client_id, name, description, status (active / signed / closed), **position** (which party the operator is in this deal: `customer` | `vendor` | `buyer` | `seller` | `licensor` | `licensee` | `receiving_party` | `disclosing_party` — set once per deal; governs what Donna's auto-detection flags as unfavorable, DD-50), created_at. A client may have multiple deals (e.g. separate deals in different years). Managed in Settings → Deals or inline when creating a contract.

**contract_types** — user-configurable taxonomy of agreement types. Pre-seeded with common types; operator can add custom. Fields: id, name (e.g. "Licence Agreement", "Offtake Agreement", "JV Agreement"), is_default, created_at. Managed in Settings → Contract Types. Never hardcoded in application logic.

**style_templates** — reusable formatting configs that can be applied to any contract. Fields: id, name, config (JSONB — same schema as per-contract style_config), is_default, created_at. A contract inherits from a template; per-contract overrides applied on top. Managed in Settings → Style Templates. Eliminates redundant style setup when multiple contracts share a house style.

**contracts** — one agreement. Fields: id, client_id, deal_id, contract_type_id (FK to contract_types), name, status (drafting / under negotiation / signed), current version label, style_template_id (nullable FK — inherits template config), **style_config** (JSONB — per-contract overrides on top of template, or standalone config if no template), created_at.

**contract_snapshots** — immutable point-in-time capture of all node states (topology + bodies, per OQ-08), like a git commit. Cut on a **send** (Clean-copy export to Counterparty / Legal — not on Internal / Copy-only grabs, DD-61), and on import to capture an external revision's as-received state (DD-48). Drives redline diffs and the version pointers. Fields: contract_id, label, created_at, origin (`export` | `as_received` | `manual`).

**snapshot_pointers** — the four named version pointers per contract (DD-48). Fields: id, contract_id, party (`counterparty` | `legal_team` | `internal`), direction (`shared` | `received`), snapshot_id (FK), set_at. Unique on (contract_id, party, direction) — each pointer references at most one snapshot and advances as new boundary events occur. `shared` pointers are the per-source diff baselines (DD-47), set **only by the export/send path** (Counterparty / Legal recipient); `received` pointers are immutable records of what the party last sent, set **only by the Phase-2 revision-import path** (captured before edits, frozen) — export never writes a `received` pointer (DD-48/DD-61). The live working copy is the current node tree, not represented here. (v1 note: the `internal` party value takes no pointer — Internal export is a pure download, DD-61; left dormant for a future multi-user internal-review feature.)

**nodes** — the universal addressable unit. Self-referential adjacency list for arbitrary depth (Article → Section → Clause → Sub-clause; appendices are top-level branches that nest the same way). Fields:
- id (primary key), contract_id, parent_id
- **order_index** (integer — position among siblings; gap-based allocation per OQ-07 resolved; unique within parent_id + contract_id)
- content_type: `prose` | `table` | `attachment`
- **role**: the node's structural role (DD-54, DD-56), default `clause`. **Front-matter:** `title` | `date` | `parties` | `recital` | `agreement_statement`. **Body:** `clause` (the only numbered region). **Back-matter:** `appendix_title` (a schedule/annex/exhibit divider, level 0 — DD-56) | `appendix` (DD-05, the schedule heading/body content) | `signature_block`. **Cross-cutting:** `drafting_note` (internal counsel/author commentary — kept but **excluded from every counterparty export**, §12). Front-matter + back-matter + `drafting_note` are excluded from the clause tree and numbering; clause numbering re-derives from the first `clause`. Back-matter is categorized by an AI whole-region pass (DD-56), semantic not keyword-based, into title/heading/body; operator-correctable, persists. The **table of contents** is detected and dropped on import (regenerated on export, §10) — never stored, not a role.
- **has_placeholder** (boolean) — node contains a fill-in blank (`[insert …]`, `___`, `[amount]`); drives a pre-signing "incomplete field" alert (ties F28). An inline marker, not a role.
- heading, body (prose); **table_data** (JSONB | null — table nodes only; `[[cell, …], …]` rows, never flattened to a string)
- **plain_text** (derived projection — regenerated from body on save; used for AI context, embeddings, search, diff display; never the source of truth)
- **file_reference** (nullable — for attachment nodes: path or storage key to the binary file)
- **is_deleted** (boolean, default false), **deleted_at** (nullable timestamp) — soft delete; deleted nodes excluded from live tree but included in snapshot diffs for tracked-deletion export
- created_at, updated_at
- **No stored clause number.** The number is *derived* from tree position + the contract's numbering scheme (DD-02, DD-11).

**node content representations**
- `prose` → **semantic markup**: plain text plus a fixed inline marker vocabulary (see below). Human-editable, diffable.
- `table` → **structured rows/cells**, never flattened to a string.
- `attachment` → file reference + metadata (binary exhibits: PDF/Excel).
- Every node also carries a **derived `plain_text` projection** — regenerated from the body, disposable — used for AI context, embeddings, search, and diff display.

**Semantic markup vocabulary (locked — OQ-01 resolved)**

Operators always type plain text. Donna resolves markers automatically on save. Raw markers are never the user-facing format — the UI always renders the interpreted output.

| Element | Stored marker | Auto-resolved from |
|---|---|---|
| Cross-ref, same contract | `[[Section 4.2]]` | "Section 4.2", "Clause 4.2", "Schedule 2" |
| Cross-ref, other contract clause | `[[TLA Section 4.2]]` | "TLA Section 4.2", "as defined in TLA Section 4.2" |
| Cross-ref, contract name only | `[[TLA]]` | "as defined in the TLA", "the TLA" |
| Defined term (reference) | `{Offtake Price}` | Any known defined term (case-insensitive match against deal registry) |
| Defined term (first definition) | `("Offtake Price")` | Canonical introduction syntax; Donna flags `"Term" means...` as inconsistent |
| Figure / percentage | `[15%]` `[USD 500,000]` | Numeric patterns with currency/percentage |
| Manual emphasis override | `~~bold~~` | Typed explicitly by operator; rare |
| Footnote anchor | `[^1]` | Inserted by Donna on import; body stored as structured note on the node |

**Resolution rules:**
- On save, Donna scans edited prose and resolves all detectable patterns above.
- Unknown capitalized terms not in the defined terms registry → flagged for operator: "Is 'Production Threshold' a defined term? Add to registry or ignore."
- Defined terms introduced with `"Term" means...` syntax instead of `("Term")` → flagged as inconsistent.
- Ambiguous cases are always flagged, never silently assumed.

**Inline enumeration note (from contract analysis):**
Inline `(a)(b)(c)…` / `(i)(ii)(iii)…` enumerators within a single prose paragraph are **split into ordered child nodes** of their lead-in clause (rule below). They appear primarily in multi-part definition clauses — those are the **defined-term carve-out** and are NOT split (they stay verbatim in the definition body). Block enumeration (each item already its own node) remains the dominant structural pattern; this rule extends the same tree shape to inline runs.

**Inline enumerators ARE split into child nodes (v1 — greenlit by Lilly 2026-06-25, reversing the prior defer).** Until 2026-06-25 these inline runs stayed as body text of their containing node (won't-fix-v1: per-enumerator addressability failed the effort-saved test and the split risked the §2.1 round-trip). Lilly has now directed the reverse: whenever the `(a)(b)(c)…` / `(i)(ii)(iii)…` pattern occurs **mid-paragraph as continuous text**, the enumerators are parsed as ordered children of the lead-in clause.

- **Shape.** Text before the first marker (e.g. "The following shall apply:") stays as the **parent node's body**; each `(a)`, `(b)`, … becomes an **ordered child** (document order preserved via `order_index`). Example: `The following shall apply: (a) both parties shall …; (b) no party shall …` → 1 parent ("The following shall apply:") + 2 children. This is in addition to Word auto-numbered sub-clauses, which already nest as their own nodes.
- **Defined-terms carve-out (permanent).** A multi-part definition — `"Affiliate" means (i) … (ii) …` (canonical `("Term")` or the `"Term" means …` form) — is **never split**. The `(i)/(ii)` are part of the definition, not sub-clauses; the definition is the cognitive unit the operator negotiates and the unit F16 registers. The split rule fires only on a non-definition lead-in.
- **Nesting — flat-only in v1.** Only one level is split: the markers directly under the lead-in. Nested runs (`(a) … (i) … (ii) … (b)`) are **not** recursed in v1 — the inner `(i)/(ii)` stay inside child `(a)`'s body. Avoids the round-trip and ordering complexity of mixed-depth inline trees for the rare nested case; revisit on real demand.
- **Child role + text.** Each child is an ordinary clause node. Its body **retains its own `(a)` / `(i)` marker as native text** — the marker is NOT stripped and re-derived, because Donna does not reliably regenerate alpha/roman enumerators on export (unlike decimal clause numbers). Keeping the literal marker in the child body is what protects the §2.1 byte-identical round-trip. *(Eng note: store the marker as native child text; do not treat it as a derived number.)*
- **Scope — new imports only.** The split happens at parse time on **new first imports**; already-parsed contracts in the DB are **not** retroactively re-split (no migration, no re-parse of committed trees).
- **Gate (unchanged, still binding).** The build is gated on an engineering round-trip de-risk spike on the reassembly oracle — one paragraph → N nodes → reassemble → assert **byte-identical to source**, with the defined-term carve-out as a no-split case — run *before* the parser change ships (engineering, DEV_TODO). A round-trip corruption here is a §2.4 trust failure, not just a parse miss.

**footnotes** — structured footnote bodies anchored to a node. Created by Donna on import when `[^N]` anchors are detected. Fields: id, node_id, anchor_index (integer — matches the `[^N]` marker in the node body), body (semantic markup), created_at.

**node_versions** — append-only history of every change to a node body. Fields: id, node_id, snapshot_id (nullable — null means the edit has not yet been assigned to a snapshot; assigned when the next snapshot is cut), body_before, body_after, actor, created_at.

**defined_terms** — capitalized defined terms, deal-scoped (shared across all contracts in the deal). Fields: deal_id, term, definition, source_node_id.

**cross_references** — explicit links between nodes (and across contracts in the deal). Fields: id, source_node_id, source_contract_id, target_node_id, target_contract_id. The displayed number is rendered from the target's *current* position, so references never break on renumber and ripple-flag on change. (DD-11)

**deal_parameters** — shared commercial values that must stay consistent across contracts (price, margin, royalty %, capacity, cross-default linkages). Defined **once** at the deal. Fields: id, deal_id, key, value, unit, notes.

**parameter_references** — links each literal mention of a deal parameter in a node to its deal_parameter record. Fields: id, node_id, deal_parameter_id, mention_text (the literal text in the clause), created_at. Changing the parameter flags every referencing node for review; conflicting literal values at import are flagged immediately. (DD-12)

**counterparty_revision_sessions** — represents one imported externally-revised .docx. Created when operator imports a revision from within the cockpit. Fields: id, contract_id, baseline_snapshot_id (the snapshot sent out — the diff baseline), source_filename, parse_path (`tracked_changes` | `clean_diff`), status (`reviewing` | `completed`), changes_count, changes_reviewed_count, imported_at. **Generalizes to `revision_sessions` (DD-47):** add `source` (`counterparty` | `legal_team` | `internal`) — the diff/review engine is identical across sources; Donna's per-change stance is parameterized by source. Full rename propagated at Phase 2 build.

**counterparty_revision_changes** — one row per **node** (chunk) that has at least one counterparty edit. The navigation unit in the left panel during review ("3.1.4 — 4 edits"). A node is the natural chunk boundary: numbered clauses (3., 3.1, 3.1(a)), lettered sub-clauses, free-floating paragraphs, definitions, and tables are all nodes and therefore all chunks. Fields: id, session_id, node_id (**nullable** — null means a proposed new node the counterparty added that does not exist in the baseline; accepting creates and inserts it, rejecting discards it), proposed_parent_id (for new nodes: where in the tree to insert), proposed_order_index (for new nodes: sibling position), hunk_count, hunks_decided, status (`pending` | `partial` | `complete`).

**counterparty_revision_hunks** — one row per individual text edit within an existing node. The decision unit — operator decides on each hunk independently within the chunk view. Fields: id, change_id, hunk_type (`insertion` | `deletion` | `replacement`), significance (`trivial` | `substantive`) — classified by Donna based on **semantic impact on the clause, not edit size**. A single word change can be substantive if it alters meaning, obligation, scope, or commercial effect (e.g. "shall" → "may", adding "not", "reasonable" → "sole", "and" → "or", a changed figure). Trivial = demonstrably no change to meaning (e.g. British/American spelling, punctuation, stylistic "shall" → "will" with no obligation shift). **Two rules that never bend:** (1) Donna must resolve all explicit references (DD-31) before classifying any hunk — a change that looks like punctuation in isolation may shift meaning once the referenced term is known; classification on raw hunk text alone is not permitted. (2) Trivial is a high-confidence positive classification, not a residual — if Donna cannot confidently confirm zero semantic impact, she classifies the hunk as substantive. When in doubt, always substantive. The cost of a false-substantive is one unnecessary operator review; the cost of a false-trivial is a silently corrupted clause. Donna reads the full clause in context before classifying — never classifies by word count alone. Trivial hunks: pre-recommended Accept, no counter drafted. Substantive hunks: full analysis + exact counter-language, position_in_body (character offset for inline rendering), original_text, proposed_text, donna_verdict (`accept` | `counter` | `keep`), donna_counter_text (Donna's exact proposed language for this hunk — null for trivial hunks), verdict (`pending` | `accepted` | `rejected` | `modified`), final_text (text actually applied or countered), decided_at.

**issues** — an open negotiation point, anchored to a node or free-floating at contract level. Fields:
- id, node_id (nullable — null = free-floating; mutable, can be set post-creation to anchor a free-floating issue), contract_id, title
- our_position, their_position, options_on_table
- **recommended_position** (Donna's proposed landing; may be a fallback ladder: ask / settle / floor)
- **donna_counter_language** (exact counter-language drafted by Donna for counterparty-proposed changes)
- status: `open` | `closed` (DD-65 — binary; the prior `agreed`/`deferred`/`kicked`/`dismissed` collapse to `closed`, set via a segmented Open|Closed toggle. A dismissed Donna auto-flag closes the issue and the dismissal is logged for learning, DD-50)
- initiator: `operator` | `counterparty` | `donna` (`donna` = surfaced by first-pass auto-detection on import, DD-50)
- **auto_flag** (JSONB | null — populated only when `initiator = donna`. `{flag_type: red_flag | below_market | missing_provision | internal_inconsistency | placeholder | missing_exhibit, benchmark_ref (FK into the knowledge layer, DD-49, null for non-benchmark flags), confidence, source_stance: counterparty | legal_team | first_import}`. `source_stance` drives ranking — legal_team surfaces over-reach first, counterparty surfaces unfavorable-to-us first, DD-50. Keep/dismiss outcome logs through the F03d/DD-29 decision path from day one.)
- **authority**: `within-operator-authority` | `needs-principal`
- **needs_legal_review** (boolean — clause requires legal/enforceability input; distinct from `authority`, which routes commercial decisions to the principal; an issue may need both. Drives the legal review packet export. DD-47)
- **category**: `commercial` | `legal` | `operational` | `counterparty_proposed_edit`
- **counterparty_revision_session_id** (nullable FK — links issues created from a counterparty revision import)
- **opened_in_snapshot_id** (nullable — which round this issue was first raised)
- **resolved_in_snapshot_id** (nullable — which round this issue was closed)
- **decision** (JSONB — populated on resolution):
  - `verdict`: `accept_theirs` | `use_donna_counter` | `custom_counter` | `keep_original`
  - `final_language`: exact text applied to the node (null if keep_original)
  - `donna_recommendation`: Donna's recommendation at decision time (archived for learning)
  - `reasoning`: operator's optional note on why
  - `actor`: `user` | `principal`
  - `decided_at`: timestamp
- **donna_research_citations** (JSONB | null — populated by Donna when live research was invoked; array of `{source_url, excerpt, retrieved_at}`; null if issue did not require market research)
- **impact** (free-text $ / risk, for briefing sort order)
- **priority** (integer — sort order in open issues list)
- created_at, resolved_at

**issue_comments** — **removed (DD-67).** The comment thread is dropped; the editable issue description (`title` + `our_position`/`their_position` via `PATCH /issues/{id}`) covers the need. Table dropped via forward migration; the DD-63 contract-delete cascade no longer cascades comments.

**node_embeddings** — pgvector embedding per node (of the plain_text projection). Built in **Phase 2** alongside Donna's intelligence — required for intra-contract implicit semantic search (DD-32). Fields: node_id, embedding, embedded_at (timestamp — used to detect staleness: if `nodes.updated_at > node_embeddings.embedded_at`, embedding is stale and re-queued).

**Embedding trigger rules (always asynchronous — never blocks a save):**

**Core principle: only embed what changed. Never re-embed unchanged nodes. Never embed before structure is finalised.**

Embeddings fire after the operator has confirmed the correct tree structure. Never during a correction or review phase — embedding malstructured content is wasted work and pollutes search results.

| Event | Trigger point | What gets embedded |
|---|---|---|
| First import (Mode A) | After operator clicks **Commit** in the import review UI — structure corrections are complete | All nodes in the contract (batch) |
| Counterparty revision (Mode B) | After **structural triage is committed** (not before, not during). Then: as each content decision is applied (accepted hunk, new node inserted) | Only nodes whose `body` actually changed |
| Inline edit in cockpit | On save — tree is already stable | That node only |
| New node inserted in cockpit | On insert — tree is already stable | That new node only |
| Structural-only change (reorder, reparent, renumber) | **Never** — body unchanged; position read from tree at query time | Nothing |
| Defined term or deal parameter updated | **Never** — node's own `plain_text` unchanged; referenced content resolved fresh at query time | Nothing |

**Stale detection**: worker checks `nodes.updated_at > node_embeddings.embedded_at`; skips any node where `embedded_at >= updated_at`. This is the enforcer — the engineering worker never needs to decide manually whether to skip.

**comment_embeddings** — **removed (DD-67)**, with the comment thread it indexed. The negotiation-history search it powered (cross-deal pattern queries — "what was our reasoning on IP across all deals?") re-bases onto the issue position/decision ledger (`issues.our_position`/`their_position`/`decision`, DD-29) + `node_versions` prose; the Phase 2+ embedding substrate over that prose is specified when built (same volume gate: ~100K tokens of negotiation prose).

**negotiation_patterns** — compact extracted insights distilled from brainstorm sessions and accumulated decision history. Not conversation transcripts — synthesized principles that compound over time. Fields: id, pattern_type (`operator_style` | `counterparty_behavior` | `deal_type_norm` | `legal_team_tendency`), subject_type (`operator` | `client` | `deal_type` | `contract_type`), subject_id (nullable — FK to `clients` or `contract_types`; null for operator-level patterns), insight (text — 1–3 sentence compact principle, not a transcript), confidence (`low` | `medium` | `high`), evidence_count (integer — reinforcement events that support this pattern), last_reinforced_at (timestamp — used for TTL pruning: low-confidence patterns unreinforced across 3 deals are pruned), source_issue_ids (JSONB — issue IDs that contributed), created_at, updated_at.

**donna_conversations** — one per contract; holds the persistent contract-level Donna Q&A thread state. Fields: id, contract_id, running_summary (text — rolling summary of turns older than the live window; updated incrementally by Donna, never the full source of truth), updated_at. (DD-40)

**donna_messages** — append-only turns in a contract's Donna conversation. Fields: id, conversation_id, role (`user` | `assistant`), content, created_at. The full thread is always persisted; only the last 10 turns + `running_summary` are injected into Donna's context per call (DD-40).

**audit_log** — append-only event log; never updated. Every mutation to node content or issue state, including direct edits and principal decisions. Fields: event_type, entity_type, entity_id, actor, payload (JSONB), created_at.

### Key Relationships

```
clients ──< deals ──< contracts ──< nodes ──< issues
                            │            └──< node_versions
                            │            └──< footnotes
                            └──< contract_snapshots
                            └──< counterparty_revision_sessions
                                    └──< counterparty_revision_changes >── nodes
                                              └──< counterparty_revision_hunks
contract_types    >── contracts
style_templates   >── contracts   (nullable; per-contract style_config overrides on top)
deals ──< defined_terms
deals ──< deal_parameters ──< parameter_references >── nodes
nodes  ──< node_embeddings
nodes  >── cross_references ──< nodes        (may cross contracts within a deal)
contracts ──< donna_conversations ──< donna_messages
negotiation_patterns  (operator-level: subject_id null; client-level: subject_id → clients;
                       deal-type/contract-type: subject_id → contract_types)
```

---

## 7. Donna — AI Design

Donna is three **surfaces** over one retrieval spine, evolving across phases.

- **Contract-scoped assistant** (F10) — grounded **read-and-explain** Q&A over one contract (the Donna tab). Locate / explain / status-briefing: "Where's the liability cap?" "What does clause 12 say?" "What's still open?" "What did we agree?" Cited to clickable nodes; explains, never advises (advice → F11). v1 is **single-contract**; whole-deal / cross-contract Q&A is deferred (DD-62).
- **Issue-scoped assistant** (F11) — works *inside* an open issue with that node's text + position ledger + its negotiation history. Brainstorms options, drafts replacement language, weighs trade-offs.
- **Counterparty revision reviewer** (F03b/F03c) — for every counterparty-proposed change: produces a verdict (accept/counter/keep), one-line reasoning, and exact counter-language ready to use. Operator judges; Donna drafts. Gets smarter over time as decisions accumulate (DD-29).
- **Knowledge base** (v2+) — cross-client, cross-deal pattern queries. "What terms have we typically accepted on IP protection?" "How did Client B's position on exclusivity compare to Client A's?" Powered by semantic search over accumulated negotiation history.
- **Insight distillation** (F30) — after each brainstorm session closes, Donna runs an extraction pass over the conversation and synthesizes 0–N compact pattern records into `negotiation_patterns`. The raw conversation is still discarded (DD-42/DD-55); only extracted principles persist. Patterns cover four types: operator negotiating style, counterparty behavioral tendencies, deal-type norms, legal-team tendencies. Merge-first: Donna checks existing patterns before creating — a matching pattern gets its evidence count incremented and wording refined; only genuinely novel insights create new records. Consolidation (triggered on deal close or after 5 new patterns): redundant patterns merged, low-confidence patterns unreinforced across 3 deals pruned, contradictions surfaced as flags not silent overwrites. The store converges to ~100–200 compact records — never grows unbounded.

### Negotiation style learning (DD-29)

Every accept/reject/modify decision is logged with full context in `issues.decision`. Donna uses this history in two ways that mature across phases:

| Phase | Mechanism | Example |
|---|---|---|
| Phase 2 | RAG over past decisions — retrieves similar cases as context | "In 3 similar situations, you rejected open-ended pricing. Recommend rejecting." |
| v2 | Pattern inference — Donna identifies principles from accumulated decisions and applies them proactively | "Operator consistently rejects open-ended commercial obligations. Applying as default." |

The infrastructure is built in Phase 2 (rich decision logging). The intelligence matures in v2. Starting to log richly from day one is non-negotiable — the v2 learning model has nothing to work from if decisions are logged poorly.

### Retrieval spine — Tiered context injection (DD-06)

**The rule: structured data → DB query. Free-text prose → semantic search (when volume demands it).**

Context is layered, not flat. Each tier is populated by DB query in Phase 2, with semantic search added in Phase 2+ as prose volume grows:

1. **Node text** — the specific clause in question. DB query by node_id.
2. **Explicit referenced context** — all defined terms (`{...}`), cross-references (`[[...]]`), and deal parameter links (`[figure]`) detected in the node body are resolved and injected. One level deep. Deterministic — always run. (DD-31)
3. **Implicit semantic context** — Donna queries `node_embeddings` for clauses semantically related to the clause under analysis that have no explicit cross-reference. Answers "what else in this contract is relevant here?" Activated when: hunk significance is `substantive`, or explicit links don't provide sufficient context for a reliable recommendation. Intra-contract semantic search — Donna discovering implicit relationships, not just following known links. (DD-31, DD-32)
4. **Issue position ledger** — our/their position, options, status for this issue. DB query by issue_id.
5. **Contract-level summary** — agreed points + open issues across the contract. DB query, aggregated.
6. **Deal-level summary** — commercial terms agreed across all contracts in the deal. DB query on deal_parameters + resolved issues.
7. **Full negotiation history** — only on explicit request. Re-based off the removed comment thread (DD-67) onto the **issue position/decision ledger** (`issues.our_position`/`their_position`/`decision`, DD-29) + `node_versions`. Phase 2: the issue's ledger + version history injected directly. Phase 2+: semantic search over that negotiation-history prose surfaces the most relevant moments.
8. **Negotiation pattern layer** (Phase 2+, F30) — always-on alongside tiers 1–7; retrieved selectively from `negotiation_patterns`. Always injected: operator-style patterns (how the operator negotiates, what they consistently accept/reject). Injected when same client: counterparty behavioral patterns for that client (what they push on, where they concede). Injected when same contract type: deal-type norms (typical market positions for this agreement type). Injected when legal team is the revision source: legal-team tendency patterns. Compact records — does not materially increase context size.

Negotiation history is retrieved **only for contested nodes** (DD-07). A clean, never-contested node pulls no history.

### Retrieval evolution by phase

| Phase | Tiers active | What Donna can answer | How |
|---|---|---|---|
| Phase 2 | Tiers 1–6 | "What's our position on X?" "What's agreed?" "What's the royalty %?" "What does clause Y mean for clause X?" | Structured DB queries (tiers 1, 4, 5, 6) + explicit reference resolution (tier 2) + intra-contract semantic search on `node_embeddings` (tier 3) |
| Phase 2+ | Tiers 1–8 | "What did the counterparty say about exclusivity?" "What was our reasoning on IP?" "What does this counterparty typically do on IP?" | Adds semantic search over the negotiation-history prose — the issue position/decision ledger + `node_versions` (tier 7, DD-67) + negotiation pattern layer (tier 8) |
| v2 knowledge base | Tiers 1–8 + cross-client | "What terms do we typically accept on recall?" "How did the counterparty compare on X across deals?" | Cross-client semantic search over all accumulated history |

**pgvector build timing — two distinct triggers:**
- **`node_embeddings` → Phase 2** (same phase as Donna's intelligence): needed for intra-contract implicit semantic search. Donna cannot discover implicit clause relationships without embeddings, regardless of contract volume. A single contract is sufficient to need this.
- **Negotiation-history embeddings → Phase 2+**: needed for negotiation-history search across many rounds, over the issue position/decision ledger + `node_versions` prose (re-based off the removed comments, DD-67; substrate specified when built). Volume-gated: when that prose exceeds ~100K tokens (~3–4 active deals with multiple rounds).

### Model quality principle (DD-35)

Donna's AI surfaces are tiered by consequence. The model assigned to each task must match the stakes of a wrong answer.

| Tier | Tasks | Minimum model |
|---|---|---|
| **High consequence** — operator acts on output directly, legal/commercial impact if wrong | Hunk significance classification, counter-language drafting, negotiation position brainstorm, Mode A triage (baseline vs open proposal) | Opus |
| **Medium consequence** — operator reviews output before acting | Deal-scoped Q&A, contract/deal-level summaries, cross-reference and defined-term resolution for context injection | Sonnet |
| **Low consequence** — structured extraction/detection, output is verifiable or internally consumed | Defined term and cross-reference extraction at import, figure/parameter detection, semantic markup slot-filling on save, node similarity scoring (Mode B Path B diff), issue title generation, structural anomaly detection | Haiku |

**Haiku as pre-screen router for hunk significance:** before routing a hunk to Opus for full significance analysis, Haiku runs a first pass. If Haiku classifies the hunk as high-confidence trivial (spelling variant, punctuation only, capitalisation only) → skip Opus, auto-recommend Accept. If uncertain → always escalate to Opus. Uncertain never defaults to trivial. This router is expected to reduce Opus calls by 30–50% on heavily-edited imports.

Model assignments live in `config/` — never hardcoded in application code. Swapping a model means changing one config value. DD-35 covers the product quality principle; the routing implementation is the engineering ADR.

### Behavioral contract (DD-14)

Donna's four non-negotiable rules — her character:

1. **Grounded & cited.** Answers only from retrieved node text + ledger + history, and cites the nodes/issues used. Not free-floating chat.
2. **Honest about limits.** When an answer needs legal judgment beyond the document (enforceability / governing law, regulatory/tax/competition compliance, final sign-off on liability, indemnity, IP, termination remedies), Donna **says "get a lawyer"** and does not bluff. At that boundary she drafts the *precise question to send the lawyer*, with relevant clauses attached, to minimize billed hours.
3. **Elicits before recommending.** Before recommending, Donna identifies what she's missing and either retrieves it, **asks the operator** (strategic intent, principal's stance, a value not yet in the system), or flags "lawyer." She recommends only once grounded. Answers given back to her are written into the issue context (capture loop).
4. **Advocates, but closes.** Always defends the operator's side, but optimizes for a *signable* deal. Frames positions on a reasonableness spectrum (favorable-but-fair → aggressive → deal-breaking), flags when a position risks counterparty walkaway, and offers a fallback ladder (ask / settle / floor). This applies to **every** source of proposed language, including the operator's own legal team: when an allied legal redline over-reaches into aggressive/deal-breaking territory likely to trigger counterparty pushback, Donna flags it and offers a more balanced alternative that still protects the operator (DD-47). Her loyalty is to closing the deal while protecting the operator — not to maximal protection, which is the legal team's job, not hers.

---

## 8. Architecture & Design Decisions

All design decision records (DD-01 … DD-68) live in **[`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md)** — see its index for the full list. Inline `DD-NN` references throughout this spec resolve there.

---

## 9. UI Design

Everything below is locked design, not proposal.

### Navigation model

- **Routes:** `/` Home · `/import` Import flow (Mode A) · `/contracts` Contracts ("My Contracts") · `/contracts/{id}` Negotiation cockpit · `/settings` Settings.
- **Persistent top bar on every screen:** donna.ai logo top-left → Home (`/`); top-right links **Import · Contracts · Settings** (the three site-wide destinations). **Export is NOT in global nav** — it's a per-contract action inside the cockpit ("Send to counterparty"); you export the contract you're already in, so a global "Export" would be the wrong altitude.
- Logo → Home from anywhere.

### Home screen

The default landing (`/`). Answers one question: **"what do I pick up?"** — not a stats dashboard, a launcher into the right contract.

- Shows the operator's **most-recently-touched contracts as resume cards**; click a card → that contract's cockpit.
- **"Your move" cards float to the top, accented** (amber) — the page surfaces what needs the operator first.
- **Each card:** a **status badge** (the where-are-we taxonomy, F27/DD-48: `Your move` / `In flight` / `Sent to counterparty` / `Sent to legal` / `Signed` — color-coded: amber = your move, green = signed, neutral = in flight, muted = waiting) · contract name + client·deal · **open-issue count (red when > 1, else grey)** · last activity.
- **Ordering / recency:** most-recently-touched first; recency derivable from the audit log (latest event per contract) or `MAX(updated_at)` across its rows.
- **Empty / first-run:** a prominent "Import your first contract" CTA.
- **Phasing:** the Phase-1 home shows `In flight` + open-issue count + recency; the richer `Sent` / `Your move` states light up as snapshots (Phase 3) + revision import (Phase 2) land (F27). More cards (e.g. Donna Q&A) are added as Phase-2+ surfaces ship.

### Contracts screen ("My Contracts") — client → deal → contract browser

Reached via the top-nav "Contracts" link. Two-level navigation in a persistent left sidebar:

```
┌──────────────────────────────────────────────────────┐
│  donna.ai          [ + New Client ]  [ Settings ⚙ ]  │
├────────────────┬─────────────────────────────────────┤
│  CLIENTS       │  COMPANY A — Active deals           │
│                │                                     │
│  Company A  ▼  │  ┌─────────────────────────────┐   │
│    Deal 1   ▼  │  │ Deal 1 — Technology Licence │   │
│      Licence   │  │ 3 contracts · 12 open issues│   │
│      Offtake   │  │ Last activity: 2 days ago   │   │
│      JVA       │  └─────────────────────────────┘   │
│    Deal 2      │                                     │
│  Company B     │  ┌─────────────────────────────┐   │
│  + Add client  │  │ Deal 2 — Distribution       │   │
│                │  │ 1 contract · 0 open issues  │   │
│                │  └─────────────────────────────┘   │
└────────────────┴─────────────────────────────────────┘
```

- Left sidebar: all clients, collapsible to deals, collapsible to individual contracts. Persistent across sessions.
- Main area: when a deal is selected, shows all contracts in that deal with a summary card (contract count, open issue count, last activity).
- **"Last activity"** — derived at query time as `MAX(nodes.updated_at, issues.updated_at)` across all contracts in the deal. No stored field; computed in the deals list endpoint.
- Click a contract → opens the negotiation cockpit.
- "Import contract" button on each deal card → launches the import flow, pre-filled with that client + deal.
- **No "imported/committed" filter on the picker (decided).** The picker lists *all* contracts; one with no committed node tree opens to a clear "No clauses yet — import first" empty state rather than being hidden. For the target user (2–5 agreements) a hidden-by-flag picker adds a concept and a "where did my contract go?" failure mode for no benefit; the empty state is self-explanatory and doubles as the next-action prompt.

### Settings

Reached via the **Settings** link in the persistent top-right nav (see Navigation model). Sub-sections:

**Settings → Clients**
Table of all clients. Add / edit / archive. Fields: name, relationship type, notes. Archiving hides the client from the home screen but preserves all data.

**Settings → Contract Types**
User-configurable list. Pre-seeded defaults (Licence Agreement, Offtake Agreement, JV Agreement, NDA, Amendment). Add custom types. Reorder. Cannot delete a type that's in use — archive instead.

**Settings → Style Templates**
Named formatting configs reusable across contracts. Fields: template name, full style config (font, numbering scheme per depth, heading styles, indentation, page breaks). Each template has a live preview pane showing a sample clause rendered with the current settings. A contract inherits from a template; per-contract overrides are applied on top. "Set as default" applies this template automatically to all new contracts unless overridden at import.

**Settings → Deals**
Manage deals per client. A deal can also be created inline during the import flow.

**Settings → Your Organization**
The operator's own organization identity (single value, single-operator v1). Used as the author on every generated redline / tracked change and as author metadata on regenerated .docx — never "Donna" (DD-44, F25). Stored in `config/`; surfaced here as a read/edit field.

### Import flow — four steps

**Step 1 — Context (new)**
Select client (dropdown — existing clients or "Create new client"). Select deal (dropdown scoped to that client — existing deals or "Create new deal"). Enter contract name. Select contract type. Upload .docx. This step is where donna.ai knows where to store the contract before parsing begins.

**Deal `position` is required when creating a deal inline (decided).** When the operator picks "Create new deal," the inline form must capture `deals.position` (customer / vendor / buyer / seller / licensor / licensee / receiving_party / disclosing_party — DD-50); selecting an *existing* deal does not re-ask (it already carries one). Rationale: position is a once-per-deal decision that governs what Donna flags as unfavorable (F28 source-stance ranking). It **cannot be reliably inferred from contract type** — a "Licence Agreement" could be licensor or licensee, an "Offtake" buyer or seller — so defaulting from type would plant a *confidently wrong* value, which is worse than null. A deal that lands `position=null` silently disables F28 ranking (logging-precedes-learning: capture the parameter the learning feature depends on at the moment the deal is born, not retroactively). Cost is one dropdown on the create-new-deal path only.

**Step 2 — AI parsing (background)**
Progress indicator: "Detecting structure… identifying clauses… resolving cross-references… flagging uncertain nodes." 10–30 seconds. Cannot be skipped.

**Step 3 — Review UI**
Two-panel layout: candidate tree on the left, original source text on the right (read-only, for reference while correcting).

The candidate tree shows every detected node with a confidence indicator: ✓ (confident) or ⚠ (uncertain level or type). Operator only needs to touch ⚠ nodes. On a 900-paragraph contract where AI gets 85% right, that's ~135 corrections, not 900.

Actions on nodes: ± level (keyboard arrows for speed), multi-select → bulk level shift, split, merge, delete, type badge toggle (HEADING / BODY / TABLE / APPENDIX).

Style detection also runs here — donna.ai proposes a style config derived from the source document. Operator can accept, adjust, or select an existing style template. Preview pane shows a sample clause rendered with the proposed config.

**Step 4 — Commit**
Summary: "Import N clauses, M tables, P appendices under [Client] → [Deal] → [Contract name]." One confirm button. Spinner. Done. Cockpit opens for the new contract.

### Export flow

Export is a **per-contract cockpit action** (top-right `Export ▾`), never in global nav (see Navigation model): you export the contract you're already in. **Three exports in v1**, all Phase 3, all rendered through the per-contract style config (no formatting decisions at export time):

```
[ Export ▾ ]
  ├── Clean copy (.docx)   → recipient: [ Counterparty ▾ | Legal | Internal | Copy only ]   (F15b)
  ├── Redline (.docx)  from: [ last shared with counterparty ▾ ]                              (F15)
  └── Issue list (.docx)                                                                       (F31)
```

**Clean copy (.docx) — F15b.** Regenerates the full contract from the DB through the style config — current state, no markup. The **recipient selector** splits into two intents (DD-61): a **send** (Counterparty / Legal) cuts a snapshot, stamps the pending edits group under it (closing this round's change set), and advances that party's `last_shared_with_X` pointer — the diff baseline for that source's next inbound revision; a **grab** (Internal / Copy-only) regenerates and downloads the current file with **no snapshot, no pointer, no lineage effect** — the operator re-reading their own draft is not a new version. Replaces the old single "Mark as sent to counterparty" checkbox — DD-48 generalised that one exclusive tag into the named pointers. Always available: this is the round-1 send and the foundation the other two exports build on.

**Redline (.docx) — F15.** Tracked-changes export: the diff between a chosen baseline snapshot and the current working copy, emitted as Word `<w:ins>`/`<w:del>` the counterparty can Accept/Reject (DD-51).
- **Baseline:** defaults to the **`last shared with counterparty`** pointer (DD-48) — the version they last saw, so the redline shows exactly what changed since. Operator can override the dropdown to any earlier snapshot (e.g. redline against the legal-shared version).
- **Author:** every change is attributed to the operator's configured organization name (F25), **never "Donna"** (DD-44).
- **Appearance:** insertions underlined, deletions struck through, moved clauses shown as tracked moves (F08f surfaces as a move, DD-13); **pure renumber shifts are suppressed** (§12) so the counterparty sees substantive change, not numbering noise.
- **Requires a baseline:** a brand-new contract has no snapshot to diff against, so Redline is **disabled until the first send (Counterparty / Legal) cuts a snapshot** — a Copy-only/Internal grab cuts none and never satisfies this, with the hint *"No snapshot yet — send a clean copy first to set the baseline; from the next round you can send redlines."* This matches the core workflow: round 1 = clean copy, every round after = redline.

**Issue list (.docx) — F31.** A single .docx table of **unresolved** issues — for briefing a principal, walking the counterparty through open points on a screen-share, or the operator's own record.
- **Filter:** `status='open'` only (DD-65 — "unresolved" is now binary). **Excludes** every `closed` issue (resolved / parked / dropped — lives in the in-app collapsed Closed section).
- **Order:** priority descending, ties broken by document order; free-floating issues (no clause anchor) grouped at the bottom under a separator row.
- **Language:** constructive, neutral, counterparty-safe. **No internal fields** (Donna reasoning, authority / `needs_legal_review` flags, session IDs), **no Donna attribution, no DB IDs.**

| Column | Content |
|---|---|
| # | Sequential rank — 1..n in render order (priority-desc sort, free-floating last). Not the raw `priority` value (an internal triage number, not counterparty-safe); the printed `#` is a stable reference number so the operator can say "item 3" on a walkthrough (DD-60) |
| Clause | Node reference (e.g. 3.4.12) or "—" for free-floating |
| Issue | Short title (the issue `title`) |
| Status | `Open` (the export lists open issues only, DD-65) |
| Raised by | `Us` / `Them` / `—` (derived from `initiator`) |
| Our position | Brief factual summary of our stance |
| Their position | Brief factual summary of their stance |
| Proposed resolution | Donna's constructive landing zone — favours the operator but framed as a mutual solution; `—` if Donna has not drafted one |

**One artifact, two audiences (decided):** the export is counterparty-safe by construction, which makes it equally usable as a principal briefing and as the operator's own outstanding-items record — no separate "internal" variant in v1. A **full-status record export** (including agreed items, an end-of-deal archive) is deferred pending real demand; the in-app Agreed tab already covers "what we've agreed" for the operator. **No CSV in v1 (decided):** the .docx issue list serves the briefing / walkthrough / record need; a parallel CSV adds a second format and maintenance surface with no distinct user-visible capability for a single operator (fails the effort-saved test) — revisit only if data-manipulation demand appears. (DD-60.)

**Snapshots & baselines (DD-61).** Only a **send** cuts a snapshot — i.e. a Clean-copy export with recipient **Counterparty** or **Legal**. That snapshot stamps the pending `node_versions` edits group under it (closing this round's change set, which is what the next redline diffs) and advances the matching `last_shared_with_X` pointer. A **grab** — Clean-copy with recipient **Internal** or **Copy-only** — only regenerates and downloads the current file: **no snapshot cut, no pointer moved, no edits-group stamped, zero lineage effect.** Rationale: an operator re-pulling the current .docx mid-draft to read it is not a new version; cutting a snapshot on every grab would fragment one round's change set into many tiny snapshots and litter the lineage view (F27) with phantom versions, muddying both the redline baseline and the "where are we" briefing (§2.4 trust). Export only ever sets `shared` pointers; the `received` pointer is set solely by the Phase-2 revision-import path, pointing at an immutable as-received snapshot captured at import before any edits (DD-48) — the export/send path never touches it. A `shared` pointer can be moved retroactively from the snapshot-history view if a recipient was set wrong. Mode B counterparty-revision import diffs against the `last shared with counterparty` pointer (DD-48).

### Negotiation cockpit

**Build status (2026-06-23) — increment-1 shipped at `/contracts/{id}`; the full design below is the target.** Built: read-only clause tree (depth indent, derived numbers, headings, issue-count badges) · **jump-to-clause-by-number** (a `/`-focused command bar — type the clause number the counterparty says, it scrolls/flashes that node) · **clause search** (F05b: same bar also does keyword substring jump with match-cycling, and an AI conceptual fallback on no-literal-match — the project's first live-LLM surface) · **raise an issue in seconds** (select clause or none=free-floating → one **Description** box + **Us / Counterparty "who raised it" toggle** → creates with `initiator=operator|counterparty`; the box routes to `our_position`/`their_position` and `title` is auto-derived, DD-59) · **issue list** (who-raised badges, click-anchor to jump). The right rail cycles three tabs — **Issues** (the open list + a collapsed "Closed (N)" section, plus the free-floating "+ new issue" affordance) · **Current Clause** (DD-66: the selected clause's text + a raise-issue form scoped to that clause + that clause's open issues) · **Donna** (F10 Q&A). Clicking an issue card — on either Issues or Current Clause — drills into the single-issue **resolution view** (DD-68, see *Issue detail view* below); a back arrow returns to where you came from. Issue **status** is the binary **segmented Open|Closed toggle** (DD-65); the issue **description is editable in place** (`title` + position via `PATCH /issues/{id}`, DD-67); the **comment thread is removed** (DD-67). Planned next (Phase 2): F05 collapsible tree + defined-term hover; per-issue Donna analysis. Structural editing is now **live in the cockpit** via the per-clause ⋮ menu — inline **edit** (F08), **insert** (F08b), **delete** subtree (F08e) — plus a **Rearrange mode** (F08f, @dnd-kit, lazy-loaded) for drag reorder + reparent. **Front-/back-matter are excluded from drag** — only the operative clause tree is rearrangeable; structural editing of front/back-matter stays in the import-review screen.

### Layout

Three layers, always present:

```
┌──────────────────────────────────────────────────────────────────┐
│  [ Document ]  [ Open Issues (N) ]  [ Agreed (N) ]  [ Donna ]   │  ← Layer 3: top tabs
├──────────────┬───────────────────────────────────────────────────┤
│ [Tree][Issues]│                                                   │  ← Layer 1: left panel toggle
│              │                                                   │
│  LEFT PANEL  │  RIGHT PANEL — document view                     │
│  (see below) │  Always the contract. Never changes.             │
│              │                                                   │
└──────────────┴───────────────────────────────────────────────────┘
```

### Layer 1 — Left panel (always visible, two modes)

Toggled explicitly by the user. Never switches automatically.

**Tree mode (default — live call)**
Full clause hierarchy, all nodes. Issue badges (●) mark contested clauses at a glance. Agreed clauses show (✓). Clean clauses show nothing. Click any node → instant jump in the right panel.

**Issues mode (explicit toggle — between calls / pre-call prep)**
Shows only clauses that have open issues, one line per issue with a short summary label. Free-floating issues (contract-level, no clause anchor) listed below under a separator. Click any → jumps to that clause in the right panel. Nothing else shown — clean signal, no noise.

**Persistent action — + Free-floating issue.** A button pinned at the bottom of the left panel, present in both modes and independent of document scroll position. One click opens inline issue creation with the anchor preset to document-level (editable). This is the fast path for capturing a contract-level point mid-call without navigating away from the clause currently in view (e.g. you're deep at 15.1 when a general concern surfaces). Hidden only in Focus mode (screen-share), where capture is not in use.

```
TREE MODE                        ISSUES MODE
─────────────────────────────    ─────────────────────────────
▼ 1. Definitions                 OPEN (12)
▼ 2. Purpose                     ● 3.4.12  Royalty rate
▼ 3. Sale & Purchase   ●         ● 4.1(b)  Payment terms
  ▼ 3.4               ●         ● 7.2     IP ownership
    3.4.12             ●         ...
▼ 4. Payment           ●
  4.1(b)               ●         FREE-FLOATING (3)
▼ 7. IP                ●         ○ Governing law TBD
                                 ○ Margin concern flagged
```

### Layer 2 — Right panel (document view, always visible)

Renders the full contract scrollably. Looks like a contract, not a database UI. This is what gets screen-shared to the counterparty.

**Inline actions on any clause (hover to reveal):**
- **Edit** — clause body becomes an inline editable field. Save → markup resolves, version logged, audit trail written.
- **+ Issue** — opens issue creation inline. No page change. **Single-box capture (DD-59):** two inputs only — **Anchor** (defaults to the clicked node — clause or sub-clause, any depth; editable at creation and re-anchorable afterward, since `node_id` is mutable per DD-17) and one **Description** box where the operator types the substance of the point in plain prose — plus the **Who raised it** Us / Counterparty toggle, which sets `initiator=operator|counterparty` (`donna` reserved for F28 auto-flag) **and routes the Description box:** operator-raised → `our_position`, counterparty-raised → `their_position` (it is *their* stance — keeps `our_position` clean for Us; this is the field Donna reads for her recommendation and the DD-50 source-stance). **`title` (NOT NULL) is auto-derived** from the Description text — a deterministic first-line/truncation snippet computed at raise (instant, no LLM on the capture path) — and is the short label in the Open Issues list; it is **editable in the issue detail view** (and may optionally be refined to a cleaner Donna-generated label by the async analysis pass). There is **no separate title field at capture** — the prior Title + Note two-field form is collapsed to this one box. Donna is not involved at creation. Save → issue appears in Open Issues list with ● badge on the anchored clause; the routed position is populated immediately, so Donna's analysis (triggered asynchronously) has the operator's stance to read and is ready by the time the detail view opens. **Document-level (free-floating) issues** are created via a persistent **+ Free-floating issue** button pinned in the left panel (always visible, in both Tree and Issues modes, independent of document scroll position — no navigation required even when scrolled deep into a clause), or via **+ New issue** in the Open Issues tab. Both default the anchor to document-level (editable — can be anchored to a node if desired).
- **⋮ menu** — Insert clause above / Insert sub-clause / Insert clause below / Delete / Move.

**Quick-jump bar** — top of right panel, always visible:
```
[ Go to clause: _______ ]
```
Type "3.4.12", hit Enter → instant scroll. The single most important live-call feature.

**Auto-renumber on structural change:** deleting or inserting a node recomputes all sibling and descendant numbers from tree position (DD-02). Cross-references that point to renumbered nodes update their rendered number automatically (DD-11). The operator never manually renumbers anything.

**Focus mode (screen-share toggle):** collapses the left panel, hides issue badges, hides top tabs. Counterparty sees a clean document. One button to enter and exit.

### Layer 3 — Top tabs (contract-level views)

| Tab | Contents | Primary use |
|---|---|---|
| Document | The two-panel cockpit (layers 1+2) | Default — live call |
| Open Issues (N) | Full sortable/filterable issue list across the contract. Filter by status / authority / category / clause. Click any issue → issue detail view. | Pre-call prep, briefing export |
| Agreed (N) | Read-only list of all closed/agreed positions. Visually locked — grey, no edit path. | "We already agreed this" defence during calls |
| Donna | **Single-contract grounded Q&A** — persistent chat thread (history survives across sessions, DD-40). Locate / explain / status-briefing ("what's still open?", "what did we agree?"); answers **cite clause nodes, clickable to jump**; **read-and-explain only** (advice routes out). See the Donna tab spec below. | Between calls, briefing prep |

### Donna tab — single-contract Q&A (F10)

The contract-level Q&A surface (Layer 3). A **persistent chat thread**, one per contract (`donna_conversations`/`donna_messages`), that survives across sessions — the operator returns days later to her prior Q&A, not a blank box (DD-40). Scope locked to v1 in DD-62.

```
┌─────────────────────────────────────────────────────────────┐
│  Donna · <Contract>                                          │
├─────────────────────────────────────────────────────────────┤
│  You:   What's still open on payment terms?                  │
│                                                              │
│  Donna: Two issues are open under §4 Payment:                │
│         • 4.1(b) Payment terms — we want net-30, they …      │
│              ↳ [4.1(b)]   ← click jumps to the clause         │
│         • 4.3 Late-payment interest — open, no position yet   │
│              ↳ [4.3]                                          │
│                                                              │
│  You:   Where's the liability cap?                           │
│                                                              │
│  Donna: The cap is in §11.2 "Maximum aggregate liability".   │
│              ↳ [11.2]                                         │
├─────────────────────────────────────────────────────────────┤
│  [ Ask about this contract…                             ↵ ]  │
└─────────────────────────────────────────────────────────────┘
```

- **Three question shapes**, all read-and-explain: **locate** ("where's the liability cap?"), **explain** ("what does clause 12 say about termination?"), **status-briefing** ("what's still open?", "what did we agree?" — answered over the issue ledger; the headline capability for briefing a principal, the clearest thing Word/Ctrl-F can't do).
- **Cited, clickable answers.** Every answer cites the node(s) it drew from at the **clause/sub-clause level**; each citation is a chip that **jumps to that clause** in the right panel (reuse the F05/F05b jump). No char-offset highlighting in v1.
- **Read-and-explain guardrail.** The box **explains the contract; it never advises, drafts, or takes a position.** "Should I accept this?" / "is this enforceable?" / "what should we counter?" are **not** answered here — Donna deflects to the issue-scoped surface (F11, where advice + drafting + live research live) or, when the question needs legal judgment beyond the document, to **"get a lawyer"** with a framed question + attached clauses (DD-14 rules 1–2). A positional assertion made outside an issue and ungrounded in a ledger is exactly the §2.4 credibility risk this guardrail removes.
- **Honest failure.** When the answer isn't in this contract, Donna says so plainly — "I don't see anything in this contract about X" — and never fabricates.
- **Grounding.** Answers draw **only** from this contract's node text + the issue ledger (`our_position`/`their_position`) + agreed/open status — no outside law or general knowledge in this surface (live market research stays in F11, DD-38). Retrieval finds the clause the operator *means* even when her words differ from the contract's via the **F05b conceptual lookup — no embeddings** (DD-62); `node_embeddings` (F12, Phase 2) later backs the same surface invisibly.
- **Single contract, not whole-deal** in v1. Cross-contract "what did we agree with this counterparty across all agreements" is a later add (the schema already makes `donna_conversations` one-per-contract).
- Context is managed by the DD-40 window (last 10 turns + rolling summary); the operator manages nothing. Optional "new thread / clear" affordance, not load-bearing for v1.

### Issue detail view — the single-issue resolution view (DD-68)

A shared master-detail drill-in, opened by clicking an issue on the **Issues** tab or one of a clause's open issues on **Current Clause**; a back arrow returns to the originating surface, and drilling in selects + flashes the issue's clause in the left tree. **Shell built** (clause context + editable issue + Open/Closed toggle + resolve-by-editing-the-clause). The **DONNA** section in the mockup below is the **F11 placeholder** (advisory recommendation + proposed redline + accept/reject/edit + brainstorm — Phase 2; the one place Donna advises, DD-62/DD-14).

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Open Issues (12)                              Clause 3.4.12      [×] │
├─────────────────────────────────────────────────────────────────────────┤
│  Storage Rate                                                           │
│  ● open  ·  Commercial  ·  Raised by us  ·  Priority 2                 │
├─────────────────────────────────────────────────────────────────────────┤
│  CLAUSE                                                                 │
│                                                                         │
│  3.4.12  Storage Rate                                                   │
│  The Licensor shall store Products at a rate of [USD 10/ton] per       │
│  month. Storage fees shall be invoiced monthly in arrears.             │
│  {Licensor} has the meaning given in §1.1.                             │
│                                              [→ Jump to clause]         │
├─────────────────────────────────────────────────────────────────────────┤
│  ISSUE DESCRIPTION                                                      │
│                                                                         │
│  We do not accept the proposed storage rate. We want to negotiate       │
│  a higher rate more in line with market standard.                       │
├─────────────────────────────────────────────────────────────────────────┤
│  DONNA                                                      [↻ Refresh] │
│                                                                         │
│  The proposed rate of USD 10/ton is below market for comparable         │
│  storage arrangements in this sector.                                   │
│                                                                         │
│  Market range (comparable projects, 2025–2026): USD 13–18/ton          │
│    ↳ Global Storage Pricing Index, June 2026                           │
│    ↳ Industry Transaction Database, May 2026                           │
│                                                                         │
│  Counter at USD 15/ton — market midpoint, defensible with data.        │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  "…shall store Products at a rate of USD 15/ton per month…"    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  [ Use Donna's language ]  [ Edit Donna's language ]                   │
│                                                                         │
│  [ Brainstorm with Donna ↗ ]                                           │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Status:  [ Open │ Closed ]              [ ↑ Escalate to principal ]    │
└─────────────────────────────────────────────────────────────────────────┘
```

**Behaviour notes:**
- **Issue Description** — the populated stance field (`our_position` or `their_position`, routed by `initiator` at capture, DD-59); operator-written at creation, editable inline. This is what Donna reads to generate her recommendation. The header **title** (the auto-derived short label) is also editable here.
- **Donna's recommendation** — generated asynchronously after issue creation; ready when the detail view opens. If the issue involves a market data point, Donna invokes live research (DD-38) and cites sources. Reasoning purely from clause context + agreed/open positions otherwise (DD-27).
- **Use Donna's language** — applies her proposed text to the clause body immediately. **Edit Donna's language** — opens the clause inline editor pre-filled with her proposed text.
- **Brainstorm with Donna ↗** — opens a chat overlay pre-loaded with the clause + issue description. Back-and-forth exploration ("what if we propose a tiered rate?", "what's the floor we should accept?") without leaving context. The conversation is **ephemeral** — not persisted; it opens fresh each time. If the brainstorm produces language the operator accepts, that committed outcome applies to the issue through the standard apply path (adopted language lands on the issue, node version + audit entry written, decision logged per DD-29). Only the adopted outcome is remembered — the exploratory chat and any rejected intermediate proposals are discarded (DD-42).
- **Status control** — a horizontal **segmented Open|Closed toggle** (DD-65), one click to switch; replaces the old status dropdown. Toggling to **Closed** drops the issue from the open list into the collapsed Closed section (not deleted; reopenable).
- **Closing flow** — toggling an issue to **Closed** shows a single confirmation step:

```
  ┌─────────────────────────────────────────────────────────┐
  │  Final agreed language                                  │
  │                                                         │
  │  ┌─────────────────────────────────────────────────┐   │
  │  │  "…shall store Products at a rate of USD 15/ton │   │
  │  │   per month. Storage fees shall be invoiced     │   │
  │  │   monthly in arrears."                          │   │
  │  └─────────────────────────────────────────────────┘   │
  │  Edit if the final agreed text differs from above.      │
  │                                                         │
  │  [ Confirm & close issue ]          [ Cancel ]          │
  └─────────────────────────────────────────────────────────┘
```

  Pre-filled with the current clause body. Editable — covers "agreed as-is" (no edit) and "we settled on different language on the call" (edit before confirming). On confirm: clause body updated (if changed), issue status → `closed`, ● badge on clause → ✓, issue drops from the open list into the collapsed Closed section (the Agreed tab, locked/read-only), audit log entry written.

### Design decisions captured here

Moved to [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md) — see the DD index in §8. DD-19 through DD-48 originated here (UI, Donna, import/export, and version-model decisions).

---

## 10. Design Assumptions from Representative Contracts

Derived from structural analysis of real-world agreements (no confidential content stored). These constrain the design:

- **Word count** ranges ~15k–26k; **paragraphs** ~800–950 per contract. Tractable in a single tree.
- **Numbering is auto-generated** (Word `w:numPr` list numbering) in the **clause body** of every sample → deriving body numbers from the tree is valid (DD-02). Literal typed numbers appear only in the table of contents (regenerated on export, never stored) and occasional schedule/annex headings (resolved by DD-36 prefix inference). Confirmed by spike #1 across 3 real formats (DD-45).
- **Content controls (`w:sdt`) carry fill-in field text** (party names, dates, placeholder values) → the import parser must descend into `w:sdtContent`; default python-docx paragraph iteration misses it and loses the content silently (DD-45).
- **Cross-references are heavy and mixed** — up to ~270 in a single document, both typed text and reference fields → structured-link conversion is essential and import-intensive (DD-11).
- **Tracked-change density varies from zero to 700+** in one file → handled in **Mode B** (counterparty revisions); **first import (Mode A) is clean in v1** (DD-46). Heavy first-import redlines (onboarding a mid-negotiation contract) are a v2 case (DD-34).
- **House styles differ per document** (some emphasize underline, some bold, some both) → per-contract style config (DD-02).
- **Tables, footnotes, and embedded counterparty comments are common** → table/attachment node types, footnote handling, and comments-→-issues seeding (§11).

---

## 11. Import Pipeline

### Mode A — First import: new contract (F03/F04)

One-time per contract. Creates the contract record, node tree, and all linked entities in the DB.

1. **Parse** .docx → candidate node tree (prose/table/attachment), reading auto-numbering from list structure.
1a. **Numbering-pattern inference pass** (DD-36) — re-parents any node whose tree position contradicts its heading-number prefix. Silent auto-correction; no operator action required. Nodes with no numeric prefix retain their parsed depth and are surfaced in structural triage if their position looks anomalous.
1b. **Front-matter regionization** (DD-54) — find the operative-clause boundary: the first top-level numbered heading (e.g. "1. …"). Everything before it is front-matter — the first block is `role: title`, the rest `role: preamble`; these are preserved but excluded from the clause tree and clause numbering. The **table of contents is detected and dropped** (regenerated on export, §10) — never stored as clauses. Clause numbering re-derives from the first operative clause.
2. **Detect & link** (AI-assisted, human-verified): cross-references → links (DD-11); defined terms → registry (DD-10); deal-parameter mentions → references (DD-12).
3. **Clean-document guard** (DD-46) — scan the incoming .docx for tracked changes (`<w:ins>`/`<w:del>`). If any are present, block by default and warn: accept all changes in Word and re-upload, or explicitly confirm import-anyway (changes flattened to their accepted state). v1 first import assumes a clean draft; the Mode A two-tier tracked-change triage is deferred to v2 (DD-34).
4. **Seed issues from existing Word comments** — extract and convert to internal issues anchored to their node.
5. **Surface content hiding in formatting** — footnotes (→ structured footnotes entity), strikethrough / pre-existing tracked changes (→ proposed deletions, never flattened), emphasis no rule explains (→ flagged for review; manual override marker available).
6. **Import-review UI** — operator verifies/corrects structure before commit. Nothing is trusted blindly.

**De-risk spike (run first, before trusting the pipeline):** import one real contract → render it straight back to .docx → diff against the original → eyeball formatting gaps. Empirically tests "formatting is rule-derivable" before we depend on it.

### Mode B — Counterparty revision import (F03b/F03c)

Triggered from within the cockpit of an existing contract. The contract already lives in the DB; this import ingests a counterparty-modified version and surfaces all their changes for review.

1. **Identify baseline** — automatically uses the most recent snapshot marked as sent to counterparty as the diff baseline. Operator can override to an earlier snapshot.
2. **Parse** incoming .docx using the same parser as Mode A.
2a. **Numbering-pattern inference pass** (DD-36) — same as Mode A. Runs before the diff to ensure the incoming tree is correctly structured before comparing against baseline.
3. **Diff against baseline** — two paths (DD-28):
   - **Path A** (tracked changes present in .docx): extract `<w:ins>` / `<w:del>` elements directly from OOXML. Reliable, exact.
   - **Path B** (clean edited copy, no tracked changes): match parsed nodes against baseline snapshot nodes (by section number → heading similarity → body text similarity), then text-diff each matched pair. Unmatched baseline nodes = deleted by counterparty. Unmatched incoming nodes = added by counterparty.
4. **Create `counterparty_revision_session`** — records the import event, parse path used, baseline snapshot, and total change count.
5. **Populate staging tables** — for each node with at least one counterparty edit, create a `counterparty_revision_changes` row (navigation unit). Within it, create one `counterparty_revision_hunks` row per individual text edit (decision unit). Donna pre-classifies each hunk's significance (`trivial` | `substantive`) by semantic impact on the clause — never by edit size. Trivial hunks are pre-recommended Accept with no counter drafted. Substantive hunks get full analysis + exact counter-language. **No issues are created at this stage** — the staging tables are the review workspace, not the issue list.
6. **Structural triage** (before content review) — the numbering-inference pass (step 2a) has already auto-corrected most hierarchy errors silently. Structural triage surfaces the residual cases the inference pass could not resolve: nodes with no recognisable numbering prefix that appear at the wrong depth, headings detected as body text, and any remaining depth anomalies relative to numbered siblings. Short pre-screen with the same level-adjustment tools as Mode A (multi-select, ± level, heading/body toggle). Operator resolves before entering content review. Can be skipped entirely if no residual anomalies remain.
7. **Donna pre-populates** verdict + one-line reasoning + exact counter-language for every substantive hunk before the review UI opens.
8. **Counterparty change review UI** — linear, one change at a time. Left panel switches to changes navigation list (numbered, ✓ reviewed / ● current / blank = pending). Right panel shows the review card: counterparty change rendered as inline tracked markup (strikethrough/underline), then Donna's counter below, then four action buttons: **Accept theirs** / **Use Donna's counter** / **Edit Donna's counter** / **Keep original**.
9. **Log decisions and create outcomes** — every decision written to the hunk record. For **accepted or modified** hunks: change applied directly to the node body; no issue created. For **rejected or deferred** hunks: an issue is created with `initiator: counterparty`, `category: counterparty_proposed_edit`, `their_position` = proposed text, `counterparty_revision_session_id` set. Decision also written to `issues.decision` JSONB with verdict, final language, Donna's archived recommendation, and optional operator reasoning (DD-25, DD-29).
10. **Unresolved changes** — any hunk not actioned during the session remains in the staging table as `verdict: pending`. Review can be paused and resumed at any time. On resumption: the changes navigation list restores exactly where the operator left off — decided hunks show their outcome (✓ accepted / ✗ rejected / ✎ modified), pending hunks show as blank. Donna's pre-populated counter-language persists. The operator picks up from the first pending hunk. Pending hunks on final session close (operator explicitly ends the review) surface as open issues in the cockpit.

---

## 12. Export Pipeline (F14 / F15b / F15 / F31)

Three operator-facing exports (§9 `Export ▾`), all surfaced from the cockpit, all rendered through the per-contract style config so styling is never decided at export time.

**Clean copy (.docx) — F15b** (the DB→.docx renderer; the foundation build):
1. **Snapshot** the contract — **only on a send** (recipient Counterparty / Legal): captures the full tree topology + node bodies at this point in time (DD-09, OQ-08), stamps the pending `node_versions` edits group under the new snapshot (closing this round's change set), and advances the matching `last_shared_with_X` `shared` pointer (DD-48). A **grab** (recipient Internal / Copy-only) **skips this step entirely** — no snapshot, no pointer, no edits-group stamp; it goes straight to regenerate + download (DD-61). Export sets `shared` pointers only; `received` pointers are set solely by the Phase-2 revision-import path (DD-48), never by export.
2. **Regenerate** the full .docx from the DB through the style config (numbering, fonts, emphasis rules). Safe because the data layer is content-complete — there is no hand-edited Word file to clobber (DD-43).
3. **Integrity check** — verify content (wording, numbering, tables, special chars) is preserved; only styling may be normalized to the house style.

**Redline (.docx) — F15** (builds on F15b's renderer):
4. **Tracked changes** from the diff between the chosen baseline snapshot (default `last shared with counterparty`, DD-48) and the current working copy (DD-03). Content and structure changes are shown — moves as tracked moves (DD-13); **pure renumber shifts are suppressed**. Emitted as Word `<w:ins>`/`<w:del>` (DD-51), each authored by the operator's configured organization, **never Donna** (DD-44, F25). Requires a baseline — disabled until the first snapshot exists (§9).

**Issue list (.docx) — F31:** a standalone table render (no snapshot, no contract body) of unresolved issues (`status='open'` only, DD-65) — columns, filter, and order in §9. Constructive, counterparty-safe language only.

Internal artifacts — issue notes, Donna reasoning, `drafting_note` nodes (DD-54), "ask the principal" TODOs, authority / `needs_legal_review` flags — **never** cross into any counterparty-facing export.

---

## 13. Phased Build Plan

| Phase | Deliverable | Gate (done = …) |
|---|---|---|
| **0 — Import spine** | Parser → node tree (incl. `w:sdt` content-control extraction, DD-45); cross-ref/term/parameter detection; clean-document guard (DD-46); comments→issues; import-review UI; **de-risk spike first** | All contracts correctly structured; parse verified by operator |
| **1 — Cockpit** | Clause tree browser; issue capture with initiator/authority/category/status; direct-edit path; audit log | Operator runs a live call capturing in Donna (Word still screen-shared) |
| **2 — Donna's brain** | Deal- and issue-scoped AI; tiered RAG + contested-node history; behavioral contract; defined-terms registry; semantic search; counterparty revision import + change review (F03b/F03c); decision logging (F03d) | Donna answers and brainstorms (every claim cited to a node); counterparty revision import functional; decisions logged |
| **3 — Redline export** | Snapshot (F14); clean-copy DB→.docx regenerate via style config (F15b); tracked-changes redline, renumber suppressed (F15); issue-list export (F31); version diff | A counterparty-readable redline with verified round-trip integrity |
| **4 — Cross-contract layer** | Deal parameters defined once, referenced, ripple-flagged; import-time inconsistency flags | Changing a shared value flags every ripple across contracts |
| **v1.1 — Principal portal** | Curated read-only view + issue-decision write path | Principal can read escalated items and decide them; no edit path |
| **v2 — Backlog** | Granola/transcript ingest; style-config UI editor; negotiation pattern learning (DD-29 v2); first-import tracked-change triage — onboard an existing in-flight contract (DD-34, DD-46); three-way merge (reconciling parallel legal team revision + operator verbally-captured edits — deferred pending v1 learnings and counterparty sequencing validation, DD-39) | — |

**Critical path:** With first import clean-only in v1 (DD-46), the hardest tracked-change work now lives entirely in **Mode B counterparty-revision review (Phase 2)** — that is the long pole, built and de-risked alongside Donna's brain. Phase 0 shrinks to clean-parse + structure review + content-control extraction; its round-trip de-risk spike is already validated (DD-45). If Phase 2 Mode B slips, Phase 1 still delivers the capture cockpit — never worse than Word.

Counterparty revision import (F03b/F03c) and decision logging (F03d) land in Phase 2 alongside Donna's brain — they share the same AI infrastructure and issue workflow.

**Phase 1 build order (locked 2026-06-23, from the operator's live-call workflow).** The gate is surviving a live counterparty call without Word. The operator's three named value-adds set the sequence:
1. **Cockpit core loop** — open a committed contract → **quick navigation** (clause tree + jump-to-clause-by-number, killing the "frantic scroll to the clause they named") → **raise an issue in seconds** on the selected clause, capturing a quick note **and who raised it (us vs counterparty, F06 `initiator`)**. This is the live-call capture; it beats Word and ships for the next call.
2. **Direct-edit / new-node** (F08/F08b) — inline edits + add-clause live.
3. Then F01c style templates / polished home browser.

Explicitly **deferred** (not Phase 1): live speech-to-text / transcription auto-issue (F23) — operator types fast enough; net-negative vs typing.

**Deadline mapping**

| Deadline | Target |
|---|---|
| Next live call with counterparty | Phases 0 + 1 (capture cockpit) |
| In-person principal meeting (~1 month) | Through Phase 4 + v1.1 ready |

---

## 14. Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Next.js | Clause tree + issue UI |
| Backend | FastAPI (Python), async | Consistent with AI services |
| Database | PostgreSQL + pgvector | Structured data + embeddings in one store |
| Doc parsing/generation | python-docx (+ direct OOXML where needed) | Import parse; export regeneration with tracked changes |
| AI | Anthropic API (Claude) | Brainstorming, comprehension, import extraction |
| Retrieval | LangChain + pgvector | Node-level chunking + metadata filters |
| LLM abstraction | LiteLLM wrapper in `services/` | Per project standards |
| Config | pydantic-settings (BaseSettings) | No scattered env reads |
| Formatting / lint / types | ruff + mypy (strict) | Per project standards |

---

## 15. Open Questions

| # | Question | Blocking | Notes |
|---|----------|----------|-------|
| OQ-03 | Rendering fidelity: acceptable formatting-drift bar, measured by the de-risk spike | F15 | Gate on spike results before trusting export |
| OQ-04 | Defined-term & parameter detection: regex first pass vs AI extraction + human review | F16, F18 | Likely AI extraction with human verification on import |
| OQ-08 | Snapshot storage mechanism — what a snapshot stores | F14, F15, F03b | **Constraint (engineering):** a snapshot must reconstruct the *full tree topology* (parent_id, order_index, is_deleted) plus node bodies at that point in time — redline export (DD-03/F15) diffs structure (insert / delete / move), and structural moves are *not* versioned in `node_versions`. This eliminates pointer-only replay of `node_versions` as an option. The engineering ADR narrows to: full per-snapshot tree dump vs. topology-snapshot + body-version-pointer hybrid; choice affects Mode B diff performance. |

**Resolved:** binary attachments → attachment node type (DD-05); cross-references → structured links (DD-11); diff baseline → last snapshot (DD-03/09); auth → Operator/Principal roles from day one, portal in v1.1 (DD-15); style format → per-contract JSON (DD-02); semantic markup vocabulary → locked inline marker set, plain-text input auto-resolved on save (OQ-01); soft-delete on nodes → `is_deleted` + `deleted_at` fields; free-floating issue anchoring → `node_id` is mutable post-creation; counterparty revision diff mechanism → DD-28; tracked-change bulk triage UX → two-tier with full Donna candidate list + operator veto before commit (DD-34, OQ-02); live screen-share readability → cockpit in Focus mode is screen-shareable from Phase 1; capture-first approach validated by design (OQ-05); style-config JSONB schema locked (DD-37, OQ-06); numbering-pattern hierarchy inference auto-corrects parse errors before operator review (DD-36); order_index maintenance → gap-based (100/200/300, insert at midpoint, rebalance on gap exhaustion; the engineering ADR, OQ-07).

---

## 16. Out of Scope for v1

- Multi-user / team collaboration beyond operator + principal (permissions designed for it; not built)
- Real-time collaborative editing
- **First import of an already-redlined contract** (Mode A tracked-change triage) — v2; v1 first import is clean-only and guarded on import (DD-46, DD-34)
- Granola / transcript ingest (v2 — F23)
- Style-config UI editor / per-contract override panel (v2 — F24); import-time style detection is Phase 0 under F04
- **Post-signature querying of signed contracts via Donna** (v2 — folds into the cross-deal knowledge base, same surface/infrastructure). Signed contracts are frozen read-only (`status: signed`) and retained in the DB; Donna is not invocable on them in v1. Low priority — the v1 bottleneck is reaching signing, not querying after. Amendments are handled as new contracts under the deal (DD-41).
- PDF contracts, e-signature, mobile, external-counsel annotation workflow
- Billing / SaaS / hosted deployment

---

## 17. Project Structure (target)

```
donna.ai/
├── SPEC.md                  ← this file (the hub)
├── DESIGN_DECISIONS.md      ← ADR log (DD-NN records; indexed in SPEC §8)
├── CLAUDE.md                ← project engineering rules
├── README.md                ← what it is, how to run, inline architecture diagram
├── .env.example
├── frontend/                ← Next.js (clause tree, issue UI, Donna panel, principal portal)
│   └── app/
├── backend/
│   ├── api/                 ← FastAPI routes (thin)
│   ├── services/            ← business logic: import, export, diff, LLM calls, consistency
│   ├── models/              ← Pydantic schemas (nodes, issues, parameters, state)
│   ├── prompts/             ← versioned prompt templates + utils.py
│   ├── config/              ← pydantic-settings
│   └── services/donna/      ← RAG retrieval + Donna's surfaces (no LangGraph in v1, DD-52)
├── db/
│   └── schema.sql
└── evals/                   ← AI output quality (separate from tests)
```
