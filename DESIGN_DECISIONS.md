# donna.ai — Design Decisions (ADR log)

> Architecture & design decision records for [`SPEC.md`](SPEC.md).
> Each entry: **Decision · Rejected alternative · Rationale.**
> Inline `DD-NN` references throughout the SPEC resolve here.
> Last updated: 2026-06-21.

---

## Index

| # | Decision |
|---|---|
| DD-01 | Database is the source of truth; content stored as structured semantic data |
| DD-02 | Presentation and numbering are derived, not stored |
| DD-03 | Tracked changes come from DB snapshot diffs |
| DD-04 | Import is two-step, with two modes |
| DD-05 | One adjacency-list node tree (clauses + appendices unified) |
| DD-06 | Tiered context injection — see SPEC §7 |
| DD-07 | Negotiation-history RAG scoped to contested nodes — see SPEC §7 |
| DD-08 | Call mode merged into edit mode |
| DD-09 | Snapshots as the versioning primitive |
| DD-10 | Defined terms as first-class, deal-scoped entities |
| DD-11 | Cross-references as structured links |
| DD-12 | Shared commercial values are deal-level parameters |
| DD-13 | Two write paths — issue vs direct edit |
| DD-14 | Donna's behavioral contract — see SPEC §7 |
| DD-15 | Operator vs Principal permissions — see SPEC §4 |
| DD-16 | Import review UI is a tree editor with multi-select |
| DD-17 | Two issue anchor modes — node-scoped and contract-level free-floating |
| DD-18 | Node creation mid-negotiation |
| DD-19 | Left panel is modal, not contextual |
| DD-20 | Issue visibility is pull, not push |
| DD-21 | Style config is per-contract, set at import, auto-applied on every export |
| DD-22 | Every export cuts a snapshot; "sent to counterparty" is a separate explicit tag |
| DD-23 | Contract types and clients are user-configurable in Settings, never hardcoded |
| DD-24 | Style templates are reusable configs; contracts inherit and override |
| DD-25 | Counterparty revision uses a dedicated staging structure; issues are created only as outcomes, not upfront |
| DD-26 | Counterparty revision review uses inline tracked-change rendering, not a two-pane diff |
| DD-27 | Donna drafts exact counter-language on every counterparty-proposed change, not instructions |
| DD-28 | Counterparty revision diff uses two paths |
| DD-29 | Every accept/reject/modify decision is a learning signal, logged with rich context from day one |
| DD-30 | Both import modes are fault-tolerant for structural errors |
| DD-31 | Donna resolves cross-references, defined terms, and deal parameters before analysing any clause |
| DD-32 | `node_embeddings` are built in Phase 2, not Phase 2+ |
| DD-33 | Embedding generation is always asynchronous, always diff-scoped, and always post-structure-finalisation |
| DD-34 | Tracked-change review in Mode A uses two-tier triage, not one-by-one review |
| DD-35 | AI model tier assignment by task consequence — see SPEC §7 model quality principle |
| DD-36 | Numbering-pattern inference corrects hierarchy silently before structural triage |
| DD-37 | Style config JSONB schema — locked at Phase 0 |
| DD-38 | Donna invokes live web research when an issue involves a market data point |
| DD-39 | Intended negotiation cadence — legal version first, verbal discussion second |
| DD-40 | Donna tab is a persistent conversation, context-managed by sliding window + rolling summary |
| DD-41 | Contract amendments are new contracts under the same deal, not edits to the signed original |
| DD-42 | Brainstorm is ephemeral; only the adopted outcome persists |
| DD-43 | Import and export are two transforms over one intermediate representation; export is a deterministic renderer that doubles as the import verifier |
| DD-44 | Generated tracked changes and exports are attributed to the operator's organization (configurable), never to Donna or the AI |
| DD-45 | Spike #1 results — round-trip verifier validated on real contracts; content controls are a mandatory parser target |
| DD-46 | First import (Mode A) is clean-only in v1; tracked-change resolution on first import is deferred to v2 |
| DD-47 | External revisions are one engine with a source-parameterized stance; Donna optimizes for closing and moderates over-reach from any source — including the operator's own legal team |
| DD-48 | Each contract carries four named snapshot pointers plus the live working copy |
| DD-49 | Donna grounds on a curated static knowledge layer (CUAD taxonomy + market benchmarks), not a live legal database |
| DD-50 | First-pass auto-issue detection rides the issue engine as an operator-confirmed draft, source-parameterized, never authoritative |
| DD-51 | Spike #2 results — Word-renderable tracked-change generation validated end-to-end, including author attribution |
| DD-52 | LangGraph omitted from v1 (flows are linear/single-branch); parked for re-examination if a single-shot AI surface's eval quality proves insufficient |
| DD-53 | No authentication and no identity/users model in v1 — single-operator local; the `actor` enum suffices; identity + auth are designed together at the v1.1 principal portal |
| DD-54 | Import distinguishes front-matter (title/preamble) from operative clauses; the clause tree begins at the first top-level numbered heading; TOC dropped |

---

### DD-01: Database is the source of truth; content stored as structured semantic data
**Decision:** The DB holds contract content as structured semantic data — prose as lightweight markup, tables as rows/cells — not as plain text and not as Word/OOXML fragments. Word is an export format.
**Rejected:** (a) plain-text bodies — loses tables, inline emphasis, and structure; (b) storing OOXML fragments — mixes presentation with data, hard to edit/query/diff.
**Rationale:** A *content-complete* data layer preserves meaning at zero drift while keeping the layer pure, queryable, diffable, and AI-native. Nothing of meaning lives only in formatting.

### DD-02: Presentation and numbering are derived, not stored
**Decision:** Formatting (bold/underline/highlight) is produced by deterministic rules from a **per-contract style config**. Clause numbering is **derived from tree position** + the numbering scheme.
**Rejected:** Storing numbers and formatting as content.
**Rationale:** Representative contracts are auto-numbered already; deriving numbers means add/move/delete propagates automatically and cross-references follow (DD-11). Per-contract style configs are required because house styles differ across documents.

### DD-03: Tracked changes come from DB snapshot diffs
**Decision:** On export, diff the previous snapshot against current node state; emit as Word tracked-change markup. Pure renumber shifts are **suppressed** (not shown as changes).
**Rationale:** We generate .docx from the DB, so Word's native tracking is unavailable. DB diffs are better — we control granularity and avoid polluting redlines with dozens of spurious "2.3 → 2.4" marks.

