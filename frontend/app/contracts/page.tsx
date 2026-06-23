"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import styles from "./cockpit.module.css";
import row from "./contracts-list.module.css";
import {
  listContracts,
  listContractTypes,
  updateContract,
  deleteContract,
  type StoredContract,
  type StoredContractType,
  type ContractStatus,
  type Origin,
} from "../lib/api";

// Entry to the cockpit: a flat list of contracts, each opening its live-call
// surface. Each row carries inline edit (rename + retype + restatus + origin)
// and a destructive delete that names its cascade before it runs. listContracts
// returns every contract; a contract with no committed tree opens to the
// cockpit's "no clauses yet" state rather than being hidden here.

const STATUS_OPTIONS: ContractStatus[] = ["drafting", "under negotiation", "signed"];
const ORIGIN_OPTIONS: { value: Origin; label: string }[] = [
  { value: "us", label: "Us" },
  { value: "our_legal", label: "Our legal" },
  { value: "counterparty", label: "Counterparty" },
];

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

type RowMode = "view" | "edit" | "confirm";

function ContractRow({
  contract,
  types,
  onChanged,
}: {
  contract: StoredContract;
  types: StoredContractType[];
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState<RowMode>("view");
  const [name, setName] = useState(contract.name);
  const [status, setStatus] = useState<ContractStatus>(contract.status as ContractStatus);
  const [contractTypeId, setContractTypeId] = useState(contract.contract_type_id);
  const [origin, setOrigin] = useState<string>(contract.origin ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setName(contract.name);
    setStatus(contract.status as ContractStatus);
    setContractTypeId(contract.contract_type_id);
    setOrigin(contract.origin ?? "");
    setError(null);
    setMode("edit");
  }

  async function save() {
    if (name.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateContract(contract.id, {
        name: name.trim(),
        status,
        contract_type_id: contractTypeId,
        origin: origin === "" ? null : origin,
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
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await deleteContract(contract.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete this contract");
      setMode("view");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "edit") {
    return (
      <li className={[row.row, row.rowEditing].join(" ")}>
        <form
          className={row.editForm}
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <div className={row.editGrid}>
            <div className={row.field}>
              <label className={row.label} htmlFor={`name-${contract.id}`}>
                Name
              </label>
              <input
                id={`name-${contract.id}`}
                className={row.control}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                required
              />
            </div>
            <div className={row.field}>
              <label className={row.label} htmlFor={`status-${contract.id}`}>
                Status
              </label>
              <select
                id={`status-${contract.id}`}
                className={row.control}
                value={status}
                onChange={(e) => setStatus(e.target.value as ContractStatus)}
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            {types.length > 0 && (
              <div className={row.field}>
                <label className={row.label} htmlFor={`type-${contract.id}`}>
                  Type
                </label>
                <select
                  id={`type-${contract.id}`}
                  className={row.control}
                  value={contractTypeId}
                  onChange={(e) => setContractTypeId(e.target.value)}
                >
                  {types.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className={row.field}>
              <label className={row.label} htmlFor={`origin-${contract.id}`}>
                Origin
              </label>
              <select
                id={`origin-${contract.id}`}
                className={row.control}
                value={origin}
                onChange={(e) => setOrigin(e.target.value)}
              >
                <option value="">Not set</option>
                {ORIGIN_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error && (
            <p className={row.rowError} role="alert">
              {error}
            </p>
          )}
          <div className={row.editActions}>
            <button
              className={row.btnGhost}
              type="button"
              onClick={() => setMode("view")}
              disabled={busy}
            >
              Cancel
            </button>
            <button className={row.btnPrimary} type="submit" disabled={name.trim() === "" || busy}>
              {busy && <span className={row.spin} aria-hidden />}
              {busy ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className={row.row}>
      <Link className={row.link} href={`/contracts/${contract.id}`}>
        <span className={styles.pickItemName}>{contract.name}</span>
        <span className={styles.pickStatus}>{contract.status}</span>
        <span className={row.arrow} aria-hidden>
          ›
        </span>
      </Link>

      {mode === "confirm" ? (
        <div className={row.confirm} role="alertdialog" aria-label={`Delete ${contract.name}`}>
          <p className={row.confirmText}>
            Delete <span className={row.confirmName}>{contract.name}</span> and all its clauses,
            issues &amp; comments? This can&rsquo;t be undone.
          </p>
          {error && (
            <p className={row.rowError} role="alert">
              {error}
            </p>
          )}
          <div className={row.confirmActions}>
            <button
              className={row.btnGhost}
              type="button"
              onClick={() => setMode("view")}
              disabled={busy}
              autoFocus
            >
              Cancel
            </button>
            <button
              className={row.btnDanger}
              type="button"
              onClick={remove}
              disabled={busy}
              aria-label={`Delete ${contract.name} and all its clauses and issues`}
            >
              {busy && <span className={row.spin} aria-hidden />}
              {busy ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>
      ) : (
        <div className={row.actions}>
          {error && (
            <p className={row.rowError} role="alert">
              {error}
            </p>
          )}
          <button
            className={row.iconBtn}
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              startEdit();
            }}
            aria-label={`Edit ${contract.name}`}
            title={`Edit ${contract.name}`}
          >
            <EditIcon />
          </button>
          <button
            className={[row.iconBtn, row.iconBtnDanger].join(" ")}
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setError(null);
              setMode("confirm");
            }}
            aria-label={`Delete ${contract.name}`}
            title={`Delete ${contract.name}`}
          >
            <TrashIcon />
          </button>
        </div>
      )}
    </li>
  );
}

export default function ContractsList() {
  const [contracts, setContracts] = useState<StoredContract[] | null>(null);
  const [types, setTypes] = useState<StoredContractType[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const c = await listContracts();
    setContracts(c);
  }, []);

  useEffect(() => {
    let live = true;
    listContracts()
      .then((c) => live && setContracts(c))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load contracts"));
    // Types power the edit row's type dropdown; a failure here just hides that
    // one field rather than blocking the list.
    listContractTypes()
      .then((t) => live && setTypes(t))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  return (
    <div className={styles.screen}>
      <div className={styles.pickWrap}>
        <div className={styles.pickInner}>
          <h1 className={styles.pickTitle}>Contracts</h1>
          <p className={styles.pickLead}>
            Open a contract to run the call — navigate clauses and capture issues live. Rename or
            remove one with its row controls.
          </p>

          {error ? (
            <div className={styles.center}>
              <p className={styles.error}>{error}</p>
            </div>
          ) : contracts === null ? (
            <div className={styles.center}>
              <div className={styles.progressTrack} role="progressbar" aria-label="Loading contracts">
                <div className={styles.progressBar} />
              </div>
              <p className={styles.phase}>Loading contracts…</p>
            </div>
          ) : contracts.length === 0 ? (
            <div className={styles.center}>
              <p className={styles.centerTitle}>No contracts yet</p>
              <p className={styles.centerHint}>Import a contract first — it will appear here ready for the call.</p>
            </div>
          ) : (
            <ul className={styles.pickList}>
              {contracts.map((c) => (
                <ContractRow key={c.id} contract={c} types={types} onChanged={reload} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
