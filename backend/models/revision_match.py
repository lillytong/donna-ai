"""Models for Mode B Path-B clause matching (F03b — counterparty-revision matching).

The matcher maps a freshly parsed incoming counterparty revision (clean copy, no
ids) onto the baseline clause tree (the `last_shared_with_counterparty` snapshot —
stable ids). It produces an injective partial map incoming -> baseline; baseline
nodes left unmatched are DELETED, incoming nodes with no baseline counterpart are
NEW, and low-confidence pairs ABSTAIN -> operator-confirm in structural triage
(DD-28 Path B, DD-64 abstain-band destination).

`ClauseNode` is the in-memory input on BOTH sides. The matcher does NOT read the
DB or snapshots — F03b's parse path + snapshot baseline wire those in later; here
the inputs are plain node lists. `RevisionMatchResult` is the typed form of the
contract the spike proved: `{matches, new, deleted, abstains}` (DD-28/DD-54 — the
matched map carries classification inheritance; only NEW nodes get re-classified).
"""

from __future__ import annotations

from pydantic import BaseModel


class ClauseNode(BaseModel):
    """One clause node from either side of the diff.

    Field semantics mirror the greenlit spike (`spikes/mode_b_matching/matcher.py`):

    - `id`: baseline = the stable `SnapshotNode` id; incoming = ``None`` (no id yet).
    - `parent`: the *nearest clause-ancestor's key* on this side — baseline = parent
      clause's `id` (str); incoming = parent clause's `order` (int). ``None`` at the
      top level. (Non-clause ancestors are skipped: F03b passes the nearest clause.)
    - `order`: 0-based position in the clause reading sequence; the incoming side's
      `order` doubles as its key, since incoming nodes have no id.
    - `heading` / `body`: clause text. The matcher's canonical comparison text is the
      heading when present, else the body (verbatim from the spike).
    - `role`: carried for the caller; the matcher treats every node it receives as
      part of the clause universe (F03b filters to ``role == "clause"`` upstream).

    `depth` and the derived dotted number are computed internally from `parent`/
    `order` — they are NOT inputs (the spike derived them while flattening the tree).
    """

    id: str | None = None
    parent: str | int | None = None
    order: int
    heading: str = ""
    body: str = ""
    role: str = "clause"


class MatchedPair(BaseModel):
    """An auto-accepted (or anchor-locked) match: incoming node -> baseline id.

    `confidence` is the composite score (1.0 for anchor-locked byte-identical text).
    This is what fills `counterparty_revision_changes.node_id` for matched clauses.
    """

    incoming_index: int
    baseline_id: str
    confidence: float


class Abstention(BaseModel):
    """A low-confidence / thin-margin pair the matcher refuses to auto-commit.

    Surfaced to the operator to confirm in structural triage (DD-64) — the matcher
    NEVER silently commits these, which is what keeps Path-B matching in DD-35's
    low-consequence tier. `best_baseline_id` is the leading candidate (may be
    ``None`` if the residue produced no candidate above the floor).
    """

    incoming_index: int
    best_baseline_id: str | None
    confidence: float


class RevisionMatchResult(BaseModel):
    """The matcher's full decision set — the four buckets the spike proved.

    `matches` + `abstains` + `new` partition the incoming nodes; `matches` (resolved
    abstains aside) + `deleted` partition the baseline nodes (Layer-A invariants).
    """

    matches: list[MatchedPair]
    new: list[int]
    deleted: list[str]
    abstains: list[Abstention]


class LayerAReport(BaseModel):
    """Result of the mechanical (no-ground-truth) oracle (DD: SPIKE #3 Layer A)."""

    injectivity: bool
    partition_incoming: bool
    partition_baseline: bool
    roundtrip: bool

    @property
    def passed(self) -> bool:
        return (
            self.injectivity
            and self.partition_incoming
            and self.partition_baseline
            and self.roundtrip
        )


class SelfMatchReport(BaseModel):
    """Result of the self-match no-op check (a tree matched against itself)."""

    new: int
    deleted: int
    abstain: int

    @property
    def passed(self) -> bool:
        return self.new == 0 and self.deleted == 0 and self.abstain == 0