### DD-04: Import is two-step, with two modes
**Decision:** (1) AI-assisted parse → candidate structured tree; (2) human review/correction before commit. **v1 first import is clean-only** (fresh draft); a clean-document guard blocks/flags tracked changes on import (DD-46). The resolve-tracked-changes first import (heavily redlined doc, two-tier triage) is **deferred to v2** (DD-34, DD-46) — in the normal lifecycle donna.ai is present from v1 and every counterparty redline flows through Mode B (DD-25/DD-28).
**Rejected:** Single-step auto-import; building Mode A tracked-change triage in v1.
**Rationale:** Legal parsing is structurally ambiguous; a wrong parse silently corrupts everything downstream. Tracked-change volume (zero to 700+) is handled in Mode B; first import is clean in v1, so the import spine stays small (DD-46).

### DD-05: One adjacency-list node tree (clauses + appendices unified)
**Decision:** A single self-referential `nodes` table of arbitrary depth. Appendices are top-level branches that nest like Articles. Each node has a content_type (prose/table/attachment).
**Rejected:** A separate appendices entity; a JSONB tree blob.
**Rationale:** Appendices have their own sections/sub-sections and must be selectable, issue-able, and editable exactly like clauses. Per-node rows enable per-node querying, embeddings, issues, and versioning.

### DD-06: Tiered context injection — see §7. Structured data = DB query always. Free-text prose = semantic search. `node_embeddings` (intra-contract semantic search) built in Phase 2 regardless of volume — required for Donna's analytical intelligence, not a scale feature. `comment_embeddings` (negotiation history search) deferred to Phase 2+ when comment prose exceeds ~100K tokens. See DD-32.

### DD-07: Negotiation-history RAG scoped to contested nodes — see §7.

### DD-08: Call mode merged into edit mode
**Decision:** No separate call UI. Live calls use the same select-node → issue/edit flow.
**Rationale:** The standard flow already covers live capture; a separate mode adds complexity for no new capability.

### DD-09: Snapshots as the versioning primitive
**Decision:** `contract_snapshots` capture all node states at send-time, like git commits.
**Rationale:** Required for accurate redline diffs; also reproduces exactly what the counterparty reviewed on any date, and powers version-diff views.

### DD-10: Defined terms as first-class, deal-scoped entities
**Decision:** Extract defined terms on import into a deal-scoped registry; link mentions in node bodies.
**Rationale:** Prevents silent cross-contract divergence, enables hover-to-define and accurate AI context.

### DD-11: Cross-references as structured links
**Decision:** On import, detect cross-references (typed and field-based) and convert to explicit node→node links. Render the target's *current* number dynamically.
**Rejected:** Frozen reference text.
**Rationale:** Resolves the conflict between "preserve the reference" and "update on renumber." Real contracts can carry hundreds of cross-references; broken references on renumber are a top risk. Resolves OQ-02.

### DD-12: Shared commercial values are deal-level parameters
**Decision:** Model price, margin, royalty %, capacity, cross-default linkage, etc. as deal parameters defined once and referenced by nodes. Changing one flags every referencing node; conflicting values at import are flagged.
**Rejected:** Re-scanning text for divergence after the fact.
**Rationale:** Makes cross-contract consistency *structural* rather than something the tool might miss.

### DD-13: Two write paths — issue vs direct edit
**Decision:** A selected node can be (a) raised as an issue (comment → open issue) or (b) edited inline. A direct edit creates a node version, writes to the audit log, and auto-surfaces in the next redline — but creates no issue.
**Rationale:** Small agreed-on-the-spot fixes shouldn't clutter the issue list, but must never escape the paper trail or the redline.

### DD-14: Donna's behavioral contract — see §7.

### DD-15: Operator vs Principal permissions — see §4.
**Decision:** Operator has full edit; principal can decide issues but cannot edit clause content.
**Rationale:** Gives the principal real authority without letting an infrequent, untrained user corrupt the source of truth; preserves a clean record of who decided what.

---

### DD-16: Import review UI is a tree editor with multi-select
**Decision:** The import review UI renders the parsed candidate tree as an interactive tree editor. Operator can select multiple nodes and shift their depth level in one action. Nodes the parser flagged as low-confidence are highlighted. No commit until the operator approves.
**Rationale:** Lawyer-drafted contracts arrive with inconsistent formatting — manually typed numbers, mixed styles, non-standard heading levels. The error rate on first parse is real but bounded (~5-10%). Multi-select bulk correction makes fixing 20-30 nodes fast enough that it's not a barrier to import.

### DD-17: Two issue anchor modes — node-scoped and contract-level free-floating
**Decision:** Issues can anchor to a specific node (standard) or float at contract level with no node anchor (`node_id = NULL`). Free-floating issues capture general remarks, structural concerns, and negotiation points raised before the relevant clause exists.
**Rejected:** Forcing every issue to anchor to a node.
**Rationale:** Live negotiations surface points that don't map to existing clauses — "we need a whole new section on X," "generally uncomfortable with the liability framework." These must be captured immediately without blocking on clause creation. Free-floating issues can be anchored to a node later, or remain contract-level if they resolve without a clause change. **Anchor at creation defaults to the clicked node and is editable; the anchor can be re-pointed at any time (`node_id` mutable).** For a not-yet-existing clause, anchor the issue to the nearest existing parent (or leave it document-level), then re-point it to the new node once the clause is inserted.

### DD-18: Node creation mid-negotiation
**Decision:** Operator can create a new node at any point, choose its parent, and position it among siblings. The node immediately receives a derived number, is editable, and can have issues attached. It surfaces as a tracked insertion in the next export.
**Rationale:** Negotiations routinely add new sections. The tool must support the live workflow without forcing a re-import or a Word round-trip to add a clause.

### DD-19: Left panel is modal, not contextual.

The context panel (slide-in on clause selection) was considered and rejected. It hijacks screen real estate during live calls without explicit intent. The left panel toggle is explicit — user chooses when to switch. Right panel is never displaced by a context panel.

### DD-20: Issue visibility is pull, not push.

Issues are not surfaced inline in the document view by default. The operator signals intent (toggle to issues mode, open the issues tab) to see them. During a live call, the document view is sacred.

