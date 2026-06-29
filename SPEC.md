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
     →  Export redlined .docx  →  send it manually
     →  Mark as sent (cuts the snapshot, advances the baseline; DD-71)
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
     →  Export redline, send it, Mark as sent (cuts snapshot), continue
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

**Deployment model — local-only, permanently (DD-74):**
- **v1 runs entirely on the operator's own machine** via a single Docker Compose startup — no remote/hosted deployment in scope, no IT involvement. Contract data stays on the machine (beyond Donna's Anthropic API calls; see Data & AI processing below).
- **Multi-machine = independent installs.** The operator runs it on either machine (work HP or personal Mac); each is a standalone install with its **own local database**. The two are **not synced** — a contract lives on whichever machine created it, and that divergence is accepted (no "home" machine, no cloud, no cross-machine identity).
- **Hosted production is deferred, not planned** (DD-74). The Switzerland data-sovereignty rationale and any auth (DD-53) return only if remote/multi-user is ever needed — designed then, with the v1.1 principal portal (F22).

**Data & AI processing:** contract content is sent to Anthropic's API (Claude) for Donna's analysis. This is acceptable under a Data Processing Agreement (DPA) with Anthropic — operator has an existing Anthropic relationship and DPA is the procurement path. DPA must be in place before any production contract data is processed. Local demo with non-confidential or synthetic data does not require the DPA.

The principal portal is built in **v1.1** — ready even if not yet shown to the principal.

---

## 5. Feature Registry

Priority: P0 (MVP) · P1 · P2. Phase maps to the build plan (§13).

> **Donna's recommendation rows compose into one engine, not six features.** F32 · F37 · F36 · F34 · F35 · F30 are the limbs of the DD-93 grounding pipeline behind every recommendation (consumed by F03c, F11, and F38) — read them together via §7 *"Donna's recommendation engine."*

