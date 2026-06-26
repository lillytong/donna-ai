// Typed client for the import-preview / commit endpoints. Mirrors the backend
// contract (backend/models/imports.py: PreviewResponse / CandidateNode / CommitRequest).

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// Structural-role taxonomy (DD-54). Only `clause` is numbered; non-clause roles
// render as labeled regions (front-matter above, back-matter below) and carry an
// empty `number` from the backend.
export type Role =
  | "title"
  | "date"
  | "parties"
  | "recital"
  | "agreement_statement"
  | "clause"
  | "appendix"
  | "appendix_title"
  | "signature_block"
  | "drafting_note";

export interface ApiCandidateNode {
  index: number;
  parent_index: number | null;
  order_index: number;
  depth: number;
  number: string;
  content_type: string;
  heading: string | null;
  body: string | null;
  table_data: string[][] | null;
  plain_text: string | null;
  uncertain: boolean;
  role: Role;
  has_placeholder: boolean;
}

export interface TrackedChangeReport {
  insertions: number;
  deletions: number;
  flattened: boolean;
}

export interface PreviewResponse {
  nodes: ApiCandidateNode[];
  node_count: number;
  uncertain_count: number;
  tracked_changes: TrackedChangeReport;
}

export interface ImportResult {
  contract_id: string;
  node_count: number;
  root_count: number;
  uncertain_count: number;
}

// A persistable node — mirrors backend/models/contract_tree.py NodeRow exactly.
// This is what /commit accepts; numbers are derived (DD-02), never stored.
export interface NodeRow {
  index: number;
  parent_index: number | null;
  order_index: number;
  content_type: "prose" | "table";
  heading: string | null;
  body: string | null;
  table_data: string[][] | null;
  plain_text: string | null;
  uncertain: boolean;
  role: Role;
  has_placeholder: boolean;
}

// Parse a .docx without persisting — returns the candidate tree for review (F04).
export async function previewDocx(file: File): Promise<PreviewResponse> {
  const res = await fetch(`${API_BASE}/import/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: await file.arrayBuffer(),
  });
  if (!res.ok) throw new Error(`Preview failed (${res.status})`);
  return res.json();
}

// Persist the operator-corrected tree (CommitRequest = { nodes: NodeRow[] }).
export async function commitTree(contractId: string, nodes: NodeRow[]): Promise<ImportResult> {
  const res = await fetch(`${API_BASE}/contracts/${contractId}/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nodes }),
  });
  if (!res.ok) throw new Error(`Commit failed (${res.status})`);
  return res.json();
}

// --- Settings client (F01/F01b/F02) ---------------------------------------
// Typed create/list mirroring backend/api/settings.py + models/settings.py.

export type RelationshipType = "counterparty" | "partner" | "licensee" | "other";
export type ClientStatus = "active" | "archived";
export type DealStatus = "active" | "signed" | "closed";
export type DealPosition =
  | "customer"
  | "vendor"
  | "buyer"
  | "seller"
  | "licensor"
  | "licensee"
  | "receiving_party"
  | "disclosing_party";
export type ContractStatus = "drafting" | "under negotiation" | "signed";
// Baseline authorship at first upload (DD-55). Sets Donna's starting redline
// posture; later revisions' source-stance (DD-47) builds on it.
export type Origin = "us" | "our_legal" | "counterparty";

export interface ClientCreate {
  name: string;
  relationship_type?: RelationshipType;
  status?: ClientStatus;
  notes?: string | null;
}
// Partial update: only the fields present are changed (backend ClientUpdate).
export interface ClientUpdate {
  name?: string;
  relationship_type?: RelationshipType;
  status?: ClientStatus;
  notes?: string | null;
}
export interface StoredClient {
  id: string;
  name: string;
  relationship_type: string;
  status: string;
  notes: string | null;
  created_at: string;
}

export interface DealCreate {
  client_id: string;
  name: string;
  description?: string | null;
  status?: DealStatus;
  position?: DealPosition | null;
}
export interface DealUpdate {
  name?: string;
  description?: string | null;
  status?: DealStatus;
  position?: DealPosition | null;
}
export interface StoredDeal {
  id: string;
  client_id: string;
  name: string;
  description: string | null;
  status: string;
  position: string | null;
  created_at: string;
}

export interface ContractTypeCreate {
  name: string;
  is_default?: boolean;
}
export interface ContractTypeUpdate {
  name?: string;
  is_default?: boolean;
}
export interface StoredContractType {
  id: string;
  name: string;
  is_default: boolean;
  created_at: string;
}