### DD-21: Style config is per-contract, set at import, auto-applied on every export.

Rejected alternative: style options at export time. Reason: export must be one click; formatting decisions made repeatedly are formatting decisions made inconsistently. Style templates (shared configs) reduce setup work across contracts in the same deal.

### DD-22: Every export cuts a snapshot; "sent to counterparty" is a separate explicit tag.

Snapshots are automatic on every export — no action required, version history is always complete. The "sent to counterparty" tag is manual: operator checks a box on the export that is actually going to the counterparty. The tag is exclusive — only one snapshot holds it; applying it moves it from the previous holder. Mode B always diffs against the tagged snapshot. Tag can be moved retroactively via snapshot history. Internal exports (sharing with a lawyer, internal review) cut a snapshot but do not affect the diff baseline. **Rejected alternative:** tag every export as the counterparty baseline automatically. **Reason:** the operator regularly exports for internal use; conflating "exported" with "sent to counterparty" would corrupt the diff baseline on the next revision import. **Generalised by DD-48:** the single "sent to counterparty" tag becomes one of four named version pointers (shared / received × counterparty / legal); the export recipient selector sets the appropriate "shared with" pointer, and importing a revision sets the matching "received from" pointer.

### DD-23: Contract types and clients are user-configurable in Settings, never hardcoded.

Rejected alternative: enum of contract types in application code. Reason: every operator's deal taxonomy is different; hardcoding makes the tool fragile for any client that doesn't fit the assumed types.

### DD-24: Style templates are reusable configs; contracts inherit and override.

Rejected alternative: copy-paste style config per contract manually. Reason: when a deal has three contracts sharing the same house style, the style should be defined once and inherited, not duplicated three times and then drift.

### DD-25: Counterparty revision uses a dedicated staging structure; issues are created only as outcomes, not upfront.

`counterparty_revision_changes` (one per node/chunk) and `counterparty_revision_hunks` (one per individual edit within a node) are the staging tables. **Navigation unit = node (chunk).** Decision unit = hunk. Chunk = node by definition: numbered clauses, lettered sub-clauses, free-floating paragraphs, definitions, and tables are all nodes and therefore all chunks — this is not a new concept, it is already embedded in the tree model. After the operator decides each hunk: accepted/modified → applied directly to the node, no issue created; rejected/deferred → issue created with `initiator: counterparty`, `their_position` = proposed text. Issues represent persistent contested points; the review tables are a staging workflow. Rejected alternative: surface every edit as an issue upfront. Reason: (a) accepted changes shouldn't become issues — they're resolved immediately; (b) grouping by node is the right cognitive unit — you cannot evaluate a word deletion without seeing the surrounding additions; (c) 40 individual issues for 12 edited nodes is overwhelming; 12 node-level items is manageable. Donna pre-classifies hunk significance by semantic impact, not edit size — a single word change can be substantive if it alters meaning, obligation, or commercial effect. Trivial = demonstrably no meaning change. Substantive = anything that shifts what a party must do, when, or under what conditions. Two rules: (1) classification requires DD-31 context resolution first — never classify on raw hunk text alone; (2) trivial is a high-confidence positive call, not a residual — when uncertain, always classify substantive. Trivial hunks are pre-recommended Accept with no counter drafted; substantive hunks get full analysis + exact counter-language regardless of how many words changed.

### DD-26: Counterparty revision review uses inline tracked-change rendering, not a two-pane diff.

Rejected alternative: left-right split (what you sent vs. what they changed). Reason: two-pane halves screen real estate, forces left-right eye movement, and is less familiar to contract readers than inline tracked changes. Strikethrough/underline inline is how Word shows it; operators already know how to read it.

### DD-27: Donna drafts exact counter-language on every counterparty-proposed change, not instructions.

Rejected alternative: high-level guidance ("you should reject this because X"). Reason: the value proposition is removing drafting work. The operator's job is to judge Donna's language, not write from scratch. Four actions: Accept theirs / Use Donna's counter / Edit Donna's counter / Keep original. **Positional grounding — no separate "brief" needed:** Donna infers the operator's position from what the system already knows: (1) agreed issues — what was ultimately accepted on settled points; (2) open issues — `our_position` on each contested point; (3) deal parameters — the locked commercial figures; (4) the draft contract itself — the starting language reflects the operator's opening position. On the first issue for a topic with no prior positions, Donna grounds in the draft language + live research (DD-38) for market data questions, and flags her reasoning basis explicitly rather than asserting a position she hasn't been given.

### DD-28: Counterparty revision diff uses two paths.

Path A: if incoming .docx contains Word tracked changes (`<w:ins>`/`<w:del>`), extract directly from XML — reliable, exact. Path B: if clean edited copy (no tracked changes), parse → match nodes against last snapshot (by section number, then heading similarity, then body text similarity) → diff text pair-by-pair. Both paths produce identical output: a numbered list of changes for the review flow.

### DD-29: Every accept/reject/modify decision is a learning signal, logged with rich context from day one.

Phase 2: Donna retrieves past decisions as RAG context — "In similar situations, you rejected this type of change." v2: Donna infers negotiation principles from accumulated decisions and applies them proactively without citing examples. The infrastructure (rich `decision` JSONB on issues) must be built in Phase 2 even though the intelligence matures in v2. If we don't log decisions richly from the start, v2 has nothing to learn from.

### DD-30: Both import modes are fault-tolerant for structural errors.

First import: tree editor review flags uncertain nodes in amber; operator corrects before commit. Counterparty revision import: a structural triage step runs before the content review flow, surfacing any formatting/depth/heading errors the counterparty's legal team introduced. Operator resolves structural issues first, then content decisions. The two concerns (structure vs. content) are deliberately separated — conflating them in one review flow creates confusion about what is being decided.

### DD-31: Donna resolves cross-references, defined terms, and deal parameters before analysing any clause.

When a node contains `{Term}`, `[[Section X.Y]]`, or `[figure]` markers, Donna retrieves the referenced content from the DB and injects it as context before classifying significance, drafting counter-language, or answering a question. This applies to all three Donna surfaces (counterparty revision reviewer, issue-scoped assistant, deal-scoped assistant). Depth limit: one level of reference resolution by default — Donna follows direct references from the clause under analysis but does not recursively chase reference chains. If deeper resolution is needed for a reliable answer, Donna flags it rather than hallucinating context. Cross-contract references (`[[TLA Section 4.2]]`) resolve the same way if the target contract is in the DB. Rejected alternative: analyse only the clause text in isolation. Reason: a percentage change in one clause is meaningless without knowing what the base it applies to means in another clause — shallow analysis produces shallow recommendations.