| # | Feature | Priority | Phase | Status | Notes |
|---|---------|----------|-------|--------|-------|
| F01 | Client management (Settings → Clients) | P0 | 0 | **built** | Required before first import. Full CRUD: `/settings` UI list + create + **edit/rename + delete** (delete FK-guarded → 409 if referenced; `status` archive separate) |
| F02 | Deal management (Settings → Deals, or inline on import) | P0 | 0 | **built** | Group contracts under one deal; scope for shared parameters & defined terms. Full CRUD in `/settings` (deals grouped under client, `position` captured; edit/delete with FK-guard) |
| F01b | Contract type management (Settings → Contract Types) | P0 | 0 | **built** | User-configurable taxonomy; pre-seeded (seed.sql); never hardcoded. Full CRUD in `/settings` (create/edit/delete) |
| F01c | Style template management (Settings → Style Templates) | P2 | v2 | **deferred (product, 2026-06-25)** | DEFERRED to v2 — co-build with F24. The pain it claims to remove ("redundant style setup across contracts") doesn't exist in the import-driven v1 flow: F04/DD-37 auto-detects each contract's style on import + an empty `{}` renders house defaults, so no operator does redundant setup. A real Settings editor is ~90% of the v2-deferred F24 (both need the same DD-37 field editor + live preview); splitting that machinery across v1/v2 is artificial. **Un-defer trigger:** observed redundant re-tuning of the *same* house style across contracts, OR a need for a named reused *non-house* style — then build F01c + F24 together. **Settled at-build-time (product, not yet built):** picking a template **COPIES (snapshots)** its config into `contracts.style_config` at apply-time — the renderer reads only `style_config` (single render-truth); `style_template_id` is **provenance**, not a live render link, so template edits never back-propagate onto in-flight/sent contracts (DD-48/DD-61 baseline discipline). Schema (table + FK + override col) is already in place — zero cost to leave it sitting. |
| F01d | Home screen — client + deal + contract browser | P0 | 1 | **built** | `/` = home (recent-contract resume cards, live data, status badge + open-issues + recency, empty state); `/contracts` browser + per-contract **edit/delete** (cascade); persistent top nav (Import·Contracts·Settings, logo→home). Deeper client→deal→contract collapsible sidebar is a later refinement |
| F03 | First import — new contract (.docx → structured node tree) | P0 | 0 | **built** | parse→tree→persist + import/get-tree routes; **live-DB verified** on real contracts (413-node round-trip). **DD-54 content-role classification landed** (backend): boundary/front-matter/operative split, TOC dropped, clause-only numbering — boundary validated at 57/41/17 on JVA/OA/TLA. F04 review UI (incl. role-region rendering) is the remaining piece. See §11 |
| F03b | Counterparty revision import (incoming .docx → change list) | P0 | 2 | **Path B backend done** (Path A + Donna-significance deferred) | Counterparty revision import (backend, Path B clean-diff): incoming clean `.docx` → Mode-B matcher (`match_revision`) diffs it against the `last_shared_with_{party}` snapshot → staged as four change buckets (edited / new / deleted / abstain) in `counterparty_revision_session/_changes/_hunks` for F03c review; tracked-markup docs rejected (422). Single-open-session + no-baseline guards (409); DD-63 cascade extended (FK-correct). Matched revised nodes inherit their baseline classification; only genuinely-new nodes default to `clause` (DD-82). See §11 Mode B; DD-25/63/64/70/78/82. **Deferred:** Path-A tracked-changes extraction (422 stub), Donna LLM hunk-significance. Build detail in git + DEV_TODO. |
| F03c | Counterparty change review — accept/reject/modify + Donna counter-language | P0 | 2 | **built** (backend + frontend; real-data apply-spine verified; pending Lilly UX acceptance) | Inline Word-style per-change redline review of a counterparty revision, with a guided decision cursor (stable dock, auto-advance through open changes in doc order, keyboard nav `<`/`>`, re-open a decision). Two phases (DD-78): structural match-confirm (the Mode-B matcher's ranked abstain queue → reclassify each into matched/new/deleted so content review starts fully classified) → content review (one document-ordered stream of the three settled types, not four bucket-screens). Single inline embedded full-clause redline (insertions green/underline, deletions red strikethrough, read in place like Word); **per-change (Word-style) decisions** — each edit its own Accept theirs/Use Donna's/Edit/Reject (whole-node added/deleted = one node decision). Donna's per-change counter **auto-runs in the background at import** (DD-82, failure-isolated, substantive-only) so "Use Donna's" is live on open; redline stays the deterministic diff, the counter is operator-adopted (DD-64). Revised side **inherits baseline classification** (DD-28/54). Verdict-aware projected reading order with live renumber on accept/reject (DD-88); decision-state visual encoding — rec-vs-chosen buttons + reject trace (DD-83/91); "Start over" resets the review (DD-86). Apply (one txn) reuses F08 edit/insert/delete paths and seeds a counterparty-proposed `issue` per rejected hunk/node. See §11 Mode B; DD-26/27/28/54/64/78/79/81/82/83/86/88/91. **Deferred:** 6a tree-shape anomaly source, F03d rich logging. Build detail in git + DEV_TODO. |
| F03d | Negotiation decision logging (learning infrastructure) | P0 | 2 | planned | Every accept/reject/modify decision logged with rich context; feeds Phase 2 RAG and v2 pattern learning; DD-29 |
| F03e | Inline enumerator splitting — `(a)(b)(c)` / `(i)(ii)(iii)` → child nodes | P1 | 1 | **built** | **Greenlit by Lilly 2026-06-25, reverses the prior won't-fix-v1** (§6). At parse time, an inline enumerator run inside one paragraph splits into ordered children of its lead-in clause. **Acceptance (met):** a paragraph `lead-in: (a) X (b) Y` parses to **1 parent + 2 children** — the lead-in text is the parent body, each `(a)`/`(b)` an ordered child, **child order preserved**. **Edge rules (§6):** defined-term definitions (`"Term" means (i)… (ii)…`) are **never split** (permanent carve-out); **flat-only in v1** (nested `(a)…(i)…(b)` not recursed); child body **retains its own `(a)`/`(i)` marker** as native text (Donna doesn't re-derive alpha/roman); **new imports only** — committed trees are not retroactively re-split. **Round-trip de-risk gate HELD (2026-06-25):** reassembly oracle byte-identical on the synthetic always-runs fixture AND the real demo contract; split is content-lossless by construction (split only at whitespace-bounded markers on normalised text → join reproduces source exactly), so a mis-detection can only add an operator-correctable node, never corrupt content. Built in `services/import_/inline_split.py` (`split_inline_enumerators`), wired into the import pipeline after role stamping. Full suite 373 green, mypy/ruff clean. See §6 inline-enumeration note |
| F04 | Import-review UI — first import (verify/correct parse before commit) | P0 | 0 | **built** | First-import review UI (Next.js two-panel tree+source): verify/correct the parse before commit — confidence flags, triage counter, commit gating, uncertain nodes highlighted, on real parses (never trust parse blindly). Role-region rendering (DD-54: front-/back-matter as labeled regions, drafting-notes flagged, non-clause rows unnumbered) + AI back-matter categorization (DD-56/58: known appendix-title designators detected deterministically as dividers, a Haiku whole-region pass categorizes the rest and can promote unseen designators). Style detected at import (DD-37). **All correction ops complete** — inline level/type/role edit (role re-buckets the node live), multi-select bulk shift, multi-select delete (Delete/Backspace removes selected rows + their subtrees, pre-commit; operator-accepted 2026-06-29), Move ↑/↓ + reparent (commit keyed to operator sequence), split, merge, content-type (Heading/Body/Table) corrections — each round-trips to commit with no-data-loss guards. Only a cosmetic visual-identity pass remains (not a correction gap). **Post-commit destination:** on commit success, redirect to the cockpit (`/contracts/[id]`). **`contracts.origin` capture (built, DD-84):** the import Context step has a required segmented control where the operator declares who authored this contract (`us / our_legal / counterparty`); persisted to `contracts.origin` (verified live), it drives Donna's opening stance for F28/F38 and closes the §6 ↔ schema.sql gap flagged in DD-84. DD-37/54/56/58/84. Build detail in git. |
| F05 | Clause tree browser (collapsible, issue badges, term hover) | P0 | 1 | **built** | Cockpit (`app/contracts/[id]`): read-only clause tree + **collapsible nodes** (twirl, jump-expands-ancestors) + issue-count badges + jump-to-clause-by-number. **Defined-term hover now built** (F16 landed): defined terms get a dotted underline + hover card with the definition (null → "no definition captured"); longest-match, empty-registry-safe |
| F05b | Clause search — keyword + conceptual jump (cockpit jump bar) | P1 | 1 | **built** | Extends the F05 jump bar beyond number-jump: (1) exact keyword **substring jump** with a multi-match counter + ‹/› cycling through hits; (2) **conceptual fallback** — on Enter with no exact literal match, `POST /contracts/{id}/clause-search` returns the best-matching clause. **First live-LLM surface in the project** (LiteLLM wrapper in `services/`, LOW/Haiku tier per DD-35, versioned prompt over clause **headings**). Backend + cockpit UI built + verified (quality 5/5 on real data). Grounded jump only — surfaces an *existing* clause, never generates content (§2.4 trust). **Eng note:** conceptual match is a **per-query LLM call over headings, no embeddings**; embeddings/pgvector (F12/F20) remain the Phase-2 retrieval path and could later back this same surface without changing operator-facing behavior. |
| F06 | Issue creation (select node → operator writes summary → open issue) | P0 | 1 | **built** | Operator writes own title + summary; Donna's analysis loads when the issue is opened, not at creation. **Cockpit UI built** (`app/contracts/[id]`): select clause → single **Description** box + **Us/Counterparty who-raised toggle** → create; captures `initiator=operator\|counterparty` (drives DD-50 source-stance). Issue list with who-raised badges. **Field-routing pending (DD-59):** box must write to `our_position`/`their_position` per the toggle with `title` auto-derived — currently maps box→`title` (engineering to wire) |
| F07 | Issue status tracking | P0 | 1 | **built** | Binary `open` \| `closed` (DD-65); the prior 5-value taxonomy collapsed (non-open → `closed`). Set via a horizontal **segmented Open\|Closed toggle** (one click, replaces the dropdown); closing drops the issue from the active list into a collapsed "Closed (N)" section (not deleted); open-count badge counts `open` only. API + cockpit UI; stamps resolved_at on close |
| F08 | Direct-edit path (inline edit without raising an issue) | P0 | 1 | **built** | Versioned + audited + auto-surfaces in redline (DD-13). Backend: `PATCH /contracts/{id}/nodes/{node_id}` `{text}` → edits `body` (else `heading`), one txn writes node + `node_versions(actor=user)` + audit `node_edited`; no-op skips both; non-prose/derived-only → 422; no renumber (DD-02). **Cockpit edit UI built** — inline textarea via the per-clause ⋮ menu, Save→version (tsc-verified) |
| F08b | New node creation mid-negotiation | P0 | 1 | **built** | Add clause/section on the fly; anchors to a parent; gets derived number; surfaces as tracked insertion in next redline. Backend: `POST /contracts/{id}/nodes` (gap-based order_index + no-gap re-space + `before_node_id` prepend; node_versions insertion row; audit). **Cockpit insert UI built** — ⋮ menu insert above/sub/below, inline new-row editor (tsc-verified) |
| F08d | Donna-assisted clause drafting (new clause from description) | P1 | 1 | **built** | Operator describes what's missing → Donna drafts complete clause language + heading → operator reviews/edits → commits; offered as "Draft with Donna" option alongside blank insert in ⋮ menu; Donna grounds draft in deal type + surrounding clause context (live research is out of v1, as F11 — a needed figure becomes a bracketed placeholder, never invented). **Backend:** `services/donna/drafting.py` (`draft_clause` → grounded structured `{heading, body, citations}` at the capable/high tier, hallucinated-id guard + id-scrub reused from qa.py; **transient** — never persists a node, the operator commits via the F08b create path) + `prompts/clause_draft_v1.txt` + `models/clause_draft.py` + `POST /contracts/{cid}/nodes/draft` (registered). **UI:** "Draft with Donna" panel in the cockpit ⋮ insert editor — describe → draft → pre-fills the insert editor (heading prepended), edit, commit; empty body = honest "couldn't draft" nudge. 9 tests; **eval 4/4** (reuses a defined term not a coined one · bracketed placeholder not a fabricated figure · empty body on a too-vague ask — real Opus). frontend tsc 0; backend gate green. |
| F08c | Free-floating issues (contract-level, no node anchor) | P0 | 1 | **built** | General remarks, structural concerns, points not tied to any existing clause. API (`node_id` nullable) + **cockpit UI built** — raising an issue with no clause selected creates a contract-level issue |
| F08e | Direct-edit — delete clause + sub-tree | P0 | 1 | **built** | Soft-delete a clause and its descendants. Backend: `DELETE /contracts/{id}/nodes/{node_id}` → one txn soft-deletes the subtree + a `node_versions` deletion row per node (actor=user) + audit `node_deleted`; surfaces as a tracked deletion in next redline (DD-13); no renumber (numbers re-derive, DD-02). **Cockpit UI built** — ⋮ menu → Delete with subtree-aware confirm. Backend tests green |
| F08f | Direct-edit — move clause (drag reorder + reparent) | P0 | 1 | **built** | Drag-and-drop **reorder and reparent** (indent/outdent) of a clause, carrying its sub-tree, via a **Rearrange mode** in the cockpit (@dnd-kit, lazy-loaded). Backend: general cycle-safe `POST /contracts/{id}/nodes/{node_id}/move` `{parent_id, after_node_id, before_node_id}` → **order-only** (audit `node_moved`, **no** `node_versions` row); **rejects moving a node into its own sub-tree**; no renumber (numbers re-derive, DD-02); surfaces as a tracked move in next redline (DD-13). **Scope: only the operative clause tree is rearrangeable — front-/back-matter are excluded from drag** (§9). Replaces the earlier up/down-only design. Backend tests green |
| F09 | ~~Issue comment thread~~ | P0 | 1 | **removed (DD-67)** | Comment feature dropped entirely (endpoints, models, `issue_comments` table, UI). Superseded by the **editable issue description** — the operator edits `title` + `our_position`/`their_position` in place (`PATCH /issues/{id}`), so a separate annotation thread is redundant |
| F10 | Donna — single-contract grounded Q&A (Donna tab) | P0 | 2 | **built** | **v1 scope locked (DD-62).** Grounded Q&A over **one contract** (the cockpit Donna tab) — three question shapes: **locate** ("where's the liability cap?"), **explain** ("what does clause 12 say?"), **status-briefing** ("what's still open?", "what did we agree?" — over the issue ledger; the most differentiated-vs-Word capability and v1's headline). **Read-and-explain only:** explains the contract, never advises/drafts/takes a position — "should I accept?" / "is this enforceable?" route to the issue surface (F11) or "get a lawyer" (DD-14 rules 1–2). **Cited to clickable clause nodes** (§7): every answer cites the node(s) it drew from, clickable to jump (reuse F05/F05b). **Retrieval via the F05b conceptual lookup — no embeddings**: finds the clause the operator *means* despite phrasing mismatch. Persistent per-contract thread (`donna_conversations`/`donna_messages`), **windowed context** = last 10 turns + rolling summary (DD-40). **Honest failure:** "I don't see anything in this contract about X" — never fabricates. **LATER (out of v1, named so not silently assumed):** position recommendations / drafting → F11; proactive issue flagging → F28; whole-deal / cross-contract Q&A → later (schema already 1-conversation-per-contract); `node_embeddings` implicit semantic search → F12 (Phase 2, backs this same surface invisibly later). See §9 Donna tab. **Backend BUILT + verified:** `services/donna/` (conversation_repo + windowing DD-40 + grounding [F05b clause retrieval + issue ledger] + `qa.py` structured answer at capable tier) + `prompts/donna_qa_v1.txt` + `POST /donna/ask` + `GET /donna/thread`; 24 tests; **grounding eval 4/4** (cites in-contract · honest not_found · deflects advice · status-briefs the ledger). UI: cockpit **Donna tab** (Issues\|Current Clause\|Donna rail, DD-66; chat with citation chips that jump+flash the cited clause; distinct answer / not_found / deflected treatments; read-and-explain guard line; staged loading + example-question empty state). **Reloaded-thread chips now persist** (`donna_messages.kind` + `citations` JSONB, migration `0004`; assistant turns store their answer kind + citation ids, rehydrated on thread load via the same `resolveCitations` resolver — a reloaded answer renders identical chips + treatment as a fresh ask; pre-migration rows render plain, as expected). |
| F10b | Donna chat — context-aware advise + draft (Brainstorm hand-off) | P1 | 2 | **built** | Context-aware Donna chat. With an active anchor (selected clause and/or open issue, shown as a context chip resolved live per turn) Donna advises (F11 engine) + drafts/revises (F08d engine) grounded+cited to it; with no context she stays F10 read-and-explain and deflects-by-acquiring-context (never "raise an issue/lawyer"); legal-opinion floor absolute either way (DD-14 r2). Manual "Ask Donna about this clause" entry IN v1; adopted language commits via the unchanged F11 apply path. **Brainstorm = stateless ephemeral overlay (DD-73/77):** client holds the transcript, backend persists nothing per turn; F11 card's "Brainstorm ↗" hands off issue+clauses as context primed with the current rec; on close Donna distils ONE summary (`{question, conclusion, fallbacks}`) into `brainstorm_summaries` (FK `issue_id`, one row per pass = history) surfaced in the resolution view, then discards the transcript. DD-69's persistence half is withdrawn; its context-aware-advice half stands. **OUT of v1:** live research (DD-38), F30 extraction from brainstorm turns, multi-clause/whole-deal advisory, Donna-initiated context. DD-14/38/40/68/69/73/77. Minor follow-ups flagged (DEV_TODO). Build detail in git. |
| F11 | Donna — issue-scoped recommendation + live research | P0 | 2 | **built** | Issue-scoped recommendation: auto-generated when the issue detail opens, grounded in clause context + DD-31 resolution, framed on the DD-14 reasonableness spectrum + ask/settle/floor ladder, as ONE object `{cited rationale + recommended landing + counter-language}` cited to clickable clause nodes; reuses Donna v1 infra + F05b grounding (no embeddings). Draft in `donna_recommendations`, never written to the F31 export columns (`issues.recommended_position`/`donna_counter_language`) until the operator confirms via [Use Donna's language] / [Edit] (DD-68 draft-vs-confirmed + edited-confirm). Resolution-view card (auto-on-open, refresh, edit-before-confirm). **OUT of v1:** live research (DD-38 — a needed market figure → recommend the structure + flag the missing benchmark, never invent a number), the Brainstorm overlay, F28 flagging, F12 embeddings. DD-14/31/38/68. Build detail in git. |
| F12 | Tiered context injection | P0 | 2 | planned | Explicit links (DB) + intra-contract semantic search (`node_embeddings`) in Phase 2; negotiation-history search over the issue position/decision ledger + `node_versions` (comments removed, DD-67) in Phase 2+ — DD-06, DD-32 |
| F13 | Negotiation-history RAG (scoped to contested nodes) | P0 | 2 | planned | DD-07 |
| F14 | Contract snapshot (cut on **Mark as sent**) | P0 | 3 | **backend done** | Immutable point-in-time capture of full tree topology + node bodies (OQ-08); cut on the **Mark-as-sent** boundary action (Counterparty / Legal), **not on export** — all exports are now grabs (DD-71, amends DD-61); marking advances the matching DD-48 `shared` pointer. DD-09. Backend: `services/snapshot.py` — cut (full-tree JSONB dump + stamps pending `node_versions.snapshot_id` = the F15 diff group) + read + DD-48 pointer upsert (now also a public `set_pointer` for the one-snapshot/two-pointer "both" case); audit `snapshot_cut`; 12 tests. **Service reused unchanged; the trigger now relocated from the F15b export handler to the new Mark-as-sent handler (`services/mark_sent.py` + `api/mark_sent.py`, DD-71 — SHIPPED).** |
| F15b | Clean copy export — DB→.docx regenerate (no markup) | P0 | 3 | **built** | The renderer (built first); regenerates the current contract from the DB through the style config (DD-43). **Now a pure download** — export is grab-only; the snapshot/pointer/recipient logic moved out to **Mark as sent** (DD-71, amends DD-61). The foundation F15/F31 build on. §9/§12, DD-60/DD-61/DD-71/DD-72. Backend: renderer (`render_docx.py`, round-trip oracle byte-identical on real 413-node contract) **unchanged**; **DD-71 rework SHIPPED** — `services/export/clean_copy.py` is now renderer-only (send/grab branch + `{recipient}` param removed), `POST …/export` takes no body and stamps `contracts.last_export_at` for the DD-72 drift marker; UI: cockpit **Export ▾** has no recipient selector (plain download). |
| F15 | Redline export — tracked-changes .docx | P0 | 3 | **built** | Diff(baseline snapshot, working copy) → Word `<w:ins>`/`<w:del>` (DD-51); **baseline defaults to `last shared with counterparty`** (DD-48), operator-overridable; the baseline only advances on a real send, so intervening Internal/Copy-only grabs never move it (DD-61); **renumber shifts suppressed**; authored by operator org, never Donna (DD-44/F25); **disabled until a snapshot exists** (409 on click → "send a clean copy first" hint, §9/§12). DD-60/DD-61. Backend: `services/export/redline.py` (node_versions change-set vs baseline) + `render_redline.py` (`w:ins`/`w:del`) + `POST /contracts/{id}/redline-export {snapshot_id?}`; UI: Redline item in the Export ▾ menu. **Moves + table insert/delete now marked** via a structural diff (baseline snapshot tree vs live tree): a moved node renders struck-at-old + inserted-at-new, a table ins/del is row-marked, edited+moved reconciled, pure-renumber not flagged. **Remaining limits (flagged):** move uses the del+ins fallback, NOT native Word `w:moveFrom/To` (reviewer sees the relocation; Word won't label it "moved" — couldn't validate the move-range OOXML safely here); a deleted table's rows can't be shown (snapshot tree dump omits `table_data`); deleted clause struck near old slot without its old number |
| F16 | Defined-terms registry (deal-scoped) | P1 | 2 | **built** | Extracted on import; hover-to-define. Backend: `services/defined_terms.py` — deterministic regex extraction (`"Term" means …`, canonical `("Term")` intro), deal-scoped upsert `UNIQUE(deal_id,term)`, precision-over-recall; `POST /contracts/{id}/defined-terms/extract` + `GET …/defined-terms`; 18 tests. **Auto-extracts on import-commit** (failure-isolated, doesn't fail the import) + **cockpit term-hover built** (F05). Follow-up: bare `("Term")` intros store no definition (precision call) |
| F17 | Cross-references as structured links | P1 | 0/3 | **backend done** | Detected on import; rendered dynamically (DD-11). **Detection + storage BUILT** (the Phase-0 half; dynamic cockpit rendering is the Phase-3 half, still planned). Backend: `services/cross_references.py` — deterministic **keyword-introduced** regex (`clause 12.3`, `Section 5`, `Schedule I`, multi `clauses 4 and 5`/`7 to 9`), resolves each designator to a target node id via the shared `_plan` numbering (so a ref resolves to the same number the clause shows everywhere), unresolved (letter/roman schedule refs, forward refs to non-existent clauses) stored with `target_node_id` NULL; idempotent persist (clear-then-reinsert); `GET /contracts/{cid}/cross-references` + `POST …/extract` (registered); **auto-extracts on import-commit** (failure-isolated, like F16). Uses the existing `cross_references` table. **Real-data verified:** sample contract → 96 refs found, 82 resolved / 14 unresolved (sensible — schedule + 1 forward ref). 22 tests; mypy/ruff clean. **Known limits:** refs without a drafting keyword (bare `(see 4.5)`) are missed by design (precision-over-recall); letter/roman designators never resolve in this slice. |
| F18 | Deal parameters + cross-contract consistency flags | P1 | 4 | planned | Shared values defined once; ripple-flagged (DD-12) |
| F19 | Audit log (append-only) | P1 | 1 | **built** | Every mutation; never updated. Service + read API + tests, AND `record_event` wired into all mutation routes (settings creates, import commit, issue lifecycle); `operator_actor` config actor (DD-53). Now logging |
| F20 | Semantic search + knowledge base | P1 | 2+ | planned | pgvector on clause bodies + the issue position/decision ledger (negotiation-history prose; comments removed, DD-67); cross-client pattern queries ("what terms do we typically accept on IP?"); triggered when prose volume exceeds ~100K tokens |
| F21 | Contract version diff (between snapshots) | P1 | 3 | planned | "What changed in §12 between v2 and v3?" |
| F22 | Principal read-only + issue-decision portal | P1 | v1.1 | planned | Built ready; shown when chosen |
| F23 | Granola / transcript ingest → auto-suggest issues | P2 | v2 | backlog | Live typing is enough for v1. **Held off (operator call, 2026-06-23):** operator types fast enough; live STT adds latency + correction overhead + mis-transcription risk → *net-negative* vs typing for a fast typist (shifts effort, doesn't reduce it). Revisit only if that calculus changes (slower typist / better real-time STT). When built: the issue engine with a transcript input + `initiator=donna` + operator-confirm — not new machinery (collapse) |
| F24 | Style-config UI editor (per-contract override panel) | P2 | v2 | backlog | Import-time style detection + accept/adjust is covered under F04 (Phase 0). Dedicated per-contract override panel deferred to v2. |
| F25 | Operator organization identity (Settings → Your Organization) | P0 | 3 | **built** | Configured org name (config value, not a DB entity); used as redline / export author; never "Donna" (DD-44). Backend: `config/settings.py` `operator_org_name` (`DONNA_OPERATOR_ORG_NAME`) → validator wires `redline_author` so the export author always resolves to the org name (or a neutral default), never "Donna"; `GET /organization`; 8 tests. UI: Settings → **Your Organization** (read-only — set via env per DD-44). §9 line ~461 calls it "read/edit"; in-app editing would need a settings-store follow-up (flagged) |
| F26 | External revision sources — legal team / internal review (rides Mode B engine) | P1 | 2 | planned | `revision_session.source`; Donna moderates legal over-reach (DD-47); + `needs_legal_review` issue flag + legal review packet export |
| F27 | Version pointers + lineage view (where-are-we tracking) | P1 | 3 | **built** (badge + lineage view, engineering live-verified); received-pointer data (Mode B) + F21 diff still planned | Where-are-we tracking. Persistent lifecycle badge in the cockpit header AND every My-Contracts card (colour-keyed taxonomy `Working copy · Sent to counterparty · Sent to legal · Sent to counterparty & legal · Your move · Signed` + a passive "edited since sent" marker; FIRST-MATCH-WINS derivation tracking the last boundary event with the other side, NOT local editing) → click-badge → version-history drawer (working copy pinned at top + v1…vN timeline newest-first/descending + 2 greyed `received` slots; read-only open of any snapshot; ordering operator-accepted 2026-06-29). 4 named snapshot pointers (DD-48); monotonic v-numbers over snapshots, working copy never numbered/locked (persisted `version_number` for gap-preservation on version-delete). Boundary action = **Mark as sent → Counterparty / Legal / Both** — the deliberate step that cuts the snapshot (current working copy), advances `last_shared_with_X` (one snapshot may carry both pointers), and mints the next v-number, with a non-blocking drift warning if edited since the last export; export is grab-only and no longer sets pointers. See §12; DD-48/70/71/72/75/87. **Still planned:** the 2 `received` pointer states (Mode B / F03b populates them) + the F21 version diff (diff/restore out of v1). Build detail in git + DEV_TODO. |
| F33 | Delete/wipe a contract version (cockpit) | P1 | 3 | **built** (operator-accepted 2026-06-29) | Hard-delete any lineage version from the Version-history drawer. **Latest-delete** rolls the working copy back to the prior version's content + re-mints the number; **middle-delete** removes only that version, working copy untouched, **gap preserved** (v1, v3, v4); **only-version** → back to the never-sent "Working copy" state. Non-blocking warnings (destructive rollback discards unsent edits; deleting a sent version erases the sent-record + rolls the redline baseline back, naming party + date). **Numbering swapped derived→persisted `version_number`** so gaps survive (migration `0012`; amends DD-70/DD-75). Backend `services/version_delete.py` + `api/version_delete.py` (`DELETE …/snapshots/{id}?confirm=`, preview→execute) + audit `version_deleted`; FK-correct cascade extends DD-63. Verified: 37 targeted tests + system rollback oracle, mypy/ruff clean. DD-85, DD-87 |
| F28 | First-pass auto-issue detection on import | P1 | 2+ | planned | On import, Donna drafts a ranked issue list (red flags, below-market terms, missing provisions, placeholders, missing exhibits, broken cross-refs) grounded in the F29 knowledge layer + deal `position`. Rides the issue engine (`initiator: donna`); **operator-confirmed, never authoritative, never auto-exported** (correctness, §2.4 — F1 ~0.62). Source-parameterized ranking (DD-50). Sequenced **after** the bulk-surface mechanism (DD-47) so the list is ranked, not a flood. Keep/dismiss logged via F03d/DD-29 from day one. DD-50 |
| F29 | Knowledge layer — market benchmarks + risk taxonomy (reference data) | P1 | 2 | planned | Curated, static seed data: CUAD risk taxonomy (whole) + market-benchmark table + red-flag taxonomy + per-type checklists (Licence / Offtake / JV built fresh, NDA ported; attach to F01b contract types). Derived from CUAD/public sources — **not** a live legal database. Grounds F28 and turns many F11 live-research calls into local lookups. DD-49 |
| F30 | Negotiation insight distillation | P1 | 2+ | **built** (DD-76) | **Trigger re-anchored to ISSUE-CLOSE (Lilly-approved 2026-06-25; not "brainstorm close", which has no built lifecycle) — distil from the committed issue ledger, never the transcript.** On issue-close (F07), a failure-isolated background task (`distill_on_issue_close` via `BackgroundTasks`, own conn, swallows errors — never fails the close) runs a medium-tier (Sonnet) extraction over the committed ledger → 0–N `negotiation_patterns` (operator-global, `subject_ref` derived from contract context never the model; empty = honest no-pattern). **Merge-first** (reinforce a real id / insert on hallucinated / flag contradiction) over the tiny pre-filtered candidate set (LLM-judge, no embeddings); **consolidation** (prune unreinforced-past-TTL + collapse dupes, deterministic). **Retrieval (tier 8):** patterns inject into F11's issue grounding as a clearly-labelled, non-authoritative, never-exported input (§2.4). Backend: `services/donna/distillation.py` (extraction + merge + consolidation + retrieval) + `models/insights.py` + `prompts/distill_v1.txt` + `negotiation_patterns` table (migration `0006`) + the issue-close hook in `api/issues.py` + the F11 grounding hook in `recommendations.py` + config knobs. **14 tests** (parse/subject-ref/merge-first apply: reinforce-real/insert-on-hallucinated/flag-contradiction/skip-empty + the close-schedules-distil / reopen-does-not route tests); full gate **497 tests green**, mypy/ruff clean. **Follow-up (minor):** a real-Sonnet extraction-quality eval is authored-pending (the merge/store/hook risk is unit/integration-tested; pattern quality is low-consequence — non-authoritative retrieval input, never exported). Engineering ADRs settled: medium/Sonnet tier; LLM-judge merge over the small set (no embeddings). After each issue closes, Donna runs an extraction pass and synthesizes 0–N compact pattern records — not the raw conversation. Merge-first: checks existing patterns before creating; updates evidence count + refines wording on a match. Consolidation pass triggered after deal close or N new patterns added: redundant patterns merged, low-confidence patterns past TTL (3 deals unreinforced) pruned, contradictions surfaced as flags not silent overwrites. Patterns retrieved selectively alongside tiered context when Donna opens an issue. Converges to ~100–200 records across all subjects; never grows unbounded. DD-55 |
| F31 | Issue-list export (.docx) | P1 | 3 | **built** | Unresolved issues (`status='open'` only, DD-65) → constructive .docx table for principal briefing / counterparty walkthrough / operator record. Columns #/Clause/Issue/Status/Raised by/Our position/Their position/Proposed resolution; **`#` = 1..n render sequence (not raw `priority` — that drives the sort but isn't printed; raw priority is an internal triage number)**; priority-desc (ties → document order), free-floating last; no internal fields / Donna attribution / IDs. Rides the F15b renderer. §9/§12, DD-60. Backend: `services/export/issue_export.py` + `GET /contracts/{id}/issue-list/export`; UI: Issue-list item in the Export ▾ menu |
| F32 | Firm profile — Donna's evolving understanding of the firm | P1 | v1 (seed) + 2+ (evolve) | **v1 built** (operator-accepted 2026-06-28); evolve half 2+/v2 | Operator-seeded standing profile of the firm — what it does, its commercial interests, standing priorities/risk posture — with **two surfaces, one primitive** (collapses the two requested capabilities: cross-contract intelligence + a firm-understanding summary). **Reasoning surface:** an always-on layer injected into Donna's recommendations (F11) + Q&A (F10) so her output reflects the firm's identity across contracts — delivers cross-contract intelligence via an *abstracted profile*, not raw cross-client retrieval (that stays the v2 tier; the profile is the cheaper, safer 80/20). **Control surface:** an operator-viewable/editable document (Settings). **Trust model (DD-80, the behavioral contract):** operator-seeded; **mode toggle Fixed** (operator-authored, Donna never writes) **\| Donna-editable** (Donna auto-updates as understanding evolves — **auto-apply + visible "what changed & why" log + one-tap revert**, Lilly's pick; operator edits/override always win). **Traceability:** every Donna-authored assertion cites the source contracts/issues that taught it (auditable — operator sees *why* before trusting/deleting). Updates ride the **issue-close** signal (same trigger as F30) + import; substrate logged by F03d/DD-29 + F30 (logging-precedes-learning satisfied). **Distinct from F30** (tactical *how-we-negotiate* patterns) **and F29** (static external benchmarks) — F32 is *who-we-are*. **v1 PULL-FORWARD (DD-90, Lilly 2026-06-28):** the operator-seeded ("Fixed") half ships **now** as a **free-text editable document** (Settings — who the firm is, commercial interests, standing positions/red-lines in prose), injected into Donna's recommendation grounding — the **F03c counterparty-revision reviewer (F34) first**, F10/F11 when built. Only the **Donna-editable evolving** half (auto-updates from corpus) stays Phase 2+/v2 (needs corpus volume). v1 shape = free-text document (structured red-line fields deferred); v1 is Fixed-mode only. DD-80, DD-90 |
| F34 | Consistent recommendations across the contract (cluster-and-judge-once) | P1 | 2+ | **built** (operator-accepted 2026-06-28) | Donna's counterparty-revision recommendations are consistent document-wide. **Problem (as-built):** each hunk is an independent temp-1.0 LLM call (`recommend_session` → `_analyze_hunk` per hunk) with no shared state, so the SAME counterparty edit in N clauses can get N different verdicts/counters — Donna can contradict herself in one contract. **v1 = "A" (same edit, one verdict):** identical/related changes (same figure, same defined-term rename, same original→proposed edit) are **clustered and judged ONCE** — one verdict + counter-language applies to every occurrence; eliminates the contradiction structurally (independent of sampling temperature). **Review surface:** a cluster collapses into a **single grouped stop** ("this change appears in N clauses") — operator decides once (accept/reject/counter), applies to all, **expandable to peel off + override an individual clause**. **Deferred fast-follow "B" (named, not built):** coherence sweep flagging *untouched* stale occurrences of a changed term/figure elsewhere (defined-term/figure drift) — Donna proposing edits beyond the counterparty redline; whole-document read, higher hallucination surface. Engineering owns cluster-detection, judge-once placement, grouped-stop propagation. Builds on DD-64/DD-83. DD-89 |
| F35 | Clickable, renumber-safe clause references in Donna's recommendations | P1 | 2+ | **built** (operator-accepted 2026-06-29) | Donna's revision recommendations reference clauses by **stable `node_id`**, rendered as **inline clickable links labeled with the LIVE projected number** (DD-88) that jump to the clause. **Problem (as-built):** her rationale emits **baseline** prose numbers with no anchor — so a reference can mismatch the projected pane *before any decision*, goes stale when accept/reject renumbers, can't disambiguate a baseline clause from a counterparty-proposed insertion of the same number, and isn't clickable. **Fix:** recommendation output carries inline node-id clause anchors (never bare numbers); frontend renders each as an inline clickable link, label resolved live from the current projected tree, `jumpTo`/`scrollIntoView` on click — **reusing the F10/F11 citation pattern + the review pane's existing scroll-by-node_id primitive.** Prompt: Donna cites clauses via the anchor only. Grounding supplies node_ids (+ projected numbers) of referenceable clauses (focal + DD-31 cross-refs in v1). Builds on DD-88/DD-31/DD-83; reuses F10/F11 citations. DD-92 |
| F36 | Contract-grounded recommendations — reference-graph grounding | P1 | 2+ | **built** (2026-06-29; ships with F32 v1) | Per clause, inject resolved defined-term **definitions** + cross-ref target bodies into Donna's revision-recommendation grounding — a deterministic graph walk over the populated F16/F17 data (no embeddings). Depth-1 (no recursion into definitions), capped (≤8 defs / ≤6 refs), **resolved once per F34 cluster**. Two guards from the validation spike — a word-boundary gate (allow trailing `s`) + a short-acronym guard (≤3-char terms only when longest-match consumed the full registered head) — kill the acronym mis-maps (the spike found 68 "bare-acronym"→generic-definition errors in one contract). Feeds the terse "B" recommendation surface (DD-93). Co-lands with F32 v1. Deal-params tier dropped (unpopulated). No schema change. DD-93 |
| F37 | Deal brief — Donna's distilled understanding of the deal | P1 | 2+ | **built** (operator-accepted 2026-06-29) | The **per-deal global context** tier (alongside F32 = who *we* are, F30 = cross-deal patterns) — what *this* partnership is, so Donna reasons like counsel holding the whole deal in mind, not clause-by-clause. **Donna distills it once at import** (one whole-contract read; **Opus/high tier**, background job): parties + roles, each party's business/interests, the partnership's economic spine, key commercial terms + how they interrelate, the deal's purpose. **Operator-reviewable/editable** (mirrors F32): auto-seeded, operator edits win, regenerates on re-import + a manual **Refresh**. **Injected into the `{deal_context}` slot** of Donna's grounding for recommendations + chat + brainstorm (DD-93 pipeline). **Grounded (validated by spike):** cite-or-flag, says "not stated in the contract" rather than invent, no outside knowledge, inferences marked — a wrong brief grounds every recommendation, so the grounding discipline + operator-edit are the guardrails (§2.4 behavioral contract). **v1 = this-contract-only** (the brief honestly flags economics living in sibling offtake/JV docs it can't see; whole-transaction-set deferred). Eng guardrails: max_tokens ~6–8k, longer background-job timeout. DD-95 |
| F38 | Generate redlines — full-contract first-pass redline on new import | P1 | 2+ | **scoped** (v1 MUST/LATER locked, DD-97; not built) | Operator-triggered **"Generate redlines" button** in the cockpit (Mode-A first imports only) → Donna drafts recommended counter-language on **every operative clause**, riding the **DD-93 recommendation engine** (F32 + F37 + F36 + F34 + F35 + F30) — F38 is that engine with the input unit set to *every clause* rather than a counterparty change cluster (F03c) or an opened issue (F11), not new machinery. Opening stance **parameterized by `contracts.origin`** (us → light/defend · our_legal → balance + moderate over-reach (DD-47) · counterparty → full scrutiny; DD-84). Each clause card carries a **materiality signal** (would-accept / minor / substantive, reusing the F34 significance split) so the operator triages, not wades. Rides the existing recommendation-card surface (DD-93/F11) + F35 citations; all F11 guardrails hold — cite-or-flag, never invents a figure (bracketed placeholder), "get a lawyer" on enforceability, never auto-applied, never auto-exported, never auto-seeds issues (§2.4 / DD-14 / DD-50). **Distinct from F28** (triage: flag problems) — F38 is position-setting (draft on every clause). **Operator-triggered, never auto-at-import** (DD-96). **v1 boundary + open taste calls (stance calibration · card-stream vs dedicated whole-doc view · F11 coexistence) in DD-97 + PM_TODO.** Prereq MET: `contracts.origin` captured at import (F04, built; DD-84). DD-96/DD-97 |
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

**contracts** — one agreement. Fields: id, client_id, deal_id, contract_type_id (FK to contract_types), name, status (drafting / under negotiation / signed), current version label, style_template_id (nullable FK — inherits template config), **style_config** (JSONB — per-contract overrides on top of template, or standalone config if no template), **origin** (nullable `TEXT` CHECK `us | our_legal | counterparty` — who drafted the baseline at first upload; sets Donna's *starting* redline posture for F28/F38, the version-zero analog of the per-revision `revision_session.source`, DD-84), created_at.

**contract_snapshots** — immutable point-in-time capture of all node states (topology + bodies, per OQ-08), like a git commit. Cut on **Mark as sent** (the boundary action — Counterparty / Legal; **not on export**, which is now a pure download — DD-71, amends DD-61), and on import to capture an external revision's as-received state (DD-48). Drives redline diffs and the version pointers. Fields: contract_id, label, created_at, origin (`export` | `as_received` | `manual`).

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
- **Insight distillation** (F30) — on **issue-close** (F07; not "brainstorm close", which has no built lifecycle — DD-76), Donna runs an extraction pass over the **committed issue ledger** (positions/decision + clause text, **never the raw transcript** — DD-76) and synthesizes 0–N compact pattern records into `negotiation_patterns`. Only extracted principles persist (DD-42/DD-55/DD-76). **(Two distinct on-close outputs: these abstract cross-deal *patterns* for Donna's own grounding, and — separately — one readable per-issue *summary* stored on the issue for the operator's continuity, DD-73.)** Patterns cover four types: operator negotiating style, counterparty behavioral tendencies, deal-type norms, legal-team tendencies. Merge-first: Donna checks existing patterns before creating — a matching pattern gets its evidence count incremented and wording refined; only genuinely novel insights create new records. Consolidation (triggered on deal close or after 5 new patterns): redundant patterns merged, low-confidence patterns unreinforced across 3 deals pruned, contradictions surfaced as flags not silent overwrites. The store converges to ~100–200 compact records — never grows unbounded.

### Donna's recommendation engine (the DD-93 pipeline)

The F-rows that produce Donna's recommendations are **one engine, not six independent features** — read them together. The recommendation **surface** is a deliberately terse card (verdict + grounded rationale + drafted counter + clickable citations, DD-93 "B" over a structured memo); the counsel-grade reasoning lives in the **grounding pipeline** the model reasons over:

| Limb | F-row | What it contributes to a recommendation |
|---|---|---|
| Firm mandate | **F32** (DD-90) | who *we* are — standing interests, positions, red-lines |
| Deal brief | **F37** (DD-95) | what *this* deal is — parties, economic spine, purpose (one whole-contract read at import) |
| Reference graph | **F36** (DD-93) | the focal clause's resolved defined-term definitions + cross-ref bodies (deterministic walk, depth-1, capped) |
| Consistency | **F34** (DD-89) | the same change judged once across the document (cluster-and-judge-once) |
| Citations | **F35** (DD-92) | node-id-anchored, inline-clickable, live-numbered clause references |
| Precedent | **F30** (DD-76) | cross-deal negotiation patterns (non-authoritative, never exported) |

Consumers of the engine: the **counterparty-revision reviewer** (F03c, the first consumer — ships with F32 v1), the **issue-scoped assistant** (F11), and **F38 "Generate redlines"** (the full-contract first-pass over a fresh Mode-A import). All share the grounding pipeline and the card surface; they differ only in the *input unit* (a counterparty change cluster · an opened issue · every clause). Canonical record: **DD-93**.

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

9. **Firm profile layer** (Phase 2+/v2, F32) — always-on alongside tiers 1–8; the firm's standing identity/interests/priorities (DD-80), injected so recommendations reflect *who the firm is* across contracts. Operator-controlled (Fixed | Donna-editable), every Donna-authored line cited to its source contracts/issues, never authoritative/never exported (§2.4 — same posture as tier 8). Compact — a profile, not a corpus dump. Distinct from tier 8 (tactical patterns) and F29 (static external benchmarks).

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

All design decision records (DD-01 … DD-97) live in **[`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md)** — see its index for the full list. Inline `DD-NN` references throughout this spec resolve there.

---

## 9. UI Design

The locked UI design lives in **[`UI_DESIGN.md`](UI_DESIGN.md)** — navigation model, home / contracts / settings screens, the four-step import flow, the negotiation cockpit (layers + tabs), the Donna tab, the issue resolution view, the counterparty-revision two-pane review, and the `Export ▾` / `Mark as sent ▾` affordances. (Split out of SPEC in the 2026-06-26 librarian pass, following the §8→`DESIGN_DECISIONS.md` precedent.)

Export/Mark-as-sent **pipeline mechanics** (what cuts a snapshot, pointer advancement, redline/issue-list generation, drift) live in §12; UI_DESIGN holds only the affordance. UI design-decision records remain `DD-NN` entries in `DESIGN_DECISIONS.md` (DD-19 onward originated as UI records).

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
   - **Path B** (clean edited copy, no tracked changes): the matcher (DD-64 — LLM judges identity only; the diff is always a deterministic text diff) sorts every clause into **four buckets**: **matched-with-edits** (confident pair → deterministic text-diff → hunks), **new** (unmatched incoming node = counterparty added), **deleted** (unmatched baseline node = counterparty removed), and **abstain** (a pair below the confidence/margin gate the matcher deliberately punts on rather than risk a wrong auto-match). The abstain bucket is **not** auto-classified — it is resolved by the operator in the structural-foundation step (6b, DD-78) before content review; resolving each abstain reclassifies it into matched / new / deleted.
4. **Create `counterparty_revision_session`** — records the import event, parse path used, baseline snapshot, and total change count. **Rewrite-scale escape hatch (DD-78):** when the change set is effectively a rewrite (matched-with-edits + new + abstain together span the majority of the tree, or abstains alone blow past the matcher's gate), Donna surfaces a non-blocking banner offering **re-import as a new baseline (Mode A)** instead — abandon the diff rather than make the operator confirm hundreds of matches. Past that line, diffing is the wrong tool, so Donna offers the right one; never a hard block.
5. **Populate staging tables (classification inherited, not re-derived — DD-28/DD-54/DD-82)** — a Mode-B revision is a *delta* on a known baseline, so each matched revised node **inherits its baseline node's role** (clause / recital / drafting-note / appendix / front-matter); only genuinely-new nodes are classified (default `clause`). Donna never re-classifies the whole tree from scratch (that would only introduce drift). Then, for each node with at least one counterparty edit, create a `counterparty_revision_changes` row (navigation unit). Within it, create one `counterparty_revision_hunks` row per individual text edit (decision unit). **A new node (counterparty added) and a deleted node (counterparty removed) each stage as one change row carrying a single whole-node decision** (node-added / node-deleted) — so all three reviewable types (edit / added / deleted) flow through the same change row + decision-unit model (DD-78; schema extension for the two whole-node decision kinds is engineering's). Donna pre-classifies each decision's significance (`trivial` | `substantive`) by semantic impact on the clause — never by edit size — for added and deleted nodes as well as edits. Trivial decisions are pre-recommended Accept with no counter drafted. Substantive ones get full analysis + exact counter-language. **No issues are created at this stage** — the staging tables are the review workspace, not the issue list.
6. **Structural foundation** (before content review, DD-30 + DD-78) — two ordered sub-steps that settle the *skeleton and the clause-correspondence* before any content is judged. Both share one mental model — *the operator confirms Donna's structural read* — but are separate sub-screens because their action vocabularies differ; never folded into one widget. The whole step is skippable if neither sub-step has residual cases.
   - **6a. Tree-shape triage** — the numbering-inference pass (step 2a) has already auto-corrected most hierarchy errors silently. This surfaces the residual cases it could not resolve: nodes with no recognisable numbering prefix at the wrong depth, headings detected as body text, depth anomalies relative to numbered siblings. Same level-adjustment tools as Mode A (multi-select, ± level, heading/body toggle).
   - **6b. Match confirmation** (the abstain bucket, DD-64/DD-78) — Donna surfaces a **ranked queue** of abstained pairs (most-uncertain first), each card showing the incoming clause beside Donna's best-guess baseline counterpart, the match confidence, and her one-line reasoning ("heading similar, body substantially rewritten"). Per card: **[Confirm match]** (→ becomes a matched pair → its hunks generate → enters content review) · **[Not a match → it's new]** (→ incoming node to the *new* bucket; if its guessed counterpart is left unmatched it drops to *deleted*) · **[Match to a different clause]** (manual pick from baseline). Resolving every abstain reclassifies it into matched / new / deleted, so content review begins on a fully-classified change set. **Bulk affordance** (DD-34 pattern): the queue offers "confirm all remaining high-confidence" / "treat rest as new" so the long tail collapses to one click and load scales sub-linearly with diff size. Abstains must be settled here, up front — a content-stream item has no diff to show until its pair is confirmed, and resolving one abstain can reclassify another node, which would retroactively churn the stream mid-flight.
7. **Donna pre-populates at import (auto-run, DD-82)** — verdict + one-line reasoning + exact counter-language for every substantive hunk generate in a **background pass at import time** (not on an explicit operator trigger), so the review UI opens with "Use Donna's" already populated on every change. The pass is **failure-isolated** (a recommendation failure never blocks import or review) and **cost-guarded** (substantive hunks only; trivial decisions pre-recommended Accept with no draft). Writes the advisory columns only, never `final_text` — the redline stays the deterministic diff, the counter is operator-adopted (DD-64).
8. **Counterparty change review UI — two-pane document view (DD-81).** By now (6b done) every change is classified. The surface is **two panes**, not a card-stream: **Left rail = navigator + to-do tracker** — one row per change in document order, anchor label = clause number else role-based fallback ("Appendix title"/"Draft note"/"Recital"/"New clause"), Added/Deleted/Modified tags, decided-state tick/strike, a prominent "N pending". **Main area = the document in reading order** — changed clauses neutral-highlighted (kind colour lives only on the rail tags, since a change may be two words inside an otherwise-intact clause), top-to-bottom mirroring how the operator reads the contract and preserving the narrative of what the counterparty did (a deletion at clause 4 sitting next to an edit at clause 5). Click a changed clause → **inline expand** to its review controls:
   - **Matched-with-edits — single inline embedded redline of the FULL clause (DD-81).** The complete clause text is shown once, read in place like Word: **insertions marked (green/underline), deletions as red strikethrough** — not a baseline/revised/per-hunk three-pane split, and not a baseline-spine or revised-text reconstruction. **Decision granularity is per-change (Word-style):** when one clause carries several edits, each change is its own row ("Change N") with its own action set — **Accept theirs** / **Use Donna's counter** / **Edit Donna's counter** / **Keep original** — never one decision for the whole clause. Donna's verdict + counter-language are **already populated** (auto-run at import, step 7), so "Use Donna's" is live on open.
   - **New (counterparty added)** — the added clause rendered as an insertion, Donna's significance read + optional counter as a single whole-node decision: **Accept addition** / **Use Donna's counter** / **Edit** / **Reject** (rejecting reinstates our version → tracked deletion in the next redline, and the rejection seeds an issue per step 9).
   - **Deleted (counterparty removed)** — the removed clause rendered as a deletion, Donna's read, single whole-node decision: **Accept deletion** / **Reject** (reinstate → tracked insertion) / **Edit** (reinstate a modified form). A *new* node already carries its position in the incoming tree, so there is no separate "placement" action — the decision is purely accept/reject the counterparty's move.
   - **Brainstorm escalation** reuses the existing ephemeral overlay, seeded per-change.
9. **Log decisions and create outcomes** — every decision (edit, added, or deleted) written to its change/decision record. For **accepted or modified** decisions: applied directly (edit → node body; accept-addition → keep node; accept-deletion → soft-delete node); no issue created. For **rejected or deferred** decisions (incl. a rejected addition or a reinstated deletion): an issue is created with `initiator: counterparty`, `category: counterparty_proposed_edit`, `their_position` = proposed text/change, `counterparty_revision_session_id` set. Decision also written to `issues.decision` JSONB with verdict, final language, Donna's archived recommendation, and optional operator reasoning (DD-25, DD-29).
10. **Unresolved changes** — any hunk not actioned during the session remains in the staging table as `verdict: pending`. Review can be paused and resumed at any time. On resumption: the changes navigation list restores exactly where the operator left off — decided hunks show their outcome (✓ accepted / ✗ rejected / ✎ modified), pending hunks show as blank. Donna's pre-populated counter-language persists. The operator picks up from the first pending hunk. Pending hunks on final session close (operator explicitly ends the review) surface as open issues in the cockpit.

---

## 12. Export & Mark-as-sent Pipeline (F14 / F15b / F15 / F31 / F27)

The canonical export-pipeline mechanics. The operator-facing **UI affordances** (the `Export ▾` / `Mark as sent ▾` menus, button placement) live in [`UI_DESIGN.md`](UI_DESIGN.md) → *Export & Mark-as-sent affordances*. Decision rationale lives in DD-60 (export surface), DD-61/DD-71 (grab-vs-send → Mark-as-sent), DD-72 (drift marker), DD-48/DD-70/DD-75 (pointers + lineage).

**Export is pure file generation — every export is a "grab" (DD-71).** No snapshot, no pointer, no badge change, no lineage effect. All three exports render through the per-contract style config so styling is never decided at export time, and each stamps `contracts.last_export_at` for the DD-72 drift probe.

**Clean copy (.docx) — F15b** (the DB→.docx renderer; the foundation build):
1. **Regenerate** the full .docx from the DB through the style config (numbering, fonts, emphasis rules). Safe because the data layer is content-complete — there is no hand-edited Word file to clobber (DD-43).
2. **Integrity check** — verify content (wording, numbering, tables, special chars) is preserved; only styling may be normalized to the house style.

**Redline (.docx) — F15** (builds on F15b's renderer): tracked changes from the diff between the chosen baseline snapshot (default `last shared with counterparty`, DD-48) and the current working copy (DD-03). Content and structure changes are shown — moves as tracked moves (DD-13); **pure renumber shifts are suppressed**. Emitted as Word `<w:ins>`/`<w:del>` (DD-51), each authored by the operator's configured organization, **never Donna** (DD-44, F25). **Requires a baseline — disabled until the first Mark-as-sent cuts a snapshot** (DD-71); round 1 = clean copy + mark sent, every round after = redline + mark sent.

**Issue list (.docx) — F31:** a standalone table render (no snapshot, no contract body) of unresolved issues (`status='open'` only, DD-65). Constructive, counterparty-safe language only — **no internal fields** (Donna reasoning, authority / `needs_legal_review` flags, session IDs), **no Donna attribution, no DB IDs.** Order: priority descending, ties broken by document order; free-floating issues (no clause anchor) grouped last under a separator row. One counterparty-safe artifact serves principal briefing, counterparty walkthrough, and operator record alike — CSV, two-variant, and full-status exports were all dropped from v1 (DD-60).

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

**Mark as sent — F27/DD-71 (the boundary event).** Decoupled from export because donna.ai can't actually send. A deliberate `Mark as sent → Counterparty | Legal` action (cockpit AND each My Contracts card) that: **cuts an immutable snapshot of the current working copy** (full tree topology + node bodies — DD-09 storage-model addendum: a self-contained JSONB tree dump per snapshot, not a delta), stamps the pending `node_versions` edits group under it (closing this round's change set, which is what the next redline diffs), advances that party's `last_shared_with_X` `shared` pointer (the diff baseline for their next inbound revision), flips the lifecycle badge to `Sent to …`, and mints the next version number in the lineage (DD-70/DD-75). One snapshot may carry both pointers (DD-48). **Drift guard (non-blocking, DD-72):** if edited since the last export, a one-click-through heads-up (*Mark anyway / Re-export*) — never a gate; marks silently otherwise.

**Snapshots & baselines (DD-61, amended by DD-71 — canonical here).** A snapshot is cut **only on Mark-as-sent** (Counterparty / Legal), never on export. Export sets no pointers; the `shared` pointers advance only on Mark-as-sent; the `received` pointers are set **solely by the Phase-2 revision-import path**, pointing at an immutable as-received snapshot captured before edits (DD-48) — the export/mark path never touches them. A `shared` pointer can be moved retroactively from the snapshot-history view if a recipient was set wrong. Mode B counterparty-revision import diffs against the `last shared with counterparty` pointer.

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
