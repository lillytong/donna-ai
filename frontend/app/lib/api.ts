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

// Surface the backend's `detail` (e.g. the 409 FK-guard message) rather than a
// bare status code, so the UI can show "Can't delete: N contracts reference…".
async function errorFrom(res: Response): Promise<Error> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return new Error(body.detail);
  } catch {
    // non-JSON body — fall through to the status-code message
  }
  return new Error(`Request failed (${res.status})`);
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

// Contract edit/rename + delete (delete CASCADES the contract's clauses + issues +
// comments — the UI must confirm first).
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

export type IssueStatus = "open" | "agreed" | "deferred" | "kicked" | "dismissed";

export const updateIssueStatus = (
  issueId: string,
  status: IssueStatus,
  decision?: Record<string, unknown> | null,
): Promise<StoredIssue> => patchJson(`/issues/${issueId}/status`, { status, decision: decision ?? null });

// Append-only comment thread on an issue (F09). actor is "user" for operator entries.
export type CommentActor = "user" | "ai" | "principal";

export interface StoredComment {
  id: string;
  issue_id: string;
  actor: string;
  content: string;
  created_at: string;
}

export const listComments = (issueId: string): Promise<StoredComment[]> =>
  getJson(`/issues/${issueId}/comments`);

export const addComment = (
  issueId: string,
  payload: { actor: CommentActor; content: string },
): Promise<StoredComment> => postJson(`/issues/${issueId}/comments`, payload);

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