### DD-32: `node_embeddings` are built in Phase 2, not Phase 2+.

Rejected assumption: intra-contract semantic search is only needed at volume. Correct framing: Donna needs to discover implicit clause relationships within a single contract to reason reliably — explicit cross-references only cover links the drafter thought to mark. A payment clause may be semantically related to a price-adjustment clause with no `[[...]]` link between them. Donna must be able to find that relationship herself. pgvector on `node_embeddings` is therefore core to Donna's analytical intelligence, not a scale feature. The ~100K token threshold only applies to `comment_embeddings` (negotiation history search).

### DD-33: Embedding generation is always asynchronous, always diff-scoped, and always post-structure-finalisation.

Three rules that never bend: (1) never synchronous — embedding API latency must never block a save; (2) never re-embed unchanged content — only nodes whose `body` actually changed get queued; (3) never embed before structure is confirmed — embeddings fire only after the operator has committed the corrected tree (Mode A: after import review Commit; Mode B: after structural triage is committed, then per content decision). Embedding malstructured pre-correction content wastes API calls and pollutes search. Structural changes (renumber, reorder, reparent), defined term updates, and deal parameter changes do NOT trigger re-embedding. Staleness tracked via `embedded_at` vs `updated_at`; worker skips any node where `embedded_at >= updated_at`.

### DD-34: Tracked-change review in Mode A uses two-tier triage, not one-by-one review.

**[Deferred to v2 — superseded for v1 by DD-46; first import is clean-only. Retained for the v2 "onboard an existing in-flight contract" feature.]** A representative contract may contain 700+ tracked changes. Reviewing each individually is a non-starter. Two tiers: (1) **Bulk triage** — Donna pre-classifies all tracked changes as `accepted_baseline` (change is already agreed or is a trivial formatting difference — fold into the starting node text) vs `open_proposal` (live negotiation point — seed as issue). Operator sees the **full list of Donna's auto-accept candidates** — node reference, change type, original → proposed text inline — not just a count. Operator scans the list and **unchecks any item they want to pull out for individual review**. One "Commit triage" button: checked items fold into baseline, unchecked items join the open issues queue. (2) **Individual review** — the unchecked items (typically 20–50 of 700) are reviewed one by one in Tier 2, using the same four-action flow as Mode B counterparty revision review. Operator maintains full oversight and veto at all times; Donna proposes, operator approves. Rejected alternative: single-item sequential review of all changes. Reason: at 700 items, sequential review is not meaningfully different from doing it in Word — it eliminates the tool's value on the most painful import case.

### DD-35: AI model tier assignment by task consequence — see §7 model quality principle.

Product decision: Opus for high-consequence tasks (significance classification, counter-language, brainstorm, triage); Sonnet for medium-consequence tasks (Q&A, summaries, context resolution); Haiku for low-consequence extraction and detection. Haiku pre-screen router cuts Opus calls on bulk triage. All model assignments in `config/` — never in application code. **Kevin's ADR:** routing architecture, config key schema, Haiku confidence threshold for trivial pre-screen, escalation logic.

### DD-36: Numbering-pattern inference corrects hierarchy silently before structural triage.

After parse, before any operator-facing review, a post-parse pass reads each node's heading text and infers the correct parent-child relationship from its numeric or letter prefix. Inference rules: `N.` = depth 0 (article); `N.M` = depth 1, child of article N; `N.M.P` = depth 2, child of section N.M; `(a)`, `(b)` etc. = child of the immediately preceding decimal clause at depth N-1, resolved by position not by explicit parent reference. Where inferred depth contradicts parsed outline level, inferred depth wins — the heading number is ground truth. Nodes with no recognisable numbering prefix (body text, narrative paragraphs, table rows) retain their parsed depth; if their position looks anomalous relative to numbered siblings, they are surfaced in structural triage. The correction is silent — no operator action required unless residual anomalies remain after the pass. **Rejected alternative:** rely solely on Word's outline level / list structure. **Reason:** counterparty documents routinely apply heading styles inconsistently; the numbering in the clause text is the only reliable, document-agnostic signal for hierarchy.

### DD-37: Style config JSONB schema — locked at Phase 0.

Minimum fields required for import-time detection, preview rendering, and export regeneration:
  ```json
  {
    "font": "Times New Roman",
    "numbering_scheme": "decimal",
    "body_font_size_pt": 10,
    "indent_per_level_pt": 18,
    "page_breaks_before_articles": false,
    "levels": {
      "0": { "bold": true,  "caps": true,  "underline": false, "font_size_pt": 12 },
      "1": { "bold": true,  "caps": false, "underline": true,  "font_size_pt": 11 },
      "2": { "bold": true,  "caps": false, "underline": false, "font_size_pt": 11 },
      "3": { "bold": false, "caps": false, "underline": false, "font_size_pt": 10 }
    }
  }
  ```
  Keys under `levels` are depth values (0 = article, 1 = section, 2 = clause, 3 = sub-clause). `numbering_scheme` is `"decimal"` (1 / 1.1 / 1.1.1) or `"mixed"` (1 / 1.1 / (a) / (i)). Donna infers these values from the source document at import; operator accepts, adjusts, or selects a template. This schema is what `style_templates.config` and `contracts.style_config` store. **Resolves OQ-06.**

  **`caps` is a render-time transform, never stored uppercase (content-integrity boundary, principle §2.1).** When a level has `"caps": true`, the heading is uppercased *only at export/preview render* — e.g. a section heading stored as "Confidentiality" renders as "18. CONFIDENTIALITY" (number derived from tree position per DD-02, title uppercased by the renderer). The node's stored heading text stays original-case ("Confidentiality"); round-tripping must recover the original case exactly, so the export renderer applies the uppercase transform and the import parser records `caps: true` rather than mutating the text. Source-derived: if the original document presents a level's headings in bold all-caps, import infers `bold: true, caps: true` for that level and regeneration reproduces it faithfully. **Build note:** spike #1's regen rendered headings in their stored case (no transform); the Phase 0/3 renderer must implement `caps` as an uppercase transform and the round-trip verifier (DD-43) must assert stored case is preserved while rendered case matches the source. Operator can override the inferred per-level `caps`/`bold` in style config.

