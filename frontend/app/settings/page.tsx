"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "./settings.module.css";
import {
  createClient,
  createContractType,
  createDeal,
  deleteClient,
  deleteContractType,
  deleteDeal,
  getOrganization,
  listClients,
  listContractTypes,
  listDeals,
  updateClient,
  updateContractType,
  updateDeal,
  type ClientStatus,
  type DealPosition,
  type DealStatus,
  type OperatorOrganization,
  type RelationshipType,
  type StoredClient,
  type StoredContractType,
  type StoredDeal,
} from "../lib/api";

type Tab = "clients" | "deals" | "types" | "organization";

// Enum values mirror backend/models/settings.py exactly — never invented here.
const RELATIONSHIP_OPTIONS: { value: RelationshipType; label: string }[] = [
  { value: "counterparty", label: "Counterparty" },
  { value: "partner", label: "Partner" },
  { value: "licensee", label: "Licensee" },
  { value: "other", label: "Other" },
];

const CLIENT_STATUS_OPTIONS: { value: ClientStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
];

const DEAL_STATUS_OPTIONS: { value: DealStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "signed", label: "Signed" },
  { value: "closed", label: "Closed" },
];

const POSITION_OPTIONS: { value: DealPosition; label: string }[] = [
  { value: "customer", label: "Customer" },
  { value: "vendor", label: "Vendor" },
  { value: "buyer", label: "Buyer" },
  { value: "seller", label: "Seller" },
  { value: "licensor", label: "Licensor" },
  { value: "licensee", label: "Licensee" },
  { value: "receiving_party", label: "Receiving party" },
  { value: "disclosing_party", label: "Disclosing party" },
];

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function humanize(value: string | null): string {
  if (!value) return "";
  return value.replace(/_/g, " ");
}

function EditIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("clients");

  const [clients, setClients] = useState<StoredClient[]>([]);
  const [deals, setDeals] = useState<StoredDeal[]>([]);
  const [types, setTypes] = useState<StoredContractType[]>([]);
  const [org, setOrg] = useState<OperatorOrganization | null>(null);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listClients(), listDeals(), listContractTypes(), getOrganization()])
      .then(([c, d, t, o]) => {
        setClients(c);
        setDeals(d);
        setTypes(t);
        setOrg(o);
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : "Could not load settings"))
      .finally(() => setLoading(false));
  }, []);

  const reloadClients = async () => setClients(await listClients());
  const reloadDeals = async () => setDeals(await listDeals());
  const reloadTypes = async () => setTypes(await listContractTypes());

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "clients", label: "Clients", count: clients.length },
    { id: "deals", label: "Deals", count: deals.length },
    { id: "types", label: "Contract types", count: types.length },
    { id: "organization", label: "Your Organization" },
  ];

  return (
    <div className={styles.screen}>
      <main className={styles.wrap}>
        <h1 className={styles.pageTitle}>Settings</h1>
        <p className={styles.pageLead}>
          Manage the clients, deals, and contract types that contracts are filed under. These are
          available the moment you start a new import.
        </p>

        <div className={styles.tabs} role="tablist" aria-label="Settings sections">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              className={[styles.tab, tab === t.id ? styles.tabActive : ""].join(" ")}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.count !== undefined && <span className={styles.tabCount}>{t.count}</span>}
            </button>
          ))}
        </div>

        {loading ? (
          <div className={styles.loading} aria-live="polite">
            <div className={styles.progressTrack}>
              <div className={styles.progressBar} />
            </div>
            <span className={styles.loadingLabel}>Loading clients, deals, and types…</span>
          </div>
        ) : loadError ? (
          <p className={styles.loadError} role="alert">
            {loadError}. Check that the API is running, then reload.
          </p>
        ) : (
          <>
            {tab === "clients" && <ClientsSection clients={clients} onCreated={reloadClients} />}
            {tab === "deals" && (
              <DealsSection clients={clients} deals={deals} onCreated={reloadDeals} />
            )}
            {tab === "types" && <ContractTypesSection types={types} onCreated={reloadTypes} />}
            {tab === "organization" && <OrganizationSection org={org} />}
          </>
        )}
      </main>
    </div>
  );
}

