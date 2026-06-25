"""Lifecycle-badge derivation + version/snapshot lineage (F27, DD-75).

Operator-facing read surfaces over the snapshot/pointer machinery (DD-48/DD-70/
DD-71) — all DERIVED, no schema change:

  * `derive_status` — the pure, I/O-free badge resolver (DD-75 table), top-down,
    FIRST MATCH WINS. Unit-tested directly.
  * `derive_status_for_contracts` — a SET-BASED badge for the My-Contracts / home
    list: ONE query (latest snapshot + its pointer tags + a per-contract
    divergence-EXISTS), no N+1. Drives the card badges.
  * `get_lineage` — the full v1→v2→…→vN chain (numbered by ROW_NUMBER over
    created_at, DD-70), direction-tagged. Received versions (Mode B `as_received`
    snapshots / `received` pointers, F03b) render as real numbered entries; the
    greyed `received` placeholder slot is the empty state, kept only for a side
    that has no received version yet. The live working copy is marked separately.

Badge derivation (DD-75), where LBE = latest boundary event = most-recent snapshot:
  1. status=signed                       → "Signed · vN"
  2. no snapshot                         → "Working copy" (no version)
  3. LBE is a receive, not engaged       → "Your move · vN"      (Phase-2)
  4. LBE is a receive, engaged (edited)  → "Working copy" (based-on "vN received…") (Phase-2)
  5. LBE is a send                       → "Sent to counterparty/legal/both · vN"
     + post-send-edit edge: diverged since the send → keep the Sent badge and set
       the "edited since sent" marker (never reverts to Working copy, DD-70 §5).

"Engaged" / the marker = the working copy has diverged since the LBE snapshot —
any non-deleted node `updated_at` > LBE.created_at, or an unassigned `node_versions`
row since it (Katrina's ADR). In v1 the LBE is always a send (only Mark-as-sent cuts
snapshots, DD-71), so rules 3/4 stay dormant until Mode B adds receives.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from backend.models.lineage import (
    ContractBadge,
    LineageEntry,
    LineageView,
    PointerRow,
    ReservedSlot,
    WorkingCopyEntry,
)


# --- protocols (keep the resolver pure + cheap to unit-test) ----------------
class _HasStatus(Protocol):
    status: str


class _SnapshotLike(Protocol):
    id: str
    origin: str

    @property
    def created_at(self) -> Any: ...


class _PointerLike(Protocol):
    party: str
    direction: str
    snapshot_id: str


# --- label helpers ----------------------------------------------------------
# schema pointer party → operator-facing side label.
_SIDE = {"counterparty": "counterparty", "legal_team": "legal"}

# (party, direction) → friendly DD-48 pointer name shown on the timeline.
_POINTER_LABEL = {
    ("counterparty", "shared"): "last_shared_with_counterparty",
    ("legal_team", "shared"): "last_shared_with_legal",
    ("counterparty", "received"): "last_received_from_counterparty",
    ("legal_team", "received"): "last_received_from_legal",
}


def _party_label(parties: set[str]) -> str | None:
    sides = {_SIDE[p] for p in parties if p in _SIDE}
    if not sides:
        return None
    if sides == {"counterparty", "legal"}:
        return "both"
    return next(iter(sides))


def _send_label(parties: set[str]) -> str:
    side = _party_label(parties)
    if side == "both":
        return "Sent to counterparty & legal"
    if side == "legal":
        return "Sent to legal"
    return "Sent to counterparty"


def _compose_badge(
    *,
    status: str,
    snapshot_count: int,
    lbe_origin: str | None,
    shared_parties: set[str],
    received_parties: set[str],
    diverged: bool,
) -> ContractBadge:
    """The single source of badge truth — both public entry points reduce to this.
    `snapshot_count` doubles as the latest snapshot's v-number (numbering is
    oldest-first, so the newest = row N = count)."""
    # Rule 1: signed wins outright.
    if status == "signed":
        return ContractBadge(label="Signed", version=(snapshot_count or None), marker=False)
    # Rule 2: nothing frozen yet → unsent working copy, no number.
    if snapshot_count == 0:
        return ContractBadge(label="Working copy", version=None, marker=False)

    version = snapshot_count
    # A receive is signalled by a `received` pointer on the LBE or an as_received cut.
    is_receive = bool(received_parties) or lbe_origin == "as_received"

    if is_receive:
        party = _party_label(received_parties)
        # Rule 3: untouched inbound revision floats up as the operator's move.
        if not diverged:
            return ContractBadge(label="Your move", version=version, marker=False, party=party)
        # Rule 4: engaged inbound revision → back to a working copy, provenance noted.
        based = f"v{version} received from {party}" if party else f"v{version} received"
        return ContractBadge(label="Working copy", version=None, marker=False, based_on=based)

    # Rule 5: a send. Post-send-edit edge — diverged keeps the Sent badge + marker.
    if shared_parties:
        return ContractBadge(
            label=_send_label(shared_parties),
            version=version,
            marker=diverged,
            party=_party_label(shared_parties),
        )

    # Defensive: a snapshot exists but no pointer rests on it (e.g. a manual cut
    # never shared, not a receive) — the live tree is the working copy. v1 never
    # hits this (Mark-as-sent always advances a `shared` pointer, DD-71).
    return ContractBadge(label="Working copy", version=None, marker=False)


def derive_status(
    contract: _HasStatus,
    snapshots: Sequence[_SnapshotLike],
    pointers: Sequence[_PointerLike],
    *,
    diverged: bool = False,
) -> ContractBadge:
    """Pure, I/O-free badge resolver (DD-75). `diverged` is the "edited since the
    LBE snapshot" signal the caller probes (the marker / engaged check); it is
    supplied rather than computed here so the resolver stays free of DB access."""
    if not snapshots:
        return _compose_badge(
            status=contract.status,
            snapshot_count=0,
            lbe_origin=None,
            shared_parties=set(),
            received_parties=set(),
            diverged=diverged,
        )
    lbe = max(snapshots, key=lambda s: s.created_at)
    at_lbe = [p for p in pointers if p.snapshot_id == lbe.id]
    shared = {p.party for p in at_lbe if p.direction == "shared"}
    received = {p.party for p in at_lbe if p.direction == "received"}
    return _compose_badge(
        status=contract.status,
        snapshot_count=len(snapshots),
        lbe_origin=lbe.origin,
        shared_parties=shared,
        received_parties=received,
        diverged=diverged,
    )


# Set-based badge for the list path (My Contracts + home) — ONE query, no N+1:
# per contract, the latest snapshot (LBE), its pointer tags, its v-number (the
# snapshot count), and a divergence-EXISTS against the LBE's created_at.
_LIST_BADGES = """
WITH latest AS (
    SELECT c.id AS contract_id, c.status,
           (SELECT count(*) FROM contract_snapshots s WHERE s.contract_id = c.id)
               AS snapshot_count,
           lbe.id AS lbe_id, lbe.created_at AS lbe_created_at, lbe.origin AS lbe_origin
    FROM contracts c
    LEFT JOIN LATERAL (
        SELECT s.id, s.created_at, s.origin
        FROM contract_snapshots s
        WHERE s.contract_id = c.id
        ORDER BY s.created_at DESC
        LIMIT 1
    ) lbe ON true
    WHERE c.id = ANY($1::uuid[])
)
SELECT l.contract_id, l.status, l.snapshot_count, l.lbe_id, l.lbe_origin,
       COALESCE(
           (SELECT array_agg(p.party || ':' || p.direction)
            FROM snapshot_pointers p
            WHERE p.contract_id = l.contract_id AND p.snapshot_id = l.lbe_id),
           ARRAY[]::text[]
       ) AS lbe_pointers,
       (l.lbe_id IS NOT NULL AND (
           EXISTS (
               SELECT 1 FROM nodes n
               WHERE n.contract_id = l.contract_id AND n.is_deleted = false
                 AND n.updated_at > l.lbe_created_at
           )
           OR EXISTS (
               SELECT 1 FROM node_versions nv
               JOIN nodes n2 ON n2.id = nv.node_id
               WHERE n2.contract_id = l.contract_id AND nv.snapshot_id IS NULL
           )
       )) AS diverged