### DD-38: Donna invokes live web research when an issue involves a market data point.

When Donna generates her recommendation for an issue and detects that the issue involves a price, rate, threshold, or industry standard (inferred from the operator's summary and the clause text), she invokes a live web search before producing her recommendation. She cites each source inline with URL and retrieval date. The citation is stored in `issues.donna_research_citations`. If she cannot find a reliable source, she flags the gap rather than asserting a number without evidence. **Rejected alternative:** derive position purely from the contract text and deal parameters. **Reason:** commercial figures in contracts need market grounding — a position asserted without a comparable benchmark is weak in negotiation; one backed by a live market rate is defensible. New external dependency: a search API (Perplexity or equivalent) must be wired into the services layer. Kevin's ADR on search API choice and caching strategy.

### DD-39: Intended negotiation cadence — legal version first, verbal discussion second.

When the counterparty's legal team sends a revised version AND the counterparty contact has verbal feedback, the correct sequence is: (1) legal redline arrives and is imported via Mode B — this is the diff baseline; (2) verbal call happens on top of the reviewed version — verbal points are additive issues, never a parallel track. **Rejected:** three-way merge (operator produces one version from verbal feedback; legal team produces another from the same base; tool reconciles both) — deferred to v2. **Rationale:** parallel editing creates two competing versions of the same clauses with no clean import path; the process constraint eliminates the conflict entirely at the cost of requiring counterparty cooperation on sequencing. Lilly confirmed this is the intended v1 workflow.

### DD-40: Donna tab is a persistent conversation, context-managed by sliding window + rolling summary.

The contract-level Donna Q&A thread (`donna_conversations` + `donna_messages`) persists across sessions — the operator sees prior questions and answers when she returns days or weeks later. **Context blowup is avoided structurally:** the full thread is stored in the DB, but only the **last 10 turns + a rolling `running_summary`** are injected into any Donna call (per the project's LLM windowing standard — sliding window of recent turns plus a running summary; never the full history). Turns older than the window are collapsed into `running_summary`, which Donna updates incrementally. Older turns remain queryable on demand (Donna can pull a specific past exchange from the DB) but never bloat the prompt. **Rejected:** (a) stateless tab — operator re-asks the same questions every round, loses continuity; (b) inject full history every call — context grows unbounded and cost scales with thread length. **Kevin's ADR:** summary-update trigger, exact window size, summarisation prompt. (Lilly confirmed persistent + windowed.)

### DD-41: Contract amendments are new contracts under the same deal, not edits to the signed original.

A signed contract is frozen (`status: signed`, read-only, no further node mutation). When the agreement must change post-signature, an amendment/addendum is imported as a **separate contract** in the same deal, cross-referencing the original's clauses (`[[Master Agreement Section 4.1]]`). The deal stays active as long as any contract under it is live. **Rejected:** mutating the signed original. **Rationale:** the signed document is a legal record of what both parties executed — it must never change; the deal's existing one-to-many contract structure already models this with no new entity. The negotiation workflow on an amendment is identical to any other contract. (Lilly confirmed.)

### DD-42: Brainstorm is ephemeral; only the adopted outcome persists.

The issue-scoped "Brainstorm with Donna" overlay conversation is not stored — it opens fresh each session, pre-loaded with the clause + issue description. When a brainstorm produces concrete language the operator accepts, that outcome commits through the standard apply path: it updates the issue's adopted language, writes a node version + audit entry if clause text changes, and logs the decision (DD-29). The committed position is what Donna grounds future answers on (DD-27) and learns from (DD-29) — never the conversation that produced it. **Rejected:** persisting the full brainstorm thread (as the contract-level Donna tab does, DD-40). **Rationale:** the value is concentrated entirely in the adopted outcome; the exploratory journey — especially proposals the operator rejected — is noise that would actively harm grounding if retrieved later (Donna could resurface an approach the operator explicitly discarded). Persisting only the outcome needs no new entity; it rides the existing decision/version logging. (Lilly confirmed ephemeral, with adopted-outcome persistence.)

### DD-43: Import and export are two transforms over one intermediate representation; export is a deterministic renderer that doubles as the import verifier.

The **skeleton** (`node_tree` + `style_config` + markup vocabulary) is the single IR both directions pivot on. **Export** = a pure, deterministic function `render(skeleton) → .docx` (numbering from tree position, formatting from `style_config` per depth, cross-refs from target position, markup resolved to text) — **no AI in the export path.** **Import** = a layered parse, deterministic-first: (1) OOXML → block stream, (2) blocks → flat node list, (3) numbering-prefix → tree topology (DD-36), (4) style-table inference for un-numbered nodes, (5) AI-assisted semantic enrichment (terms / cross-refs / params / markup, DD-35), (6) human review of layers 4–5 flags only. **Every import is self-verified by re-rendering and diffing content against the source** — drift is surfaced in the tree editor before commit. **One `style_config` rule table** drives export rendering, import detection, and the preview pane. **One markup resolver** serves import-save, export-render, and Donna's context injection (DD-31). **Rejected:** (a) separately-written import and export formatting logic — drifts, double-maintained; (b) import as a pure inverse of export — impossible, formatting→structure is many-to-one and lossy; (c) any AI in the export path — non-determinism breaks round-trip verification and content integrity (Principle 1). **Rationale:** export's determinism is what makes import *safe* — re-render-and-diff turns "did I parse correctly?" from unanswerable into a mechanical check, and confines AI to the genuinely ambiguous residue while the deterministic core (esp. numbering, the bridge derived on export and used as the primary structural signal on import, DD-02/DD-36) carries the load. Ties to OQ-08: the skeleton the round-trip operates on must already carry full tree topology, so the snapshot/topology question is a Phase-0 schema concern, not Phase-3. The de-risk spike (§11) is the first instance of this verifier, not a one-off fidelity test.

### DD-44: Generated tracked changes and exports are attributed to the operator's organization (configurable), never to Donna or the AI.

Every `<w:ins>` / `<w:del>` emitted on export (F15) and every counterparty-facing artifact carries the operator's organization name as author — a single configured value, never hardcoded and never the tool's name. Donna is internal infrastructure and must never surface to the counterparty, consistent with the issues-summary "no Donna attribution" rule (§9) and the internal-artifact exclusion (§12); the redline author is the same principle on the document itself. **Operator organization identity** is a new global setting (single-operator v1): stored in `config/` (`pydantic-settings`), editable in **Settings → Your Organization**, and used as (a) the tracked-change / redline author and (b) author metadata on the regenerated .docx. **Rejected:** attributing changes to "Donna" / the tool, or leaving the author blank. **Rationale:** the counterparty must see the redline as coming from the operator's company — exactly as a manual Word redline would read — and AI involvement is never disclosed on the negotiation artifact. A config value, swappable per deployment with no code change. (Lilly: the author must be the operator's organization, set per deployment, not "Donna.")

### DD-45: Spike #1 results — round-trip verifier validated on real contracts; content controls are a mandatory parser target.

The DD-43 verifier ran against 3 real agreements of distinct formats (a JVA, an OA, a TLA). Findings: **(1) Round-trip is lossless** — extract → regenerate → re-extract is identical on all three; body + table text coverage 99.5–100%. DD-01/DD-02 hold for the clause body on real input. **(2) Numbering is auto-numbered (`w:numPr`) in the clause body** of every sample; literal typed numbers appear only in the table of contents (regenerated on export, never stored) and a few schedule/annex headings (resolved by DD-36 prefix inference) — so DD-02 holds, but the "fully auto-numbered" framing in §10 was too strong and is corrected. **(3) Content controls (`w:sdt` / `w:sdtContent`) are a silent-loss blind spot** — python-docx's default paragraph iteration does not descend into them, dropping fill-in field text (party names, dates, placeholder values; 0.5% of the OA). **The Phase-0 parser MUST explicitly extract `w:sdtContent`** and confirm hyperlink-run capture; this is a hard requirement, not a nice-to-have, because the lost content is the highest-stakes content. **(4)** One sample carried 700 insertions + 161 deletions — the §10 worst case is real, confirming DD-34 two-tier triage is on the critical path. **Rationale:** validates the core architectural bet on real documents before build and converts "the parser might miss content" from an unknown risk into one named, testable Phase-0 requirement.

### DD-46: First import (Mode A) is clean-only in v1; tracked-change resolution on first import is deferred to v2.

In the normal lifecycle donna.ai is present from v1 of a contract, so the first import is a clean draft and every subsequent counterparty redline is handled by Mode B (DD-25/DD-28). First import therefore assumes no tracked changes. **Safety guard (not a silent assumption):** on Mode A import the parser scans for `<w:ins>`/`<w:del>`; if any are present it blocks by default and warns — accept all changes in Word and re-upload, or explicitly confirm import-anyway (changes flattened to their accepted state). The baseline is never built from an ambiguous redline silently. **Rejected:** building the Mode A two-tier bulk-triage flow (DD-34) in v1. **Rationale:** the only first-import-with-tracked-changes case is onboarding a contract already mid-negotiation — the current exception, not the norm — handled in v1 by cleaning the document in Word before import. This removes the hardest, highest-risk piece from Phase 0 and shrinks the import spine to clean-parse + structure review + content-control extraction; the heavy tracked-change work consolidates in Mode B (Phase 2). DD-34's bulk-triage capability is retained for a v2 "onboard existing in-flight contract" feature, and its trivial-vs-substantive auto-classification lives on at the hunk level in Mode B (DD-25). (Lilly: in the normal flow donna.ai is there from v1; the current redlined documents are the exception and will be cleaned before import.)

### DD-47: External revisions are one engine with a source-parameterized stance; Donna optimizes for closing and moderates over-reach from any source — including the operator's own legal team.

Generalize the Mode B import: `counterparty_revision_session` → `revision_session` with `source` (`counterparty` | `legal_team` | `internal`). One diff/review engine, one review UI, one set of four actions. What varies is Donna's per-change stance:
  - **`counterparty`** — adversarial; Donna defends the operator's position and drafts counter-language.
  - **`legal_team`** — allied but **not** blindly incorporated. Donna's priority is a **signable deal that protects the operator**, not maximal protection. She incorporates sound legal improvements, flags where a legal fix dents a commercial term, AND **flags where the legal team has over-reached — an aggressive, maximalist clause likely to trigger counterparty pushback or walkaway — and offers a more balanced alternative that still protects the operator.** Donna moderates her own ally when the ally's zeal threatens the deal. (Real failure mode: legal writes a 100%-protective clause; the counterparty replies "manage your legal team, this is unacceptable"; the deal stalls. Donna's job is to catch that *before* it goes back to the counterparty.)
  - **`internal`** — reserved for a principal's external advisor or other allied reviewer.
  - **Targeted-question path** (a few clauses, not a full redline) rides DD-14's "get a lawyer" boundary: a **`needs_legal_review` issue flag** (distinct from `needs-principal` — legal decides enforceability/risk, principal decides commercial) and a **"legal review packet" export** (flagged clauses + Donna's framed questions, ready to email counsel — minimizes billed hours). Baseline for a legal import is the snapshot the operator sent to legal (selectable at import; optional "sent to legal" snapshot marker alongside "sent to counterparty," DD-22).
  - **Review mechanism (confirmed — bulk-surface):** for a high-volume legal redline, Donna runs a bulk pass — auto-accepts benign pure-legal cleanups and surfaces for one-by-one review only the changes that (a) touch a commercial term or (b) over-reach and risk counterparty pushback. Reuses the deferred DD-34 bulk-triage pattern, which finds its real home here. (Lilly confirmed bulk-surface over full one-by-one review.)
  **Rejected:** a separate bespoke legal-review subsystem; Donna treating legal-team input as authoritative and auto-incorporating it. **Rationale:** import mechanics are identical across sources, so one engine + a `source` flag captures all of it; and Donna's value is protecting the deal as a whole — a maximalist ally that blows up the deal is as much a problem as an adversarial counterparty. This is DD-14 rule 4 ("advocates, but closes") applied to the operator's own side. (Lilly: legal sometimes takes aggressive, maximalist stances biased against the counterparty; the client then says "manage your legal team"; Donna must manage expectations and flag over-reach.)

### DD-48: Each contract carries four named snapshot pointers plus the live working copy.

Four timestamped pointers, each referencing at most one immutable snapshot and each advancing independently as boundary events occur: `last_shared_with_counterparty`, `last_received_from_counterparty`, `last_shared_with_legal`, `last_received_from_legal`. The **working copy** is the live, mutable node tree — not a snapshot, not a pointer; it is simply the contract as it currently is. **"Shared with X"** is set on export to X (recipient chosen at export) and does double duty: the automatic diff baseline for X's next inbound revision (DD-47 source → baseline) and the record of what/when was sent. **"Received from X"** is set on import of a revision from X and points to an **immutable snapshot of X's version exactly as received** (baseline + their proposed changes applied, captured *before* the operator reviews or edits — option (a), Lilly-confirmed). It is **frozen — it never moves when the operator later edits**; this is the load-bearing property that makes `diff(working_copy, last_received_from_X)` answer "what have we changed since their version." The operator may adopt a received version as the working basis (equivalent to accept-all in the Mode B review, or a deliberate shortcut); the instant she edits, the working copy diverges and the diff becomes meaningful — but a received pointer is itself never the working copy. A single snapshot may hold several pointers (the version shared with legal is both the current export and `last_shared_with_legal`). Importing a revision therefore materialises an as-received snapshot **in addition to** running the Mode B review: the reviewed/edited result becomes the working copy; the as-received snapshot stays frozen as the pointer. **Generalises DD-22's single exclusive "sent to counterparty" tag** into this family; the export recipient selector (counterparty | legal | internal | copy-only) sets the matching "shared with" pointer (copy-only sets none). Timestamps come from snapshot `created_at` (shares) and import time (receives). Enables a **version-lineage view** (the v1→v2→…→vN chain with pointer labels) — the at-a-glance antidote to version divergence. **Rejected:** (a) "working copy" as a fifth co-equal tag — conflates live mutable state with frozen snapshots; (b) "received from X" pointing to the post-review merged state — loses the faithful record of what X actually proposed. **Caution (for build):** "adopt their version as working copy" means accepting all their changes — fine for a trusted source (own legal's cleanups) but a major concession from the counterparty; it must be a deliberate, logged accept-all through the review flow, never a silent overwrite. And with two parties interleaved, `diff(working, last_received_from_counterparty)` shows everything since the counterparty's version — including legal's later changes and the operator's — not only the operator's edits. (Confirmed (a) immutable-as-received; the working copy diverges on edit; diff the two for changes-since.)