export interface ContractCreate {
  client_id: string;
  deal_id: string;
  contract_type_id: string;
  name: string;
  status?: ContractStatus;
  origin?: Origin | null;
  current_version_label?: string | null;
  style_template_id?: string | null;
  style_config?: Record<string, unknown>;
}
export interface StoredContract {
  id: string;
  client_id: string;
  deal_id: string;
  contract_type_id: string;
  name: string;
  status: string;
  origin: string | null;
  current_version_label: string | null;
  style_template_id: string | null;
  style_config: Record<string, unknown>;
  created_at: string;
  // F27 lifecycle badge. Set-based on the LIST read (GET /contracts) so a card can
  // render the badge with no extra call; null on a single GET /contracts/{id}
  // (the cockpit calls /lineage for the full view instead).
  badge: ContractBadge | null;
}

// Carries the HTTP status alongside the `detail` message so callers can branch on
// it (e.g. redline's 409 → "send a clean copy first" hint, not a raw error). Stays
// an Error subclass, so existing `e instanceof Error ? e.message` paths are intact.
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Surface the backend's `detail` (e.g. the 409 FK-guard message) rather than a
// bare status code, so the UI can show "Can't delete: N contracts reference…".
async function errorFrom(res: Response): Promise<Error> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return new ApiError(body.detail, res.status);
  } catch {
    // non-JSON body — fall through to the status-code message
  }
  return new ApiError(`Request failed (${res.status})`, res.status);
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