FROM latest l
"""


def _split_pointer_tags(tags: Sequence[str]) -> tuple[set[str], set[str]]:
    """`["counterparty:shared", "legal_team:shared"]` → (shared_parties, received)."""
    shared: set[str] = set()
    received: set[str] = set()
    for tag in tags:
        party, _, direction = tag.partition(":")
        (shared if direction == "shared" else received).add(party)
    return shared, received


async def derive_status_for_contracts(
    conn: Any, contract_ids: Sequence[str]
) -> dict[str, ContractBadge]:
    """Set-based badge for a list of contracts in ONE query (no N+1). Returns a
    {contract_id: ContractBadge} map; ids with no matching contract are absent."""
    if not contract_ids:
        return {}
    rows = await conn.fetch(_LIST_BADGES, list(contract_ids))
    badges: dict[str, ContractBadge] = {}
    for r in rows:
        shared, received = _split_pointer_tags(r["lbe_pointers"] or [])
        badges[str(r["contract_id"])] = _compose_badge(
            status=r["status"],
            snapshot_count=int(r["snapshot_count"]),
            lbe_origin=r["lbe_origin"],
            shared_parties=shared,
            received_parties=received,
            diverged=bool(r["diverged"]),
        )
    return badges


def _entry_direction(origin: str, has_shared: bool, has_received: bool) -> str:
    if has_received or origin == "as_received":
        return "received"
    return "sent"


async def get_lineage(conn: Any, contract_id: str) -> LineageView:
    """Assemble the full lineage view: the badge (via the set-based resolver, which
    carries the divergence probe), the numbered send/receive timeline (received
    versions from Mode B appear as real numbered entries, F03b), the
    separately-marked working copy, and the greyed `received` placeholder slot for
    any side without a received version yet (the empty state)."""
    from backend.services.snapshot import list_numbered_snapshots, list_pointers

    badge = (await derive_status_for_contracts(conn, [contract_id])).get(
        contract_id, ContractBadge(label="Working copy", version=None, marker=False)
    )
    numbered = await list_numbered_snapshots(conn, contract_id)
    pointers = await list_pointers(conn, contract_id)

    # Group pointers by the snapshot they rest on + find the current redline baseline.
    pointers_at: dict[str, list[PointerRow]] = {}
    for p in pointers:
        pointers_at.setdefault(p.snapshot_id, []).append(p)
    baseline_snapshot_id = next(
        (p.snapshot_id for p in pointers if (p.party, p.direction) == ("counterparty", "shared")),
        None,
    )

    timeline: list[LineageEntry] = []
    for version, snap in numbered:
        at = pointers_at.get(snap.id, [])
        shared = {p.party for p in at if p.direction == "shared"}
        received = {p.party for p in at if p.direction == "received"}
        timeline.append(
            LineageEntry(
                version=version,
                direction=_entry_direction(snap.origin, bool(shared), bool(received)),
                party=_party_label(received) if received else _party_label(shared),
                created_at=snap.created_at,
                snapshot_id=snap.id,
                pointer_labels=sorted(
                    _POINTER_LABEL[(p.party, p.direction)]
                    for p in at
                    if (p.party, p.direction) in _POINTER_LABEL
                ),
                is_current_baseline=(snap.id == baseline_snapshot_id),
            )
        )

    # A received version now renders as a real numbered timeline entry (Mode B sets
    # the `received` pointer / as_received snapshot, F03b). The greyed reserved slot
    # is the empty state — keep it only for a side with no received version yet.
    received_sides: set[str] = set()
    for entry in timeline:
        if entry.direction != "received" or not entry.party:
            continue
        if entry.party == "both":
            received_sides |= {"counterparty", "legal"}
        else:
            received_sides.add(entry.party)
    reserved = [
        ReservedSlot(party=side, label=f"Received from {side}")
        for side in ("counterparty", "legal")
        if side not in received_sides
    ]
    return LineageView(
        contract_id=contract_id,
        badge=badge,
        timeline=timeline,
        working_copy=WorkingCopyEntry(diverged_since_last_send=badge.marker),
        reserved=reserved,
    )