---

### DD-49: Donna grounds on a curated static knowledge layer, not a live legal database.

A reference dataset (feature F29) seeded once and baked in: the CUAD risk taxonomy (41 categories, whole), a market-benchmark table (per provision: standard / yellow-flag / red-flag thresholds), a red-flag taxonomy, and per-contract-type checklists that attach to the F01b contract-type taxonomy. Derived from CUAD and public/common-knowledge sources. It serves two purposes: grounds first-pass auto-detection (DD-50), and turns many F11 "issue needs a market number" calls from live web research (DD-38) into local lookups — cheaper, lower-latency, deterministic, which §2.4 (correctness is the long pole) wants. **Explicitly not** a live legal database (Westlaw/LexisNexis) — that is a paid enterprise dependency that conflicts with the open-source rule (§1); flagged as a possible v2+ integration, not v1. **Licensing:** the knowledge base is built from CUAD (public) + cited primary sources, not lifted from the third-party `contract-review` skill that prompted this — benchmark values are facts, but another author's curation is their expression. **Scope (resolved):** seed the CUAD taxonomy whole (type-agnostic); ship four type-specific checklists — **Licence, Offtake, JV** built fresh from primary sources (the real v1 deal flow), and **NDA** ported from the source skill (universal). The skill's SaaS / M&A / Payment / Broker checklists are dropped — they don't match v1's deal types. **Origin:** mining the third-party MIT `contract-review` skill for capabilities Donna lacked; the knowledge layer was the reusable core, the skill's one-shot report format and its `legal-redline-tools` companion were rejected (Donna is issue-tracked, not report-driven, and already owns export via DB-regeneration, F15).