/* ---- Your Organization (F25, DD-44) ----
   Read-only: the org identity is a config value (config/.env), not a DB entity, so
   there is no save path here. It authors every redline / export — never "Donna". */

function OrganizationSection({ org }: { org: OperatorOrganization | null }) {
  const hasName = !!org && org.organization_name.trim() !== "";
  return (
    <div
      className={styles.orgPanel}
      role="tabpanel"
      id="panel-organization"
      aria-labelledby="tab-organization"
    >
      <div className={styles.orgCard}>
        <h2 className={styles.formTitle}>Your Organization</h2>
        <p className={styles.formLead}>
          The name authored on every redline and exported document. It is what the
          counterparty sees on each tracked change — never &ldquo;Donna&rdquo;.
        </p>

        <div className={styles.field}>
          <label className={styles.label}>Organization name</label>
          <div
            className={[styles.orgValue, hasName ? "" : styles.orgValueMuted].join(" ")}
          >
            {hasName ? org!.organization_name : org ? `Not set — exports use “${org.export_author}”` : "—"}
          </div>
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Document author</label>
          <div className={styles.orgValue}>{org ? org.export_author : "—"}</div>
        </div>

        <div className={styles.orgNote}>
          <span>
            This is a configuration value, not an editable record. Set it per deployment
            via <code>DONNA_OPERATOR_ORG_NAME</code> in your environment, then restart the
            app. If unset, exports are authored as “{org ? org.export_author : "Operator Organization"}”.
          </span>
        </div>
      </div>
    </div>
  );
}

/* ---- Clients (F01) ---- */

function ClientsSection({
  clients,
  onCreated,
}: {
  clients: StoredClient[];
  onCreated: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [relationship, setRelationship] = useState<RelationshipType>("counterparty");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim() === "" || submitting) return;
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      await createClient({
        name: name.trim(),
        relationship_type: relationship,
        notes: notes.trim() || null,
      });
      await onCreated();
      setName("");
      setRelationship("counterparty");
      setNotes("");
      setDone("Client added.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the client");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.layout} role="tabpanel" id="panel-clients" aria-labelledby="tab-clients">
      <section>
        <div className={styles.listHead}>
          Clients
          <span className={styles.listHint}>Counterparties and partners you negotiate with</span>
        </div>
        {clients.length === 0 ? (
          <p className={styles.empty}>No clients yet. Add your first one to start filing contracts.</p>
        ) : (
          <div className={styles.list}>
            {clients.map((c) => (
              <ClientRow key={c.id} client={c} onChanged={onCreated} />
            ))}
          </div>
        )}
      </section>

      <aside>
        <form className={styles.formCard} onSubmit={onSubmit}>
          <h2 className={styles.formTitle}>New client</h2>
          <p className={styles.formLead}>Files all of this client&apos;s deals and contracts.</p>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="client-name">
              Name
            </label>
            <input
              id="client-name"
              className={styles.control}
              placeholder="e.g. Acme Corporation"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="client-relationship">
              Relationship type
            </label>
            <select
              id="client-relationship"
              className={styles.control}
              value={relationship}
              onChange={(e) => setRelationship(e.target.value as RelationshipType)}
            >
              {RELATIONSHIP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="client-notes">
              Notes <span className={styles.optional}>· optional</span>
            </label>
            <textarea
              id="client-notes"
              className={[styles.control, styles.textarea].join(" ")}
              placeholder="Anything worth remembering about this client"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          {done && !error && <p className={styles.success}>{done}</p>}

          <button className={styles.submit} type="submit" disabled={name.trim() === "" || submitting}>
            {submitting ? "Adding…" : "Add client"}
          </button>
          {submitting && <div className={styles.submitBar} />}
        </form>
      </aside>
    </div>
  );
}

