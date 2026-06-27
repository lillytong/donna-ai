# donna.ai — UI Design

> Locked UI design for [`SPEC.md`](SPEC.md) (extracted from SPEC §9 in the 2026-06-26 librarian split, following the §8→`DESIGN_DECISIONS.md` precedent). SPEC §9 is now a pointer here.
> Everything below is locked design, not proposal. Design-decision records (`DD-NN`) live in [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md); export-pipeline *mechanics* (snapshots, pointers, Mark-as-sent, redline/issue-list generation) live in **SPEC §12** — this file holds the operator-facing **affordances** only.

---

## Navigation model

- **Routes:** `/` Home · `/import` Import flow (Mode A) · `/contracts` Contracts ("My Contracts") · `/contracts/{id}` Negotiation cockpit · `/settings` Settings.
- **Persistent top bar on every screen:** donna.ai logo top-left → Home (`/`); top-right links **Import · Contracts · Settings** (the three site-wide destinations). **Export is NOT in global nav** — export and Mark as sent are per-contract actions inside the cockpit (DD-71); you export the contract you're already in, so a global "Export" would be the wrong altitude.
- Logo → Home from anywhere.

## Home screen

The default landing (`/`). Answers one question: **"what do I pick up?"** — not a stats dashboard, a launcher into the right contract.

- Shows the operator's **most-recently-touched contracts as resume cards**; click a card → that contract's cockpit.
- **"Your move" cards float to the top, accented** (amber) — the page surfaces what needs the operator first.
- **Each card:** a **status badge** (the where-are-we taxonomy, F27/DD-75: `Your move` / `Working copy` / `Sent to counterparty` / `Sent to legal` / `Sent to counterparty & legal` / `Signed` — color-coded: amber = your move, green = signed, neutral = working copy, muted = waiting; tracks the last boundary event with the other side, not local edits — DD-70) · an **"edited since sent" marker** when the working copy has diverged from the last shared snapshot (passive, non-blocking — DD-70) · contract name + client·deal · **open-issue count (red when > 1, else grey)** · last activity.
- **Ordering / recency:** most-recently-touched first; recency derivable from the audit log (latest event per contract) or `MAX(updated_at)` across its rows.
- **Empty / first-run:** a prominent "Import your first contract" CTA.
- **Phasing:** the Phase-1 home shows `Working copy` + open-issue count + recency; the richer `Sent` / `Your move` states light up as snapshots (Phase 3) + revision import (Phase 2) land (F27). More cards (e.g. Donna Q&A) are added as Phase-2+ surfaces ship.

## Contracts screen ("My Contracts") — client → deal → contract browser

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
- Per-contract **lifecycle badge** + **Export** + **Mark as sent** controls (F27/DD-71) — see *Export & Mark-as-sent affordances* below.
- **No "imported/committed" filter on the picker (decided).** The picker lists *all* contracts; one with no committed node tree opens to a clear "No clauses yet — import first" empty state rather than being hidden. For the target user (2–5 agreements) a hidden-by-flag picker adds a concept and a "where did my contract go?" failure mode for no benefit; the empty state is self-explanatory and doubles as the next-action prompt.

## Settings

Reached via the **Settings** link in the persistent top-right nav (see Navigation model). Sub-sections:

**Settings → Clients**
Table of all clients. Add / edit / archive. Fields: name, relationship type, notes. Archiving hides the client from the home screen but preserves all data.

**Settings → Contract Types**
User-configurable list. Pre-seeded defaults (Licence Agreement, Offtake Agreement, JV Agreement, NDA, Amendment). Add custom types. Reorder. Cannot delete a type that's in use — archive instead.

**Settings → Style Templates**
Named formatting configs reusable across contracts. Fields: template name, full style config (font, numbering scheme per depth, heading styles, indentation, page breaks). Each template has a live preview pane showing a sample clause rendered with the current settings. A contract inherits from a template; per-contract overrides are applied on top. "Set as default" applies this template automatically to all new contracts unless overridden at import. (F01c is deferred to v2 — see §5; the schema is in place.)

**Settings → Deals**
Manage deals per client. A deal can also be created inline during the import flow.

**Settings → Your Organization**
The operator's own organization identity (single value, single-operator v1). Used as the author on every generated redline / tracked change and as author metadata on regenerated .docx — never "Donna" (DD-44, F25). Stored in `config/`; surfaced here as a read/edit field. (Set via env per DD-44; in-app editing needs a settings-store follow-up — flagged on F25.)

## Import flow — four steps

**Step 1 — Context (new)**
Select client (dropdown — existing clients or "Create new client"). Select deal (dropdown scoped to that client — existing deals or "Create new deal"). Enter contract name. Select contract type. Upload .docx. This step is where donna.ai knows where to store the contract before parsing begins.

