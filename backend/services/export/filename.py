"""Export filename convention: `[Client]_[Contract Name]_[YYMMDD]_v[N][_kind].docx`.

One place so the three export routes (clean copy, redline, issue list) name files
the same way. `version` is the contract's working version number (DD-48 lineage);
true per-version labelling is F27 — until then the caller derives N from the
snapshot count. `kind` distinguishes the document type for the non-clean-copy
exports so they don't collide with a clean copy of the same version.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from backend.services.settings_repo import get_client
from backend.services.snapshot import list_snapshots

_ILLEGAL = re.compile(r"[^A-Za-z0-9 ._-]")


def _slug(value: str, fallback: str) -> str:
    cleaned = _ILLEGAL.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def export_filename(
    *,
    client_name: str | None,
    contract_name: str | None,
    version: int,
    on: date,
    kind: str | None = None,
) -> str:
    fields = [
        _slug(client_name or "", "Client"),
        _slug(contract_name or "", "Contract"),
        on.strftime("%y%m%d"),
        f"v{version}",
    ]
    if kind:
        fields.append(kind)
    return "_".join(fields) + ".docx"


async def resolve_export_filename(
    conn: Any, contract: Any, *, kind: str | None = None, on: date | None = None
) -> str:
    """Build the export filename for a `StoredContract`, resolving the client name
    and deriving the working version N from the snapshot count (F27 replaces this
    with a real version label). Falls back to generic fields when a piece is
    missing rather than failing the download."""
    client = await get_client(conn, contract.client_id) if contract is not None else None
    version = (len(await list_snapshots(conn, contract.id)) + 1) if contract is not None else 1
    return export_filename(
        client_name=client.name if client is not None else None,
        contract_name=contract.name if contract is not None else None,
        version=version,
        on=on or date.today(),
        kind=kind,
    )
