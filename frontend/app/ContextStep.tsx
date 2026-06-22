"use client";

import { useEffect, useState } from "react";
import styles from "./review.module.css";
import {
  createClient,
  createContract,
  createContractType,
  createDeal,
  listClients,
  listContractTypes,
  listDeals,
  type StoredClient,
  type StoredContractType,
  type StoredDeal,
} from "./lib/api";

export interface ContractContext {
  contractId: string;
  clientLabel: string;
  dealLabel: string;
  contractName: string;
}

const NEW = "__new__";

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
  const [contractName, setContractName] = useState("");
  const [typeId, setTypeId] = useState("");
  const [newTypeName, setNewTypeName] = useState("");

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
  }

  const clientReady = creatingClient ? newClientName.trim() !== "" : clientId !== "";
  const dealReady = creatingDeal ? newDealName.trim() !== "" : dealId !== "";
  const typeReady = typeId === NEW ? newTypeName.trim() !== "" : typeId !== "";
  const ready = clientReady && dealReady && contractName.trim() !== "" && typeReady && !submitting;

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
        ? await createDeal({ client_id: client.id, name: newDealName.trim() })
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
      <header className={styles.topbar}>
        <div className={styles.brand}>
          donna<span className={styles.dot}>.</span>ai
        </div>
        <ol className={styles.steps}>
          <li className={styles.stepActive}>Context</li>
          <li>Parse</li>
          <li>Review</li>
          <li>Commit</li>
        </ol>
        <div className={styles.right} />
      </header>

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