async function sendJson<T>(method: "POST" | "PATCH", path: string, payload: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

const postJson = <T>(path: string, payload: unknown): Promise<T> => sendJson("POST", path, payload);
const patchJson = <T>(path: string, payload: unknown): Promise<T> =>
  sendJson("PATCH", path, payload);

async function deleteReq(path: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw await errorFrom(res);
}

// DELETE that returns a JSON body (e.g. node delete → { deleted_ids }).
async function deleteJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

export const listClients = (): Promise<StoredClient[]> => getJson("/clients");
export const createClient = (payload: ClientCreate): Promise<StoredClient> =>
  postJson("/clients", payload);
export const updateClient = (id: string, payload: ClientUpdate): Promise<StoredClient> =>
  patchJson(`/clients/${id}`, payload);
export const deleteClient = (id: string): Promise<void> => deleteReq(`/clients/${id}`);

// Backend GET /deals returns all deals; callers filter by client_id.
export const listDeals = (): Promise<StoredDeal[]> => getJson("/deals");
export const createDeal = (payload: DealCreate): Promise<StoredDeal> =>
  postJson("/deals", payload);
export const updateDeal = (id: string, payload: DealUpdate): Promise<StoredDeal> =>
  patchJson(`/deals/${id}`, payload);
export const deleteDeal = (id: string): Promise<void> => deleteReq(`/deals/${id}`);

// --- Operator organization (F25, DD-44) ------------------------------------
// The operator's org identity. A config value (config/.env), not a DB entity, so
// it is read-only here: `editable` is false and there is no write path.
// `export_author` is the name authored on every redline / export — never "Donna".
export interface OperatorOrganization {
  organization_name: string;
  export_author: string;
  editable: boolean;
}

export const getOrganization = (): Promise<OperatorOrganization> => getJson("/organization");

export const listContractTypes = (): Promise<StoredContractType[]> => getJson("/contract-types");
export const createContractType = (payload: ContractTypeCreate): Promise<StoredContractType> =>
  postJson("/contract-types", payload);
export const updateContractType = (
  id: string,
  payload: ContractTypeUpdate,
): Promise<StoredContractType> => patchJson(`/contract-types/${id}`, payload);
export const deleteContractType = (id: string): Promise<void> => deleteReq(`/contract-types/${id}`);

export const createContract = (payload: ContractCreate): Promise<StoredContract> =>
  postJson("/contracts", payload);

export const listContracts = (): Promise<StoredContract[]> => getJson("/contracts");

// Contract edit/rename + delete (delete CASCADES the contract's clauses + issues —
// the UI must confirm first).
export interface ContractUpdate {
  name?: string;
  contract_type_id?: string;
  status?: string;
  origin?: string | null;
}
export const updateContract = (id: string, payload: ContractUpdate): Promise<StoredContract> =>
  patchJson(`/contracts/${id}`, payload);
export const deleteContract = (id: string): Promise<void> => deleteReq(`/contracts/${id}`);

// --- Cockpit: contract tree + issues (Phase 1) ----------------------------
// Mirrors backend/models/imports.py (NodeTreeItem / ContractTreeResponse) and
// backend/models/issues.py (IssueCreate / StoredIssue). The tree is nested and
// carries NO clause number — numbers are derived on the client (DD-02) from the
// clause-role nodes in document order, via lib/numbering.

// A node in the nested read tree. Children are pre-ordered by order_index.
export interface NodeTreeItem {
  id: string;
  order_index: number;
  content_type: string;
  heading: string | null;
  body: string | null;
  table_data: string[][] | null;
  plain_text: string | null;
  role: Role;
  has_placeholder: boolean;
  children: NodeTreeItem[];
}

export interface ContractTreeResponse {
  contract_id: string;
  nodes: NodeTreeItem[];
}

// Who raised the issue. The cockpit's Us/Counterparty toggle maps Us → "operator",
// Counterparty → "counterparty" (the backend default is "operator").
export type Initiator = "operator" | "counterparty";

export interface IssueCreate {
  node_id?: string | null;
  title: string;
  our_position?: string | null;
  their_position?: string | null;
  initiator?: Initiator;
}

// Read-back shape (subset of backend StoredIssue) — the fields the cockpit reads.
export interface StoredIssue {
  id: string;
  contract_id: string;
  node_id: string | null;
  title: string;
  our_position: string | null;
  their_position: string | null;
  status: string;
  initiator: string;
  category: string;
  created_at: string;
}

export const getContractTree = (id: string): Promise<ContractTreeResponse> =>
  getJson(`/contracts/${id}/tree`);

// --- F27: persistent lifecycle badge + version/snapshot lineage (DD-70/DD-48) -
// The badge is the at-a-glance lifecycle state, shown persistently in the cockpit
// header and on every contract card. `label` is the lifecycle phase; `version` is
// the lineage v-number (null for an unsent Working copy — never numbered); `marker`
// is the "edited since sent" drift flag (DD-72); `party` is who the latest send
// went to; `based_on` is a human note of the snapshot the working copy descends
// from (e.g. "v3"). Same shape on `lineage.badge` and on each list contract's badge.
export type BadgeLabel =
  | "Working copy"
  | "Sent to counterparty"
  | "Sent to legal"
  | "Sent to counterparty & legal"
  | "Your move"
  | "Reviewing revision"
  | "Signed";
export type BadgeParty = "counterparty" | "legal" | "both";

export interface ContractBadge {
  label: BadgeLabel | string;
  version: number | null;
  marker: boolean;
  party: BadgeParty | null;
  based_on: string | null;
}

// One row of the version timeline (v1…vN), newest or oldest order as the backend
// returns it. `direction` is whether we sent it out or received it back; `party` is
// the counterpart; `snapshot_id` is the immutable tree captured at that boundary
// (clickable → read-only view); `pointer_labels` are the DD-48 baseline pointers
// that currently point here (mapped to friendly tags in the UI); `is_current_baseline`
// flags the row the redline currently diffs against.
export type LineageDirection = "sent" | "received";

export interface LineageTimelineEntry {
  version: number;
  direction: LineageDirection;
  party: string;
  created_at: string;
  snapshot_id: string;
  pointer_labels: string[];
  is_current_baseline: boolean;
}

// The live working copy, pinned at the top of the lineage view — never a numbered
// version. `diverged_since_last_send` is the DD-72 drift flag (edited since the
// last send), surfaced as "edited since v{n}".
export interface LineageWorkingCopy {
  label: string;
  diverged_since_last_send: boolean;
}

// A reserved (not-yet-populated) lineage slot — a placeholder for a revision that
// will arrive with a future import. Rendered greyed/disabled.
export interface LineageReserved {
  party: string;
  direction: LineageDirection;
  label: string;
  populated: false;
}

export interface LineageView {
  contract_id: string;
  badge: ContractBadge;
  timeline: LineageTimelineEntry[];
  working_copy: LineageWorkingCopy;
  reserved: LineageReserved[];
}

export const getLineage = (id: string): Promise<LineageView> =>
  getJson(`/contracts/${id}/lineage`);

// Read a past snapshot's tree (same shape as GET /contracts/{id}/tree), so the
// existing tree renderer can show it read-only. 404 if the snapshot is missing or
// belongs to another contract.
export const getSnapshotTree = (
  id: string,
  snapshotId: string,
): Promise<ContractTreeResponse> => getJson(`/contracts/${id}/snapshots/${snapshotId}/tree`);

export const listIssues = (contractId: string): Promise<StoredIssue[]> =>
  getJson(`/contracts/${contractId}/issues`);

export const createIssue = (contractId: string, payload: IssueCreate): Promise<StoredIssue> =>
  postJson(`/contracts/${contractId}/issues`, payload);

export type IssueStatus = "open" | "closed";

export const updateIssueStatus = (
  issueId: string,
  status: IssueStatus,
  decision?: Record<string, unknown> | null,
): Promise<StoredIssue> => patchJson(`/issues/${issueId}/status`, { status, decision: decision ?? null });

// Inline edit of an issue's description (DD-67). Only the provided fields update;
// omitted fields are left untouched. Returns the full updated issue.
export interface IssueUpdate {
  title?: string;
  our_position?: string | null;
  their_position?: string | null;
}
export const updateIssue = (issueId: string, payload: IssueUpdate): Promise<StoredIssue> =>
  patchJson(`/issues/${issueId}`, payload);

// --- F11: Donna's issue recommendation (DD-68) -----------------------------
// Donna's issue-scoped advisory draft: a grounded rationale plus the two draftable
// fields (a recommended landing and/or exact counter-language) and the cited ids.
// The draft lives apart from the issue's exported fields until the operator confirms
// ([Use Donna's language]); `missing_benchmark` is her honest "needed a market figure
// I don't have" flag — she recommends the structure, never invents a number.
export interface StoredRecommendation {
  id: string;
  issue_id: string;
  rationale: string;
  draft_recommended_position: string | null;
  draft_counter_language: string | null;
  citations: string[] | null;
  missing_benchmark: boolean;
  model: string;
  generated_at: string;
  confirmed: boolean;
}

// The result of confirming: the draft (or the operator's edited text) was copied into
// the issue's exported fields (DD-68).
export interface RecommendationConfirmResult {
  issue_id: string;
  confirmed: boolean;
  recommended_position: string | null;
  donna_counter_language: string | null;
}

// Optional [Edit] payload — the operator-adjusted language to confirm instead of the
// stored draft. Omitted entirely on a plain [Use].
export interface RecommendationEdit {
  edited_recommended_position?: string | null;
  edited_counter_language?: string | null;
}

const _recPath = (contractId: string, issueId: string): string =>
  `/contracts/${contractId}/issues/${issueId}/recommendation`;

// Generate (or regenerate) the draft. Regenerating replaces the prior draft and resets
// confirmed — async, may take a few seconds at the capable tier.
export const generateRecommendation = (
  contractId: string,
  issueId: string,
): Promise<StoredRecommendation> => postJson(_recPath(contractId, issueId), {});

// The current draft, or null when none exists yet (the 404 the backend returns for a
// never-generated issue is a normal "not yet", not an error).
export async function getRecommendation(
  contractId: string,
  issueId: string,
): Promise<StoredRecommendation | null> {
  try {
    return await getJson<StoredRecommendation>(_recPath(contractId, issueId));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

// [Use Donna's language]: copy the draft (or the operator's edited language) into the
// issue's exported fields. `edit` carries the edited text; omit it for a verbatim copy.
// A plain Use sends NO body (undefined → no request body) so the backend copies the
// stored draft as-is; sending `{}` would be read as an edit that clears both fields.
export const confirmRecommendation = (
  contractId: string,
  issueId: string,
  edit?: RecommendationEdit,
): Promise<RecommendationConfirmResult> =>
  postJson(`${_recPath(contractId, issueId)}/confirm`, edit);

// Conceptual clause search (F-jump): the AI fallback the cockpit fires on Enter
// when an exact keyword query has zero literal matches. Returns the best-matching
// node (or null) so the jump bar can navigate to it and flag it as non-literal.
export interface ClauseSearchResult {
  node_id: string | null;
  matched_text: string | null;
}

export const searchClause = (
  contractId: string,
  query: string,
): Promise<ClauseSearchResult> => postJson(`/contracts/${contractId}/clause-search`, { query });

// --- Cockpit: inline node edit (F08) + node insert (F08b) -------------------
// Both mirror backend/models/imports.py StoredNode (the flat row read-back) and
// backend/models/nodes.py request shapes. Edit rewrites the node's body (else
// heading); insert lands a new node after `after_node_id` within `parent_id`
// (after_node_id=null appends as the last child). Numbers are NOT carried — the
// cockpit re-derives them from tree position (DD-02) after the local mutation.

// A node as read back from the DB (flat row) — the response for both edit + insert.
export interface StoredNode {
  id: string;
  parent_id: string | null;
  order_index: number;
  content_type: string;
  heading: string | null;
  body: string | null;
  table_data: string[][] | null;
  plain_text: string | null;
  role: Role;
  has_placeholder: boolean;
}

export interface NodeCreate {
  parent_id: string | null;
  after_node_id: string | null;
  before_node_id?: string | null;
  text: string;
  role?: string;
}

export const editNode = (
  contractId: string,
  nodeId: string,
  text: string,
): Promise<StoredNode> => patchJson(`/contracts/${contractId}/nodes/${nodeId}`, { text });

export const createNode = (contractId: string, payload: NodeCreate): Promise<StoredNode> =>
  postJson(`/contracts/${contractId}/nodes`, payload);

// F08d "Draft with Donna": a grounded clause draft to pre-fill the insert editor. The
// draft is transient — the operator reviews/edits it and commits via createNode (it is
// never persisted by this call). `body` is the clause language; `heading` an optional
// short title (null for a plain operative clause); empty `body` = Donna couldn't draft.
export interface ClauseDraft {
  heading: string | null;
  body: string;
  citations: string[];
}
export interface ClauseDraftRequest {
  description: string;
  anchor_node_id: string | null;
  mode: "below" | "sub" | "above";
}
export const draftClause = (
  contractId: string,
  payload: ClauseDraftRequest,
): Promise<ClauseDraft> => postJson(`/contracts/${contractId}/nodes/draft`, payload);

// Soft-delete a node and its whole sub-tree (cockpit ⋮ menu). Mirrors backend/
// models/nodes.py NodeDeleteResponse — returns every removed id (the node plus
// all descendants) so the cockpit can drop them from local state and re-derive
// numbers (DD-02).
export interface NodeDeleteResponse {
  deleted_ids: string[];
}

export const deleteNode = (
  contractId: string,
  nodeId: string,
): Promise<NodeDeleteResponse> => deleteJson(`/contracts/${contractId}/nodes/${nodeId}`);

// Reparent + reposition a clause (carrying its whole sub-tree) anywhere in the
// tree — the cockpit's Rearrange (drag-and-drop) target. `parent_id` is the new
// parent (null = top level); the anchor is `after_node_id` OR `before_node_id`
// (mutually exclusive; neither = append as the parent's last child). Rejects a
// cycle (moving into own sub-tree) with 422 → surfaced via `detail`. `moved:false`
// means the position was unchanged. Numbers re-derive from position, so the
// cockpit re-fetches the tree on a real move (DD-02).
export interface NodeMoveRequest {
  parent_id: string | null;
  after_node_id: string | null;
  before_node_id: string | null;
}
export interface NodeMoveResponse {
  moved: boolean;
  node_id: string;
  parent_id: string | null;
}

export const moveNode = (
  contractId: string,
  nodeId: string,
  body: NodeMoveRequest,
): Promise<NodeMoveResponse> =>
  postJson(`/contracts/${contractId}/nodes/${nodeId}/move`, body);

// --- Cockpit: defined terms (F05 hover-to-define, unblocked by F16) ----------
// Mirrors the backend GET /contracts/{id}/defined-terms response. `definition` is
// null when the term was introduced but no "means" clause was captured;
// `source_node_id` is the node the term was defined in (may be null).
export interface DefinedTerm {
  id: string;
  deal_id: string;
  term: string;
  definition: string | null;
  source_node_id: string | null;
}

export interface DefinedTermsResponse {
  deal_id: string;
  terms: DefinedTerm[];
}

export const getDefinedTerms = (contractId: string): Promise<DefinedTermsResponse> =>
  getJson(`/contracts/${contractId}/defined-terms`);

// --- Cockpit: document export (SPEC §9, DD-71) ------------------------------
// These endpoints stream a .docx (binary), not JSON, so they bypass the JSON
// helpers above. DD-71: export is a pure grab — it cuts NO snapshot and advances
// NO pointer (the snapshot/pointer boundary is the separate Mark-as-sent action,
// `markSent` below). There is no recipient selector on export anymore.

// Pull the server-set name out of Content-Disposition (RFC 5987 filename*= wins
// over a plain filename=); fall back to the caller's default when absent.
function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^["']|["']$/g, ""));
    } catch {
      // malformed encoding — fall through to the plain filename
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : fallback;
}

// Fetch a .docx stream and trigger a browser download. Surfaces the backend
// `detail` on failure (same contract as errorFrom). The object URL is revoked
// right after the click so it doesn't leak.
async function downloadDocx(
  method: "GET" | "POST",
  path: string,
  fallbackName: string,
  payload?: unknown,
): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    ...(payload === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  });
  if (!res.ok) throw await errorFrom(res);
  const blob = await res.blob();
  const name = filenameFromDisposition(res.headers.get("Content-Disposition"), fallbackName);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Clean copy: POST renders the current working copy and streams the .docx (a pure
// grab — no snapshot, no pointer; DD-71). POST (not GET) only because it stamps
// `last_export_at` server-side for the Mark-as-sent drift marker (DD-72). No body.
export const exportCleanCopy = (contractId: string): Promise<void> =>
  downloadDocx("POST", `/contracts/${contractId}/export`, "contract.docx");

// Issue list: GET streams the call's issue list as a .docx.
export const exportIssueList = (contractId: string): Promise<void> =>
  downloadDocx("GET", `/contracts/${contractId}/issue-list/export`, "issue-list.docx");

// Redline: POST cuts a tracked-changes .docx of the working tree against the
// baseline snapshot (the last clean copy sent to the counterparty). snapshot_id:null
// targets that default baseline. Throws an ApiError with status 409 when no baseline
// exists yet (nothing has been sent) — the cockpit turns that into the friendly
// "send a clean copy first" hint rather than a raw error.
export const exportRedline = (contractId: string): Promise<void> =>
  downloadDocx("POST", `/contracts/${contractId}/redline-export`, "redline.docx", {
    snapshot_id: null,
  });

// --- Mark as sent (DD-71): the boundary event, decoupled from export ---------
// The app can't actually send — the operator exports, sends manually, then records
// it here. Marking cuts a snapshot of the CURRENT working copy, advances the DD-48
// `last_shared_with_X` pointer(s) (recipient "both" → one snapshot, two pointers),
// and mints the next lineage v-number (DD-70). When the working copy was edited
// since the last export, the first call returns `marked:false, drift:true` WITHOUT
// cutting (the non-blocking DD-72 warning); re-call with `acknowledge_drift:true`
// to proceed ("Mark anyway").
export type MarkSentRecipient = "counterparty" | "legal" | "both";

export interface MarkSentResult {
  marked: boolean;
  drift: boolean;
  recipient: MarkSentRecipient;
  version: number;
  pointers: string[];
  snapshot_id: string | null;
  last_export_at: string | null;
}

export const markSent = (
  contractId: string,
  recipient: MarkSentRecipient,
  acknowledgeDrift = false,
): Promise<MarkSentResult> =>
  postJson(`/contracts/${contractId}/mark-sent`, {
    recipient,
    acknowledge_drift: acknowledgeDrift,
  });

// --- Donna tab: context-aware grounded chat (F10b, SPEC §9) ----------------
// `kind` is the PERSISTED answer-treatment carried on stored thread messages
// (F10 persistence): a normal `answer`, an honest `not_found`, or a `deflected`
// (out-of-scope) reply. Live asks now return the richer `mode` below; `kind`
// stays the rehydration vocabulary for reloaded turns.
export type DonnaAnswerKind = "answer" | "not_found" | "deflected";

// Live ask result (F10b): Donna is context-aware. With a clause/issue in context
// she advises / drafts grounded on it; without context she's read-and-explain.
//   explain        = read-and-explain answer (the old F10 behaviour)
//   advise         = a positional recommendation
//   draft          = `draft_language` carries clause language to apply
//   legal_referral = "get a lawyer" — Donna will NOT opine
//   need_context   = a soft nudge to select a clause / open an issue first
export type DonnaChatMode = "explain" | "advise" | "draft" | "legal_referral" | "need_context";

// The active context sent with an ask: the clause node(s) and/or the issue Donna
// should ground on. Optional — absent = open read-and-explain. An issue in context
// auto-includes its clause (the cockpit fills `node_ids`).
export interface DonnaAskContext {
  node_ids: string[];
  issue_id?: string | null;
}

// Live ask response (REPLACES the old {answer,citations,deflected,kind} shape).
// `citations` are node ids (and may include issue ids); the cockpit resolves each
// to a clickable clause chip. `draft_language` is non-null only on `mode === "draft"`.
export interface DonnaChatResponse {
  reply: string;
  mode: DonnaChatMode;
  citations: string[];
  draft_language: string | null;
}

// Persistent per-contract thread (DD-40). Assistant turns persist their answer
// treatment (`kind`) + cited ids (`citations`), so loaded history rehydrates the same
// chips + kind styling a fresh ask renders. User turns / pre-migration rows carry
// neither (null) and render as plain grounded answers.
export type DonnaMessageRole = "user" | "assistant";

export interface DonnaThreadMessage {
  role: DonnaMessageRole;
  content: string;
  kind?: DonnaAnswerKind | null;
  citations?: string[] | null;
}

export interface DonnaThread {
  conversation_id: string;
  running_summary: string | null;
  messages: DonnaThreadMessage[];
}

export const getDonnaThread = (contractId: string): Promise<DonnaThread> =>
  getJson(`/contracts/${contractId}/donna/thread`);

// --- Brainstorm overlay (F10b, DD-73/DD-77): stateless ephemeral exploration ---
// The client holds the running transcript and replays it each turn; the backend
// persists NOTHING until close. On close Donna distils one compact summary onto the
// issue (table `brainstorm_summaries`), or returns 204 when nothing was substantive.

// One prior exchange in the running transcript: the operator's message + Donna's reply.
export interface BrainstormTurn {
  question: string;
  answer: string;
}

export interface BrainstormTurnRequest {
  issue_id: string;
  turns: BrainstormTurn[];
  message: string;
}

export interface BrainstormTurnResponse {
  reply: string;
  citations: string[];
}

export interface BrainstormCloseRequest {
  issue_id: string;
  turns: BrainstormTurn[];
}

// One distilled brainstorm pass stored on the issue. `created_at` is an ISO timestamp.
export interface StoredBrainstormSummary {
  id: string;
  issue_id: string;
  question: string | null;
  conclusion: string | null;
  fallbacks: string | null;
  created_at: string;
}

export interface BrainstormSummariesResponse {
  summaries: StoredBrainstormSummary[];
}

export const brainstormTurn = (
  contractId: string,
  body: BrainstormTurnRequest,
): Promise<BrainstormTurnResponse> =>
  postJson(`/contracts/${contractId}/donna/brainstorm`, body);

// Close distils ONE summary, or 204 (nothing substantive) → null. A 429 surfaces as
// ApiError like every other call, so the overlay can show the rate-limit line.
export async function closeBrainstorm(
  contractId: string,
  body: BrainstormCloseRequest,
): Promise<StoredBrainstormSummary | null> {
  const res = await fetch(`${API_BASE}/contracts/${contractId}/donna/brainstorm/close`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 204) return null;
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

export const getBrainstormSummaries = (
  issueId: string,
): Promise<BrainstormSummariesResponse> =>
  getJson(`/issues/${issueId}/brainstorm-summaries`);

// Context is optional: omitted when there's no active selection (JSON.stringify
// drops the undefined key), so an open ask sends just `{ question }`.
export const askDonna = (
  contractId: string,
  question: string,
  context?: DonnaAskContext,
): Promise<DonnaChatResponse> =>
  postJson(`/contracts/${contractId}/donna/ask`, { question, context });

// Map an ask/thread failure to an operator-facing line. A 429 (rate limit) gets a
// friendly "catching her breath" message; everything else surfaces the backend
// `detail` carried on ApiError, with a plain fallback.
export function donnaErrorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 429) {
    return "Donna's catching her breath — give it a moment and ask again.";
  }
  if (e instanceof Error && e.message) return e.message;
  return "Donna couldn't answer just now. Try again.";
}

// --- Mode B revision import + review (F03b/F03c) ----------------------------
// Mirrors backend/models/revision_import.py + revision_review.py. F03b ingests a
// clean counterparty/legal .docx revision against the last-sent baseline and stages
// the matcher's buckets; F03c is the two-phase review (DD-78): a structural-
// foundation pass (abstain match-confirm) before a document-ordered content stream.

export type RevisionSource = "counterparty" | "legal";
export type ParsePath = "tracked_changes" | "clean_diff";
export type RevisionHunkType = "insertion" | "deletion" | "replacement";
export type Significance = "trivial" | "substantive";
export type ChangeStatus = "pending" | "partial" | "complete";
// Derived per-change classification (DD-78): an edit, a counterparty-added node, a
// counterparty-deleted node, or an unsettled low-confidence match (Phase-1 only).
export type ChangeKind = "edited" | "new" | "deleted" | "abstain";
export type StoredHunkVerdict = "pending" | "accepted" | "rejected" | "modified";
// Operator-facing action vocabularies (mapped onto StoredHunkVerdict server-side).
export type HunkDecisionAction = "accept" | "counter" | "edit" | "keep";
export type NodeDecisionAction = "accept" | "reject" | "edit";
export type MatchConfirmAction = "confirm" | "new" | "rematch";

// The receipt returned on a successful clean-diff import (F03b). `version` is the
// as-received snapshot's lineage v-number; the bucket counts mirror the matcher.
export interface RevisionImportResponse {
  session_id: string;
  contract_id: string;
  source: string;
  parse_path: ParsePath;
  baseline_snapshot_id: string;
  as_received_snapshot_id: string;
  received_pointer_party: string;
  version: number;
  status: string;
  changes_count: number;
  hunk_count: number;
  edited_matches: number;
  unchanged_matches: number;
  new: number;
  deleted: number;
  abstains: number;
}

export interface StoredRevisionSession {
  id: string;
  contract_id: string;
  baseline_snapshot_id: string;
  source: string;
  source_filename: string | null;
  parse_path: ParsePath;
  status: string;
  changes_count: number;
  changes_reviewed_count: number;
  // Derived: changes still to decide (status != 'complete') — the resume "N pending".
  pending_changes: number;
  imported_at: string;
}

// One decision unit. For an edited change each hunk is a text edit (DD-27 four
// actions); for new/deleted a single whole-node hunk carries the added/removed body.
// `donna_*` are her staged read + exact counter-language; `final_text` is the
// resolved language once decided (edit/counter).
export interface ReviewHunk {
  id: string;
  change_id: string;
  hunk_type: RevisionHunkType;
  significance: Significance;
  position_in_body: number | null;
  original_text: string | null;
  proposed_text: string | null;
  donna_verdict: string | null;
  donna_counter_text: string | null;
  verdict: StoredHunkVerdict;
  final_text: string | null;
}

// Structural context for one side (baseline/incoming) of a review change (F03c UX).
// `number`/`heading` = clause identity; `breadcrumb` = the section it sits under;
// `body` = the FULL clause text the hunk offsets index into (so an edited diff renders
// in place); `children_preview`/`prev_label`/`next_label` = what's under/beside it.
// `found` is false when the side has no resolvable node (the other fields then empty).
export interface ChangeContextSide {
  side: "their" | "baseline";
  found: boolean;
  number: string | null;
  heading: string | null;
  breadcrumb: string[];
  children_preview: string[];
  body: string | null;
  prev_label: string | null;
  next_label: string | null;
}

// Both sides of a change's structural context. edited/deleted populate `baseline`
// (located by node_id); new populates `their`; abstain populates both.
export interface ChangeContext {
  their: ChangeContextSide;
  baseline: ChangeContextSide;
}

// One navigation unit + its hunks and derived `change_kind`. For an abstain,
// `proposed_parent_id` carries the provisional baseline candidate and
// `match_confidence` its score; for a new node `proposed_order_index` its position.
// `context` is read-only structural enrichment, populated for EVERY change.
export interface ReviewChange {
  id: string;
  session_id: string;
  change_kind: ChangeKind;
  node_id: string | null;
  proposed_parent_id: string | null;
  proposed_order_index: number | null;
  match_confidence: number | null;
  hunk_count: number;
  hunks_decided: number;
  status: ChangeStatus;
  hunks: ReviewHunk[];
  context: ChangeContext | null;
}

// A residual 6a tree-shape anomaly. F03b stages none yet → always empty.
export interface TreeAnomaly {
  node_id: string;
  reason: string;
}

export interface ReviewPhase1 {
  abstains: ReviewChange[];
  tree_anomalies: TreeAnomaly[];
}

export interface ReviewPayload {
  session: StoredRevisionSession;
  phase1: ReviewPhase1;
  phase2: ReviewChange[];
}

export interface ConfirmMatchRequest {
  action: MatchConfirmAction;
  baseline_node_id?: string | null;
}
export interface HunkDecideRequest {
  verdict: HunkDecisionAction;
  final_text?: string | null;
}
export interface NodeDecideRequest {
  verdict: NodeDecisionAction;
  final_text?: string | null;
}

// Receipt for apply: what landed where (F08 paths) + which rejections seeded issues.
export interface ApplyResult {
  session_id: string;
  status: string;
  edits_applied: number;
  nodes_inserted: number;
  nodes_deleted: number;
  issues_created: number;
  issue_ids: string[];
}

// F03b entry: the clean revision .docx is the raw request body (same upload
// convention as previewDocx); `source` + `filename` ride the query string. Surfaces
// the typed backend errors via ApiError — 422 (tracked changes, Path A not built),
// 409 (no baseline / a session already open) — so the cockpit can branch on status.
export async function importRevision(
  contractId: string,
  source: RevisionSource,
  file: File,
): Promise<RevisionImportResponse> {
  const qs = new URLSearchParams({ source, filename: file.name });
  const res = await fetch(`${API_BASE}/contracts/${contractId}/revisions/import?${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: await file.arrayBuffer(),
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

export const listRevisionSessions = (contractId: string): Promise<StoredRevisionSession[]> =>
  getJson(`/contracts/${contractId}/revisions/sessions`);

export const getRevisionReview = (sessionId: string): Promise<ReviewPayload> =>
  getJson(`/revisions/sessions/${sessionId}`);

// 6b abstain resolution. `rematch` carries the operator-chosen `baseline_node_id`;
// returns the reclassified change (resolving one abstain can churn the stream, so the
// caller re-fetches the payload).
export const confirmMatch = (
  changeId: string,
  body: ConfirmMatchRequest,
): Promise<ReviewChange> => postJson(`/revisions/changes/${changeId}/confirm-match`, body);

// DD-27 four-action hunk verdict. `counter` uses the staged donna_counter_text (422
// if none); `edit` requires `final_text`. Returns the rolled-up change.
export const decideHunk = (hunkId: string, body: HunkDecideRequest): Promise<ReviewChange> =>
  postJson(`/revisions/hunks/${hunkId}/decide`, body);

// Whole-node decision for a new/deleted change. `edit` requires `final_text`.
export const decideNode = (changeId: string, body: NodeDecideRequest): Promise<ReviewChange> =>
  postJson(`/revisions/changes/${changeId}/decide-node`, body);

// Apply every decision to the working copy (409 if any change is still undecided).
export const applyRevisionSession = (sessionId: string): Promise<ApplyResult> =>
  postJson(`/revisions/sessions/${sessionId}/apply`, {});
