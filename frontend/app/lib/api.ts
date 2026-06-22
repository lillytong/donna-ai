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

export interface ClientCreate {
  name: string;
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
  current_version_label: string | null;
  style_template_id: string | null;
  style_config: Record<string, unknown>;
  created_at: string;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

export const listClients = (): Promise<StoredClient[]> => getJson("/clients");
export const createClient = (payload: ClientCreate): Promise<StoredClient> =>
  postJson("/clients", payload);

// Backend GET /deals returns all deals; callers filter by client_id.
export const listDeals = (): Promise<StoredDeal[]> => getJson("/deals");
export const createDeal = (payload: DealCreate): Promise<StoredDeal> =>
  postJson("/deals", payload);

export const listContractTypes = (): Promise<StoredContractType[]> => getJson("/contract-types");
export const createContractType = (payload: ContractTypeCreate): Promise<StoredContractType> =>
  postJson("/contract-types", payload);

export const createContract = (payload: ContractCreate): Promise<StoredContract> =>
  postJson("/contracts", payload);