**Deal `position` is required when creating a deal inline (decided).** When the operator picks "Create new deal," the inline form must capture `deals.position` (customer / vendor / buyer / seller / licensor / licensee / receiving_party / disclosing_party — DD-50); selecting an *existing* deal does not re-ask (it already carries one). Rationale: position is a once-per-deal decision that governs what Donna flags as unfavorable (F28 source-stance ranking). It **cannot be reliably inferred from contract type** — a "Licence Agreement" could be licensor or licensee, an "Offtake" buyer or seller — so defaulting from type would plant a *confidently wrong* value, which is worse than null. A deal that lands `position=null` silently disables F28 ranking (logging-precedes-learning: capture the parameter the learning feature depends on at the moment the deal is born, not retroactively). Cost is one dropdown on the create-new-deal path only.

**Step 2 — AI parsing (background)**
Progress indicator: "Detecting structure… identifying clauses… resolving cross-references… flagging uncertain nodes." 10–30 seconds. Cannot be skipped.

**Step 3 — Review UI**
Two-panel layout: candidate tree on the left, original source text on the right (read-only, for reference while correcting).

The candidate tree shows every detected node with a confidence indicator: ✓ (confident) or ⚠ (uncertain level or type). Operator only needs to touch ⚠ nodes. On a 900-paragraph contract where AI gets 85% right, that's ~135 corrections, not 900.

Actions on nodes: ± level (keyboard arrows for speed), multi-select → bulk level shift, split, merge, delete, type badge toggle (HEADING / BODY / TABLE / APPENDIX). Role-region rendering (front-/back-matter as labeled regions, DD-54/DD-56/DD-58).

Style detection also runs here — donna.ai proposes a style config derived from the source document. Operator can accept, adjust, or select an existing style template. Preview pane shows a sample clause rendered with the proposed config.

**Step 4 — Commit**
Summary: "Import N clauses, M tables, P appendices under [Client] → [Deal] → [Contract name]." One confirm button. Spinner. Done. Cockpit opens for the new contract.

## Export & Mark-as-sent affordances

The **UI affordances** only — the snapshot/pointer/redline/issue-list **pipeline mechanics** live in **SPEC §12** (canonical), grounded in DD-60/DD-61/DD-71/DD-72.

Export is a **per-contract cockpit action** (top-right `Export ▾`), never in global nav (see Navigation model): you export the contract you're already in. **Mark as sent** is the separate boundary action (cockpit AND each My Contracts card), decoupled from export because donna.ai can't actually send (DD-71).

```
[ Export ▾ ]   (pure file generation — downloads only, no boundary effect; DD-71)
  ├── Clean copy (.docx)                                          (F15b)
  ├── Redline (.docx)  from: [ last shared with counterparty ▾ ]  (F15)
  └── Issue list (.docx)                                          (F31)

[ Mark as sent ▾ ]  → [ Counterparty | Legal ]   (the boundary event; DD-71 — also on each My Contracts card)
   cuts a snapshot of the current working copy · advances last_shared_with_X · badge → Sent · mints vN
   ⚠ non-blocking drift warning if edited since the last export
```

- **`Export ▾`** has no recipient selector — every export is a plain download (DD-71). The Redline item carries a baseline dropdown (defaults to `last shared with counterparty`, DD-48). Redline is **disabled until the first Mark as sent cuts a snapshot**, with the hint *"No baseline yet — export a clean copy, send it, and Mark as sent first."*
- **`Mark as sent ▾`** picks the recipient (Counterparty and/or Legal). On a working copy edited since the last export it shows the one-click-through drift heads-up — *"You've edited since your last export (vN, date). Marking now records your CURRENT version as sent. [Mark anyway] [Re-export]"* — never a gate (DD-72).
- **Version-history drawer** — clicking the lifecycle badge opens the lineage view (F27/DD-75): Working copy pinned + v1…vN timeline with the current-baseline tag + 2 greyed `received` slots ("arrives with revision import"); clicking a version opens it **read-only** ("Viewing v3 — read-only · Return to working copy").

