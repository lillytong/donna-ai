"""Shared canonical clustering key for cross-document recommendation consistency (DD-89).

The SAME normalization must drive recommend-time clustering (services/donna/revision_recommend
judges each cluster ONCE and fans the verdict to every member) and read-time clustering
(services/import_/revision_review stamps each hunk with its cluster id + size for the grouped
review stop). If the two drifted, the grouped stop would not line up with the verdicts Donna
fanned out. So the field-level logic lives here once; each call site wraps it for its row shape
(asyncpg Record vs ReviewHunk).
"""

from __future__ import annotations

import hashlib


def normalize_segment(text: str) -> str:
    """Whitespace- and case-insensitive normal form for span-equality: trim, collapse internal
    whitespace runs to single spaces, casefold."""
    return " ".join(text.split()).casefold()


def strip_edge_punct(text: str) -> str:
    """Strip SURROUNDING non-alphanumeric punctuation from each side (internal chars untouched),
    so a defined-term rename wrapped in edge punctuation in one clause — e.g. a leading "(" —
    matches the same rename bare elsewhere. `str.isalnum` keeps unicode letters/digits."""
    start, end = 0, len(text)
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return text[start:end]


def cluster_key(
    significance: str, original_text: str | None, proposed_text: str | None
) -> tuple[str, str] | None:
    """Canonical clustering key for a SUBSTANTIVE REPLACEMENT hunk (DD-89): the pair
    (norm(original_text), norm(proposed_text)) where norm = `normalize_segment` (ws-collapse +
    casefold) plus `strip_edge_punct`. Identical original→proposed edits — modulo case,
    whitespace, and surrounding punctuation — collapse to ONE key so the same counterparty edit
    recurring across clauses is judged once and fanned to all members.

    Returns None (→ singleton on the per-hunk path) for: a trivial hunk; a whole-node new/deleted
    hunk (one side empty); or a degenerate edit whose key side is empty after the strip.
    Note: opposite-direction figure edits ("5%"→"10%" vs "10%"→"5%") yield DIFFERENT keys."""
    if significance == "trivial":
        return None
    if not original_text or not proposed_text:
        return None
    key_original = strip_edge_punct(normalize_segment(original_text))
    key_proposed = strip_edge_punct(normalize_segment(proposed_text))
    if not key_original or not key_proposed:
        return None
    return (key_original, key_proposed)


def cluster_id(key: tuple[str, str]) -> str:
    """Stable synthetic id for a cluster key — a hash so it is a safe URL path segment and never
    leaks the (adversarial) clause text. Deterministic across requests (derive-at-read, no
    schema/table)."""
    digest = hashlib.sha256("\x00".join(key).encode("utf-8")).hexdigest()
    return f"cl_{digest[:16]}"