### DD-50: First-pass auto-issue detection rides the issue engine as an operator-confirmed draft.

On import, Donna reads the contract against the F29 knowledge layer + the deal `position` and drafts a ranked issue list (red flags, below-market terms, missing provisions, placeholders, missing exhibits, broken cross-references). This is feature F28. **Collapses onto the existing issue engine** — no new subsystem: a new `initiator: donna` and an `auto_flag` JSONB on `issues` (the engine already carried `initiator: counterparty` from Mode B). **Source-parameterized stance** (inherits DD-47's `revision_session.source`): a counterparty version ranks terms-unfavorable-to-us first; the operator's own legal-team version ranks **over-reach** first (where our lawyers went too aggressive and risk the deal — DD-14 rule 4), with below-market as a low-priority scan. **Hard correctness boundary** (§2.4): the skill this came from self-reports F1 ~0.62 on clause extraction — so auto-flags are *Donna-suggested draft only*, visibly marked, operator-confirmed, and **never cross into an export** (consistent with F06 keeping issue authorship human). **North-star gate:** an unranked flood of flags shifts triage work rather than reducing it — so F28 ships **after** the bulk-surface mechanism (DD-47), which auto-collapses benign flags and surfaces only material ones. **Logging precedes learning:** the operator's keep/dismiss decision on each auto-flag (a `dismissed` status transition) logs through the F03d/DD-29 path from the day F28 ships, so the flagger can later be tuned to cut noise — not retrofitted. **Missing-provision flags** compose with F08d: a detected gap offers "Draft with Donna."

### DD-51: Spike #2 — Word-renderable tracked-change generation is validated end-to-end.

**Question (spike #2):** can we hand-emit `<w:ins>` / `<w:del>` OOXML that Word renders as real, Accept/Reject-able tracked changes? python-docx can't do this natively, and both export (F15) and Mode B Path A counterparty-revision parsing (DD-28) depend on it. **Result: PASS at both levels.** XML-level: the elements survive a save/reopen with `w:author` applied and no `w:author="Donna"` (asserted in the spike). Word-level (human check): a one-insertion / one-deletion fixture opened in Word renders both as proper Accept/Reject tracked changes, and the `w:author` attribution displays on screen — which validates the DD-44 operator-org-name mechanism (configured author, never the AI) end-to-end, not just in the XML. **Verdict:** the last load-bearing architectural assumption is retired; tracked-change generation is cleared for Phase 3 export and Phase 2 Mode B Path A. The author slot is filled from `config/` (F25) at build time; "Acme Corp"/"Operator Organization" in the spike are placeholders only.

### DD-52: LangGraph is omitted from v1 — parked, with explicit re-examination triggers.

**Decision:** v1 (and v2 as currently specced) is built on async FastAPI + LiteLLM, with LangChain used only for retrieval (pgvector) — **no LangGraph orchestration**, so the `agents/ nodes/ tools/ memory/` directories are not scaffolded. Document this deviation from the default stack in the project `CLAUDE.md`. **Rationale:** every v1 Donna flow is linear or single-branch — import pipeline (fixed sequence), Q&A and issue recommendation (retrieve → one call → return, with at most a single `if` for live research), counterparty revision review (a map over hunks: classify → conditionally draft). None cycle; none coordinate multiple agents. LangGraph would add a state-machine framework with nothing dynamic to manage. **Parked, not rejected — re-examine if eval quality on a single-shot AI surface proves insufficient and the fix is a reasoning loop.** Concrete triggers, all currently hypothetical: (1) a **self-critique→revise counter-draft loop** (F03c upgrades from single-shot to draft → critic-scores → revise → loop-to-cap) if single-shot counter quality underperforms; (2) **autonomous multi-step round-prep planning** ("prep my full position for round 3" — iterate issues, research, sequence by leverage, with later steps depending on earlier); (3) a **drafter/critic/researcher multi-agent split** if one prompt can't reliably do grounded-draft + adversarial-check + citation together. The threshold in one line: *a Donna surface needs to loop an unknown number of times, or coordinate several agents.* **Explicit non-trigger:** Mode B pause/resume is served by persisted domain state (the staging tables, §11 step 10), not a suspended graph — LangGraph's checkpointer would duplicate what the data model already owns. Tracked in `DEV_TODO.md` as a standing re-examination item gated on eval results.

### DD-53: No authentication and no identity/users model in v1.

**Decision:** v1 ships with no authentication and no `users`/accounts entity. The operator runs the whole stack locally (FastAPI + Next.js + Postgres) as the sole user; the `actor` enum (`user` | `ai` | `principal`) on `node_versions`, `issue_comments`, `issues.decision`, and `audit_log` records who-did-what by value, with no FK to an identity table. **Rationale:** a single-user local tool needs no login; authentication and identity are coupled (multi-user identity is meaningless without auth to bind it), so building an identity model now without auth would be half a feature exercised by nothing. **Rejected alternative:** stub a `users` table in v1 to make v1.1 additive. **Reason rejected:** YAGNI for v1, and v1.1's principal portal is the natural seam where auth + identity arrive together as one deliberate unit. **Accepted cost (one-way-door note):** at v1.1 the `actor` enums convert to FKs into a new `users` table — a contained, known migration touching a handful of columns, landing at a planned phase boundary rather than mid-flight. Deployment context (§4): Phase 0 local on the operator's laptop, Phase 1 Azure Switzerland North; production auth is designed with the portal, not before.

### DD-54: Import distinguishes front-matter from operative clauses; not every paragraph is a clause.

**Problem (found in real-data review):** the parser categorized *every* paragraph as a clause/body — so the title page, parties, recitals, and every table-of-contents line imported as fake clauses, numbered 1, 2, 3… before the real first clause ("1. Definitions…") even began. **Decision:** the import recognizes three regions by role. The **operative-clause tree begins at the first top-level numbered heading** (e.g. "1. NAME"; the parser may generalize to "Article 1"/"Section 1" — Kevin's detection ADR). Everything *before* that boundary is **front-matter**, carried on a new `nodes.role` field: the first block is `title`, the remainder `preamble`. Front-matter is **preserved and shown but excluded from the clause tree and clause numbering** — which also fixes the spurious 1/2/3 numbering, since the operative tree now re-derives from the real clause 1. The **table of contents is detected and dropped** on import — this is conformance to the existing §10 rule ("TOC regenerated on export, never stored"), so TOC-as-clauses was a bug, not a new behavior. **Full role taxonomy (validated against JVA/OA/TLA):** front-matter = `title` | `date` | `parties` | `recital` | `agreement_statement`; body = `clause`; back-matter = `appendix` (DD-05) | `signature_block`; cross-cutting = `drafting_note`. Two roles carry rules beyond labeling: **`drafting_note`** — internal counsel/author commentary in brackets (JVA ~28, TLA ~14 in the real set) — is kept but **never crosses into a counterparty export** (§12 integrity; a leaked internal note is a credibility failure); **`signature_block`** (all three contracts) is captured, not a clause. **TOC** is dropped (§10). **Placeholders** — fill-in blanks (`[insert …]`, `___`; JVA ~28, OA 5, TLA 6) — are an inline `has_placeholder` flag → pre-signing "incomplete field" alert (F28), not a role. Front-matter + `signature_block` + `drafting_note` are excluded from the clause tree and numbering; the operative tree re-derives from the first `clause`. **F04 impact:** non-clause roles render as labeled regions (front-matter above, signature/appendix below), not numbered rows; drafting-notes flagged distinctly. **Engineering (DEV_TODO):** boundary detection, the `role` column + `has_placeholder`, per-role detection, TOC exclusion, export-exclusion of `drafting_note`, and the F04 rendering.

**Classification — deterministic-first, AI only for the residue (confirmed).** Roles are assigned by cheap deterministic rules first (keyword/structure: `WHEREAS`→recital, `BETWEEN`→parties, `IN WITNESS`→signature_block, bracketed `[…Note:…]`→drafting_note, tab+page-number→TOC, first `1.`→clause boundary) — free, instant, no hallucination. Only blocks the rules can't confidently place go to an AI pass at the **low-consequence tier (Haiku, DD-35)** — structured classification whose output the operator verifies in F04 (the existing ⚠ mechanism), so the model tier matches the stakes. Not a new subsystem — the same deterministic-then-AI-then-operator-confirm pattern as the existing import detection. **Guard (`drafting_note` is special):** it is the one role excluded from the counterparty export (§12), so a mislabel is asymmetrically costly in both directions (drop real content / leak an internal note). Therefore content is **never auto-excluded from export on the model's say-so alone** — when uncertain whether a block is a drafting note, surface it for operator confirmation; never silently drop. (When-in-doubt-surface, the same stance as the trivial/substantive rule.)