function ClientRow({
  client,
  onChanged,
}: {
  client: StoredClient;
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [name, setName] = useState(client.name);
  const [relationship, setRelationship] = useState<RelationshipType>(
    client.relationship_type as RelationshipType,
  );
  const [status, setStatus] = useState<ClientStatus>(client.status as ClientStatus);
  const [notes, setNotes] = useState(client.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setName(client.name);
    setRelationship(client.relationship_type as RelationshipType);
    setStatus(client.status as ClientStatus);
    setNotes(client.notes ?? "");
    setError(null);
    setMode("edit");
  }

  async function save() {
    if (name.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateClient(client.id, {
        name: name.trim(),
        relationship_type: relationship,
        status,
        notes: notes.trim() || null,
      });
      await onChanged();
      setMode("view");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save changes");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteClient(client.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this client");
      setMode("view");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "edit") {
    return (
      <div className={styles.editRow}>
        <div className={styles.editGrid}>
          <div className={styles.editField}>
            <label className={styles.label} htmlFor={`client-name-${client.id}`}>
              Name
            </label>
            <input
              id={`client-name-${client.id}`}
              className={styles.control}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className={styles.editField}>
            <label className={styles.label} htmlFor={`client-rel-${client.id}`}>
              Relationship
            </label>
            <select
              id={`client-rel-${client.id}`}
              className={styles.control}
              value={relationship}
              onChange={(e) => setRelationship(e.target.value as RelationshipType)}
            >
              {RELATIONSHIP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className={styles.editField}>
            <label className={styles.label} htmlFor={`client-status-${client.id}`}>
              Status
            </label>
            <select
              id={`client-status-${client.id}`}
              className={styles.control}
              value={status}
              onChange={(e) => setStatus(e.target.value as ClientStatus)}
            >
              {CLIENT_STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className={styles.editField}>
          <label className={styles.label} htmlFor={`client-notes-${client.id}`}>
            Notes <span className={styles.optional}>· optional</span>
          </label>
          <textarea
            id={`client-notes-${client.id}`}
            className={[styles.control, styles.textarea].join(" ")}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        {error && (
          <p className={styles.rowError} role="alert">
            {error}
          </p>
        )}
        <div className={styles.editActions}>
          <span className={styles.editSpacer} />
          <button className={styles.btnGhost} type="button" onClick={() => setMode("view")} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnPrimary}
            type="button"
            onClick={save}
            disabled={name.trim() === "" || busy}
          >
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.item}>
      <div className={styles.itemMain}>
        <div className={styles.itemName}>{client.name}</div>
        {client.notes && <div className={styles.itemMeta}>{client.notes}</div>}
        {error && (
          <div className={styles.rowError} role="alert">
            {error}
          </div>
        )}
      </div>
      <div className={styles.pills}>
        <span className={styles.pill}>{humanize(client.relationship_type)}</span>
        <span
          className={[
            styles.pill,
            client.status === "active" ? styles.pillActive : styles.pillNeutral,
          ].join(" ")}
        >
          {client.status}
        </span>
      </div>
      {mode === "confirm" ? (
        <div className={styles.confirm}>
          <span className={styles.confirmText}>Delete?</span>
          <button className={styles.btnGhost} type="button" onClick={() => setMode("view")} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnDanger}
            type="button"
            onClick={remove}
            disabled={busy}
            aria-label={`Confirm delete ${client.name}`}
          >
            {busy ? "Deleting…" : "Delete"}
          </button>
        </div>
      ) : (
        <div className={styles.rowActions}>
          <button
            className={[styles.rowBtn, styles.rowBtnEdit].join(" ")}
            type="button"
            onClick={startEdit}
            aria-label={`Edit ${client.name}`}
            title={`Edit ${client.name}`}
          >
            <EditIcon />
          </button>
          <button
            className={[styles.rowBtn, styles.rowBtnDanger].join(" ")}
            type="button"
            onClick={() => {
              setError(null);
              setMode("confirm");
            }}
            aria-label={`Delete ${client.name}`}
            title={`Delete ${client.name}`}
          >
            <TrashIcon />
          </button>
        </div>
      )}
      <span className={styles.itemDate}>{formatDate(client.created_at)}</span>
    </div>
  );
}

/* ---- Deals (F02) — grouped under their client (SPEC §9) ---- */

function DealsSection({
  clients,
  deals,
  onCreated,
}: {
  clients: StoredClient[];
  deals: StoredDeal[];
  onCreated: () => Promise<void>;
}) {
  const [clientId, setClientId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [position, setPosition] = useState<DealPosition | "">("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const grouped = useMemo(
    () =>
      clients
        .map((c) => ({ client: c, deals: deals.filter((d) => d.client_id === c.id) }))
        .filter((g) => g.deals.length > 0),
    [clients, deals],
  );

  const noClients = clients.length === 0;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (clientId === "" || name.trim() === "" || submitting) return;
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      await createDeal({
        client_id: clientId,
        name: name.trim(),
        description: description.trim() || null,
        position: position || null,
      });
      await onCreated();
      setName("");
      setDescription("");
      setPosition("");
      setDone("Deal added.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the deal");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.layout} role="tabpanel" id="panel-deals" aria-labelledby="tab-deals">
      <section>
        <div className={styles.listHead}>
          Deals
          <span className={styles.listHint}>Negotiation umbrellas, grouped by client</span>
        </div>
        {grouped.length === 0 ? (
          <p className={styles.empty}>
            No deals yet. Pick a client and add a deal to group its contracts.
          </p>
        ) : (
          grouped.map((g) => (
            <div key={g.client.id} className={styles.group}>
              <div className={styles.groupHead}>
                <span className={styles.groupName}>{g.client.name}</span>
                <span className={styles.groupCount}>
                  {g.deals.length} {g.deals.length === 1 ? "deal" : "deals"}
                </span>
              </div>
              <div className={styles.groupBody}>
                {g.deals.map((d) => (
                  <DealRow key={d.id} deal={d} onChanged={onCreated} />
                ))}
              </div>
            </div>
          ))
        )}
      </section>

      <aside>
        <form className={styles.formCard} onSubmit={onSubmit}>
          <h2 className={styles.formTitle}>New deal</h2>
          <p className={styles.formLead}>The scope for shared terms across its contracts.</p>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="deal-client">
              Client
            </label>
            <select
              id="deal-client"
              className={styles.control}
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              disabled={noClients}
              required
            >
              <option value="">{noClients ? "Add a client first" : "Select a client…"}</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="deal-name">
              Name
            </label>
            <input
              id="deal-name"
              className={styles.control}
              placeholder="e.g. 2026 Technology Licence"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={noClients}
              required
            />
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="deal-position">
              Our position <span className={styles.optional}>· optional</span>
            </label>
            <select
              id="deal-position"
              className={styles.control}
              value={position}
              onChange={(e) => setPosition(e.target.value as DealPosition | "")}
              disabled={noClients}
            >
              <option value="">Not set</option>
              {POSITION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="deal-description">
              Description <span className={styles.optional}>· optional</span>
            </label>
            <textarea
              id="deal-description"
              className={[styles.control, styles.textarea].join(" ")}
              placeholder="What this deal covers"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={noClients}
            />
          </div>

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          {done && !error && <p className={styles.success}>{done}</p>}

          <button
            className={styles.submit}
            type="submit"
            disabled={noClients || clientId === "" || name.trim() === "" || submitting}
          >
            {submitting ? "Adding…" : "Add deal"}
          </button>
          {submitting && <div className={styles.submitBar} />}
        </form>
      </aside>
    </div>
  );
}

function DealRow({ deal, onChanged }: { deal: StoredDeal; onChanged: () => Promise<void> }) {
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [name, setName] = useState(deal.name);
  const [description, setDescription] = useState(deal.description ?? "");
  const [status, setStatus] = useState<DealStatus>(deal.status as DealStatus);
  const [position, setPosition] = useState<DealPosition | "">((deal.position as DealPosition) ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setName(deal.name);
    setDescription(deal.description ?? "");
    setStatus(deal.status as DealStatus);
    setPosition((deal.position as DealPosition) ?? "");
    setError(null);
    setMode("edit");
  }

  async function save() {
    if (name.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateDeal(deal.id, {
        name: name.trim(),
        description: description.trim() || null,
        status,
        position: position || null,
      });
      await onChanged();
      setMode("view");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save changes");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteDeal(deal.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this deal");
      setMode("view");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "edit") {
    return (
      <div className={styles.editRow}>
        <div className={styles.editGrid}>
          <div className={styles.editField}>
            <label className={styles.label} htmlFor={`deal-name-${deal.id}`}>
              Name
            </label>
            <input
              id={`deal-name-${deal.id}`}
              className={styles.control}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className={styles.editField}>
            <label className={styles.label} htmlFor={`deal-position-${deal.id}`}>
              Our position
            </label>
            <select
              id={`deal-position-${deal.id}`}
              className={styles.control}
              value={position}
              onChange={(e) => setPosition(e.target.value as DealPosition | "")}
            >
              <option value="">Not set</option>
              {POSITION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className={styles.editField}>
            <label className={styles.label} htmlFor={`deal-status-${deal.id}`}>
              Status
            </label>
            <select
              id={`deal-status-${deal.id}`}
              className={styles.control}
              value={status}
              onChange={(e) => setStatus(e.target.value as DealStatus)}
            >
              {DEAL_STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className={styles.editField}>
          <label className={styles.label} htmlFor={`deal-desc-${deal.id}`}>
            Description <span className={styles.optional}>· optional</span>
          </label>
          <textarea
            id={`deal-desc-${deal.id}`}
            className={[styles.control, styles.textarea].join(" ")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        {error && (
          <p className={styles.rowError} role="alert">
            {error}
          </p>
        )}
        <div className={styles.editActions}>
          <span className={styles.editSpacer} />
          <button className={styles.btnGhost} type="button" onClick={() => setMode("view")} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnPrimary}
            type="button"
            onClick={save}
            disabled={name.trim() === "" || busy}
          >
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.deal}>
      <div className={styles.dealMain}>
        <div className={styles.dealName}>{deal.name}</div>
        {deal.description && <div className={styles.dealDesc}>{deal.description}</div>}
        {error && (
          <div className={styles.rowError} role="alert">
            {error}
          </div>
        )}
      </div>
      <div className={styles.pills}>
        {deal.position && (
          <span className={[styles.pill, styles.pillNeutral].join(" ")}>
            {humanize(deal.position)}
          </span>
        )}
        <span
          className={[
            styles.pill,
            deal.status === "active"
              ? styles.pillActive
              : deal.status === "signed"
                ? styles.pillSigned
                : styles.pillNeutral,
          ].join(" ")}
        >
          {deal.status}
        </span>
      </div>
      {mode === "confirm" ? (
        <div className={styles.confirm}>
          <span className={styles.confirmText}>Delete?</span>
          <button className={styles.btnGhost} type="button" onClick={() => setMode("view")} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnDanger}
            type="button"
            onClick={remove}
            disabled={busy}
            aria-label={`Confirm delete ${deal.name}`}
          >
            {busy ? "Deleting…" : "Delete"}
          </button>
        </div>
      ) : (
        <div className={styles.rowActions}>
          <button
            className={[styles.rowBtn, styles.rowBtnEdit].join(" ")}
            type="button"
            onClick={startEdit}
            aria-label={`Edit ${deal.name}`}
            title={`Edit ${deal.name}`}
          >
            <EditIcon />
          </button>
          <button
            className={[styles.rowBtn, styles.rowBtnDanger].join(" ")}
            type="button"
            onClick={() => {
              setError(null);
              setMode("confirm");
            }}
            aria-label={`Delete ${deal.name}`}
            title={`Delete ${deal.name}`}
          >
            <TrashIcon />
          </button>
        </div>
      )}
    </div>
  );
}

/* ---- Contract types (F01b) ---- */

function ContractTypesSection({
  types,
  onCreated,
}: {
  types: StoredContractType[];
  onCreated: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim() === "" || submitting) return;
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      await createContractType({ name: name.trim() });
      await onCreated();
      setName("");
      setDone("Contract type added.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the contract type");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.layout} role="tabpanel" id="panel-types" aria-labelledby="tab-types">
      <section>
        <div className={styles.listHead}>
          Contract types
          <span className={styles.listHint}>The taxonomy contracts are classified under</span>
        </div>
        {types.length === 0 ? (
          <p className={styles.empty}>No contract types yet. Add one to classify imported contracts.</p>
        ) : (
          <div className={styles.list}>
            {types.map((t) => (
              <TypeRow key={t.id} type={t} onChanged={onCreated} />
            ))}
          </div>
        )}
      </section>

      <aside>
        <form className={styles.formCard} onSubmit={onSubmit}>
          <h2 className={styles.formTitle}>New contract type</h2>
          <p className={styles.formLead}>Available as a choice on every import.</p>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="type-name">
              Name
            </label>
            <input
              id="type-name"
              className={styles.control}
              placeholder="e.g. Offtake Agreement"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          {done && !error && <p className={styles.success}>{done}</p>}

          <button className={styles.submit} type="submit" disabled={name.trim() === "" || submitting}>
            {submitting ? "Adding…" : "Add type"}
          </button>
          {submitting && <div className={styles.submitBar} />}
        </form>
      </aside>
    </div>
  );
}