## Negotiation cockpit

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
- **Read-and-explain guardrail.** The box **explains the contract; it never advises, drafts, or takes a position.** "Should I accept this?" / "is this enforceable?" / "what should we counter?" are **not** answered here — Donna deflects to the issue-scoped surface (F11, where advice + drafting + live research live) or, when the question needs legal judgment beyond the document, to **"get a lawyer"** with a framed question + attached clauses (DD-14 rules 1–2). A positional assertion made outside an issue and ungrounded in a ledger is exactly the §2.4 credibility risk this guardrail removes. (With an active clause/issue anchor, advice + drafting are available via F10b — DD-69; the guardrail is the *no-context* behaviour.)
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
│  DONNA  (F11 — Phase 2 placeholder)                        [↻ Refresh] │
│                                                                         │
│  The proposed rate is below market for comparable storage              │
│  arrangements in this sector.                                          │
│                                                                         │
│  ⚠ Market-figure grounding (live research, DD-38) is OUT of v1 —       │
│    F11 v1 recommends the counter STRUCTURE and flags the missing       │
│    benchmark; it never invents a number (PM_TODO F11 §3).              │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  "…shall store Products at a rate of [counter rate] per month…" │   │
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
- **Donna's recommendation** — generated asynchronously after issue creation; ready when the detail view opens. Reasoning from clause context + agreed/open positions; **live market research (DD-38) is deferred out of v1** — a market-figure issue gets the counter structure + a flagged missing benchmark, never an invented number (F11 v1, PM_TODO). Cites sources only once DD-38 lands (Phase 2).
- **Use Donna's language** — applies her proposed text to the clause body immediately. **Edit Donna's language** — opens the clause inline editor pre-filled with her proposed text.
- **Brainstorm with Donna ↗** — opens an **ephemeral** chat overlay pre-loaded with the clause + issue description. Back-and-forth exploration ("what if we propose a tiered rate?", "what's the floor we should accept?") without leaving context. The conversation is **not persisted** — it opens fresh each time, the raw transcript is discarded (DD-42/DD-73/DD-77). If the brainstorm produces language the operator accepts, that committed outcome applies to the issue through the standard apply path (adopted language lands on the issue, node version + audit entry written, decision logged per DD-29). **On close Donna distils a compact, readable summary** (the question explored, the position concluded, the key fallbacks considered) and **stores it on the issue** (`brainstorm_summaries`, one row per pass) so the reasoning survives without the transcript (DD-73/DD-77). This is separate from the silent cross-deal pattern extraction (DD-55/DD-76), which fires on issue-close. Donna's grounding still reads only the committed ledger + clause text, never the summary or transcript.
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

  Pre-filled with the current clause body. Editable — covers "agreed as-is" (no edit) and "we settled on different language on the call" (edit before confirming). On confirm: clause body updated (if changed), issue status → `closed`, ● badge on clause → ✓, issue drops from the open list into the collapsed Closed section (the Agreed tab, locked/read-only), audit log entry written. Issue-close also fires the failure-isolated insight distillation (F30/DD-76).

### Counterparty revision review — two-pane document view (F03c)

The Mode-B revision-review surface. Two panes (DD-81); the full rendering encoding + guided-cursor interaction model is DD-83, the review pipeline is **SPEC §11 Mode B**.

- **Left rail = navigator + to-do tracker** — one row per change in document order; anchor label = clause number else role-based fallback ("Appendix title"/"Draft note"/"Recital"/"New clause"); Added/Deleted/Modified tags; decided-state tick/strike; a prominent "N pending".
- **Main area, two modes:** **Phase 1 match-confirm** = before/after baseline │ revised columns, each abstain pair highlighted in both sides, controls = Confirm match / Not a match→new / Match to a different clause (DD-78 step 6b). **Phase 2 content** = the document in reading order, changed clauses neutral-highlighted (kind colour only on the rail tags), click → **inline expand** to a single embedded full-clause redline (insertions green/underline, deletions red strikethrough — read in place like Word, DD-81).
- **Per-change (Word-style) decisions** — a clause with several edits exposes each as its own "Change N" row with its own Accept theirs / Use Donna's / Edit / Keep; whole-node added/deleted stay one node decision (DD-81/DD-83).
- **Encoding (DD-83):** colour = green add / red delete / purple Donna-adopted; line-style = dotted pending / solid decided; reject keeps a struck trace; Edit/Use-Donna's shows both Word-style (orig red struck + new purple underline).
- **Guided decision cursor (DD-83):** stable decision dock (focused change auto-scrolls + highlights); cursor walks strict document order, stopping only on open changes; auto-advance on decide; edit advances on save; end → Apply; re-open a decided change to redo; `<`/`>` step prev/next open change. Donna's verdict + counter auto-populate at import (DD-82), so "Use Donna's" is live on open.
- **Brainstorm escalation** reuses the ephemeral overlay, seeded per-change.

---

## Design decisions referenced here

`DD-NN` records live in [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md); see the DD index in SPEC §8. DD-19 through DD-48 (plus the later UI/Donna/import/export/version-model DDs) originated as UI design records.
