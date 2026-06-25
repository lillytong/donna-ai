"use client";

import { useEffect, useState } from "react";
import styles from "./review.module.css";
import ImportTopBar from "./ImportTopBar";
import {
  createClient,
  createContract,
  createContractType,
  createDeal,
  listClients,
  listContractTypes,
  listDeals,
  type DealPosition,
  type Origin,
  type StoredClient,
  type StoredContractType,
  type StoredDeal,
} from "../lib/api";

export interface ContractContext {
  contractId: string;
  clientLabel: string;
  dealLabel: string;
  contractName: string;
}

const NEW = "__new__";

// Baseline authorship (DD-55) — who drafted this first version. Required because
// it sets Donna's starting redline stance (own paper vs. counterparty paper).
const ORIGIN_OPTIONS: { value: Origin; label: string }[] = [
  { value: "us", label: "Us" },
  { value: "our_legal", label: "Our legal team" },
  { value: "counterparty", label: "Counterparty" },
];

// Which side of the deal the operator is on (deals.position, DD-50) — governs what
// Donna flags as unfavorable. Required only when creating a NEW deal; an existing
// deal already carries its position. Values mirror the schema CHECK exactly.
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

// Step 1 — Context (§9). Tells donna.ai where to store the contract before any
// parsing: select-or-create client, select-or-create deal (scoped to the client),
// contract name, contract type. On submit it resolves the FK chain
// (client → deal → contract) and lifts the new contract_id to the parent.
export default function ContextStep({ onReady }: { onReady: (ctx: ContractContext) => void }) {
  const [clients, setClients] = useState<StoredClient[]>([]);
  const [deals, setDeals] = useState<StoredDeal[]>([]);
  const [types, setTypes] = useState<StoredContractType[]>([]);

  const [clientId, setClientId] = useState("");
  const [newClientName, setNewClientName] = useState("");
  const [dealId, setDealId] = useState("");
  const [newDealName, setNewDealName] = useState("");
  const [newDealPosition, setNewDealPosition] = useState<DealPosition | "">("");
  const [contractName, setContractName] = useState("");
  const [typeId, setTypeId] = useState("");
  const [newTypeName, setNewTypeName] = useState("");
  const [origin, setOrigin] = useState<Origin | "">("");

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listClients(), listDeals(), listContractTypes()])
      .then(([c, d, t]) => {
        setClients(c);
        setDeals(d);
        setTypes(t);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load settings"))
      .finally(() => setLoading(false));
  }, []);

  const creatingClient = clientId === NEW;
  // A brand-new client has no existing deals, so deal creation is forced.
  const dealsForClient = creatingClient ? [] : deals.filter((d) => d.client_id === clientId);
  const creatingDeal = creatingClient || dealId === NEW;

  function resetDeal() {
    setDealId("");
    setNewDealName("");
    setNewDealPosition("");
  }

  const clientReady = creatingClient ? newClientName.trim() !== "" : clientId !== "";
  // A new deal must also declare its position (DD-50); an existing deal already has one.
  const dealReady = creatingDeal ? newDealName.trim() !== "" && newDealPosition !== "" : dealId !== "";
  const typeReady = typeId === NEW ? newTypeName.trim() !== "" : typeId !== "";
  const ready =
    clientReady && dealReady && contractName.trim() !== "" && typeReady && origin !== "" && !submitting;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    setError(null);
    try {
      const client = creatingClient
        ? await createClient({ name: newClientName.trim() })
        : clients.find((c) => c.id === clientId)!;

      const deal = creatingDeal
        ? await createDeal({
            client_id: client.id,
            name: newDealName.trim(),
            position: newDealPosition || null,
          })
        : deals.find((d) => d.id === dealId)!;

      const type =
        typeId === NEW
          ? await createContractType({ name: newTypeName.trim() })
          : types.find((t) => t.id === typeId)!;

      const contract = await createContract({
        client_id: client.id,
        deal_id: deal.id,
        contract_type_id: type.id,
        name: contractName.trim(),
        origin: origin || null,
      });

      onReady({
        contractId: contract.id,
        clientLabel: client.name,
        dealLabel: deal.name,
        contractName: contract.name,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the contract");
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.screen}>
      <ImportTopBar active="context" />

      <div className={styles.ctxWrap}>
        <form className={styles.ctxCard} onSubmit={onSubmit}>
          <h1 className={styles.ctxTitle}>New contract</h1>
          <p className={styles.ctxLead}>
            Tell donna where this contract lives before it parses the document.
          </p>

          {loading ? (
            <p className={styles.ctxLoading}>Loading clients and deals…</p>
          ) : (
            <>
              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="client">
                  Client
                </label>
                <select
                  id="client"
                  className={styles.control}
                  value={clientId}
                  onChange={(e) => {
                    setClientId(e.target.value);
                    resetDeal();
                  }}
                >
                  <option value="">Select a client…</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                  <option value={NEW}>+ Create new client</option>
                </select>
                {creatingClient && (
                  <input
                    className={styles.control}
                    placeholder="New client name"
                    value={newClientName}
                    onChange={(e) => setNewClientName(e.target.value)}
                    autoFocus
                  />
                )}
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="deal">
                  Deal
                </label>
                {creatingClient ? (
                  <input
                    className={styles.control}
                    placeholder="New deal name"
                    value={newDealName}
                    onChange={(e) => setNewDealName(e.target.value)}
                  />
                ) : (
                  <>
                    <select
                      id="deal"
                      className={styles.control}
                      value={dealId}
                      disabled={clientId === ""}
                      onChange={(e) => setDealId(e.target.value)}
                    >
                      <option value="">
                        {clientId === "" ? "Choose a client first…" : "Select a deal…"}
                      </option>
                      {dealsForClient.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                      <option value={NEW}>+ Create new deal</option>
                    </select>
                    {dealId === NEW && (
                      <input
                        className={styles.control}
                        placeholder="New deal name"
                        value={newDealName}
                        onChange={(e) => setNewDealName(e.target.value)}
                      />
                    )}
                  </>
                )}
              </div>

              {creatingDeal && (
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="deal-position">
                    Our position in this deal
                  </label>
                  <select
                    id="deal-position"
                    className={styles.control}
                    value={newDealPosition}
                    onChange={(e) => setNewDealPosition(e.target.value as DealPosition | "")}
                  >
                    <option value="">Select a position…</option>
                    {POSITION_OPTIONS.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="name">
                  Contract name
                </label>
                <input
                  id="name"
                  className={styles.control}
                  placeholder="e.g. Technology Licence Agreement"
                  value={contractName}
                  onChange={(e) => setContractName(e.target.value)}
                />
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="type">
                  Contract type
                </label>
                <select
                  id="type"
                  className={styles.control}
                  value={typeId}
                  onChange={(e) => setTypeId(e.target.value)}
                >
                  <option value="">Select a type…</option>
                  {types.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                  <option value={NEW}>+ Create new type</option>
                </select>
                {typeId === NEW && (
                  <input
                    className={styles.control}
                    placeholder="New contract type"
                    value={newTypeName}
                    onChange={(e) => setNewTypeName(e.target.value)}
                  />
                )}
              </div>

              <div className={styles.field}>
                <span className={styles.fieldLabel}>Who drafted this first version?</span>
                <div className={styles.segment} role="radiogroup" aria-label="Who drafted this first version?">
                  {ORIGIN_OPTIONS.map((o) => (
                    <button
                      key={o.value}
                      type="button"
                      role="radio"
                      aria-checked={origin === o.value}
                      className={[styles.segmentBtn, origin === o.value ? styles.segmentSelected : ""].join(" ")}
                      onClick={() => setOrigin(o.value)}
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
              </div>

              {error && <p className={styles.error}>{error}</p>}

              <button className={styles.ctxSubmit} type="submit" disabled={!ready}>
                {submitting ? "Creating…" : "Continue to upload →"}
              </button>
            </>
          )}
        </form>
      </div>
    </div>
  );
}