function TypeRow({
  type,
  onChanged,
}: {
  type: StoredContractType;
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [name, setName] = useState(type.name);
  const [isDefault, setIsDefault] = useState(type.is_default);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setName(type.name);
    setIsDefault(type.is_default);
    setError(null);
    setMode("edit");
  }

  async function save() {
    if (name.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateContractType(type.id, { name: name.trim(), is_default: isDefault });
      await onChanged();
      setMode("view");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save changes");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteContractType(type.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this contract type");
      setMode("view");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "edit") {
    return (
      <div className={styles.editRow}>
        <div className={styles.editField}>
          <label className={styles.label} htmlFor={`type-name-${type.id}`}>
            Name
          </label>
          <input
            id={`type-name-${type.id}`}
            className={styles.control}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <label className={styles.editCheck} htmlFor={`type-default-${type.id}`}>
          <input
            id={`type-default-${type.id}`}
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
          />
          Default type for new imports
        </label>
        {error && (
          <p className={styles.rowError} role="alert">
            {error}
          </p>
        )}
        <div className={styles.editActions}>
          <span className={styles.editSpacer} />
          <button className={styles.btnGhost} type="button" onClick={() => setMode("view")} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnPrimary}
            type="button"
            onClick={save}
            disabled={name.trim() === "" || busy}
          >
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.item}>
      <div className={styles.itemMain}>
        <div className={styles.itemName}>{type.name}</div>
        {error && (
          <div className={styles.rowError} role="alert">
            {error}
          </div>
        )}
      </div>
      <div className={styles.pills}>
        {type.is_default && (
          <span className={[styles.pill, styles.pillDefault].join(" ")}>Default</span>
        )}
      </div>
      {mode === "confirm" ? (
        <div className={styles.confirm}>
          <span className={styles.confirmText}>Delete?</span>
          <button className={styles.btnGhost} type="button" onClick={() => setMode("view")} disabled={busy}>
            Cancel
          </button>
          <button
            className={styles.btnDanger}
            type="button"
            onClick={remove}
            disabled={busy}
            aria-label={`Confirm delete ${type.name}`}
          >
            {busy ? "Deleting…" : "Delete"}
          </button>
        </div>
      ) : (
        <div className={styles.rowActions}>
          <button
            className={[styles.rowBtn, styles.rowBtnEdit].join(" ")}
            type="button"
            onClick={startEdit}
            aria-label={`Edit ${type.name}`}
            title={`Edit ${type.name}`}
          >
            <EditIcon />
          </button>
          <button
            className={[styles.rowBtn, styles.rowBtnDanger].join(" ")}
            type="button"
            onClick={() => {
              setError(null);
              setMode("confirm");
            }}
            aria-label={`Delete ${type.name}`}
            title={`Delete ${type.name}`}
          >
            <TrashIcon />
          </button>
        </div>
      )}
      <span className={styles.itemDate}>{formatDate(type.created_at)}</span>
    </div>
  );
}
