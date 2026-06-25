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

// --- Cockpit: document export (SPEC §9) -------------------------------------
// These endpoints stream a .docx (binary), not JSON, so they bypass the JSON
// helpers above. Each cuts a server-side snapshot and hands the browser a file
// to save. The recipient on a clean copy sets the version pointer server-side.
export type ExportRecipient = "counterparty" | "legal" | "internal" | "copy_only";

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

// Clean copy: POST cuts a snapshot for the chosen recipient and streams the .docx.
export const exportCleanCopy = (contractId: string, recipient: ExportRecipient): Promise<void> =>
  downloadDocx("POST", `/contracts/${contractId}/export`, "contract.docx", { recipient });

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

// --- Donna tab: single-contract grounded Q&A (F10, SPEC §9) ----------------
// Read-and-explain only — Donna locates / explains / status-briefs over THIS
// contract's nodes + issue ledger; she never advises (DD-14). `citations` are
// node ids (and may include issue ids) the answer drew from; the cockpit resolves
// each to a clickable clause chip. `kind` drives the answer treatment: a normal
// `answer`, an honest `not_found`, or a `deflected` (out-of-scope) reply.
export type DonnaAnswerKind = "answer" | "not_found" | "deflected";

export interface DonnaAnswer {
  answer: string;
  citations: string[];
  kind: DonnaAnswerKind;
  deflected: boolean;
}

// Persistent per-contract thread (DD-40). Stored messages carry only role + text;
// citation chips + `kind` styling are live on a fresh ask, so loaded history
// renders as plain grounded answers.
export type DonnaMessageRole = "user" | "assistant";

export interface DonnaThreadMessage {
  role: DonnaMessageRole;
  content: string;
}

export interface DonnaThread {
  conversation_id: string;
  running_summary: string | null;
  messages: DonnaThreadMessage[];
}

export const getDonnaThread = (contractId: string): Promise<DonnaThread> =>
  getJson(`/contracts/${contractId}/donna/thread`);

export const askDonna = (contractId: string, question: string): Promise<DonnaAnswer> =>
  postJson(`/contracts/${contractId}/donna/ask`, { question });

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
