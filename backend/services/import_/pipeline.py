"""Import orchestrator for a first import (Mode A) — a thin coordinator (DD-04/43).

Chains the existing, separately-validated import pieces:
    read_docx(path) -> build_tree(parsed) -> tree_to_node_rows(tree)
        -> insert_nodes(conn, contract_id, rows)

No business logic lives here; each step is owned by its own service. The
synchronous parse (docx read + tree build + row mapping) is CPU/file-bound and
runs in a thread executor so it never blocks the event loop (async standard).
The persist step is the only awaited DB I/O.

Entity detection (detect_entities, DD-10/11/12) is AI/Anthropic-dependent, so it
is OPTIONAL and off by default: the core parse->persist path never touches the
LLM. When `detect=True`, candidates are returned for the import-review UI; they
are not persisted here (resolved into defined_terms / cross_references only after
the operator confirms).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from backend.models.contract_tree import (
    BlockClassification,
    NodeRow,
    ParsedDocument,
    ParsedTree,
)
from backend.models.extraction import Extraction
from backend.models.imports import (
    CandidateNode,
    ImportResult,
    PreviewResponse,
    TrackedChangeReport,
)
from backend.services.contract_repo import insert_node_images, insert_nodes
from backend.services.import_.classify import (
    classify,
    classify_backmatter_region,
    classify_frontmatter_region,
    find_boundary,
)
from backend.services.import_.detect import detect_entities
from backend.services.import_.docx_reader import count_tracked_changes, read_docx
from backend.services.import_.numbering import derive_enumerators, derive_numbers
from backend.services.import_.persist import tree_to_node_rows
from backend.services.import_.tree_builder import build_tree

# Back-matter AI category (DD-56) → (role, force_kind). A `title` becomes the new
# `appendix_title` role (level-0 divider); heading/body stay `appendix` but force
# the persist heading/body split; signature content becomes `signature_block`.
_BACKMATTER_MAP: dict[str, tuple[str, str | None]] = {
    "title": ("appendix_title", "heading"),
    "heading": ("appendix", "heading"),
    "body": ("appendix", "body"),
    "signature": ("signature_block", None),
}
_BACK_MATTER_ROLES = frozenset({"appendix", "appendix_title", "signature_block"})


def _read_and_classify(
    path: str | Path,
) -> tuple[ParsedDocument, dict[int, BlockClassification]]:
    doc = read_docx(path)
    return doc, classify(doc.blocks)


def _build_stamped(
    doc: ParsedDocument, classifications: dict[int, BlockClassification]
) -> ParsedTree:
    """Drop TOC -> build_tree -> stamp each node with its role / has_placeholder.
    TOC blocks are excluded before the tree is built; `drafting_note` and
    front-matter are kept (only TOC is dropped). Node index maps one-to-one to its
    kept block (build_tree emits one node per block in order). A node's final
    `uncertain` is the OR of structural (DD-36) and classification uncertainty."""
    kept = [b for b in doc.blocks if not classifications[b.order].is_toc]
    tree = build_tree(
        ParsedDocument(
            blocks=kept,
            extracted_chars=doc.extracted_chars,
            ceiling_chars=doc.ceiling_chars,
        )
    )
    for n in tree.nodes:
        c = classifications[kept[n.index].order]
        n.role = c.role
        n.has_placeholder = c.has_placeholder
        n.uncertain = n.uncertain or c.uncertain
        n.force_kind = c.force_kind
    return tree


async def _apply_region(
    doc: ParsedDocument, classifications: dict[int, BlockClassification]
) -> None:
    """Whole-region front-matter classification pass (DD-54/DD-35). Sends ALL
    front-matter blocks (everything up to and including the agreement-statement
    boundary, TOC excluded) together — one Haiku call labels them with the full
    front matter in view, fixing the per-block failure mode (a second title,
    grouped recitals mislabeled `parties`). Mutates `classifications` in place.

    Guard (§12 export-exclusion): a deterministic `drafting_note` is never
    overridden, and a block the *model* newly calls `drafting_note` is set to that
    role but left `uncertain` for operator confirmation — content is never
    silently excluded from export on the model's say-so. A failed/empty answer
    leaves the deterministic roles untouched."""
    boundary = find_boundary(doc.blocks)
    if boundary is None:
        return
    region = {
        b.order: b.text
        for b in doc.blocks
        if b.order <= boundary and not classifications[b.order].is_toc
    }
    if not region:
        return
    for idx, role in (await classify_frontmatter_region(region)).items():
        if classifications[idx].role == "drafting_note":
            continue
        if role == "drafting_note":
            classifications[idx] = classifications[idx].model_copy(
                update={"role": "drafting_note", "uncertain": True}
            )
        else:
            classifications[idx] = classifications[idx].model_copy(
                update={"role": role, "uncertain": False}
            )


async def _apply_backmatter_region(
    doc: ParsedDocument, classifications: dict[int, BlockClassification]
) -> None:
    """Whole-region back-matter categorization pass (DD-56/DD-35), hybrid with the
    deterministic title rule (DD-58). The deterministic pass already settled
    known-designator title dividers ("Schedule I", "Annexure A") as
    `appendix_title`; those are EXCLUDED here. The remaining back-matter blocks
    (deterministic `appendix` / `signature_block`, TOC excluded) go to one Haiku
    call that labels each title / heading / body / signature SEMANTICALLY (no
    keyword list) — so an unseen designator ("Table 3") can still be promoted to
    `appendix_title`. Mutates `classifications` in place via `_BACKMATTER_MAP`;
    `force_kind` carries the heading/body decision to persist. A failed/empty
    answer leaves the deterministic roles."""
    region = {
        b.order: b.text
        for b in doc.blocks
        if classifications[b.order].role in _BACK_MATTER_ROLES
        and classifications[b.order].role != "appendix_title"
        and not classifications[b.order].is_toc
    }
    if not region:
        return
    for idx, category in (await classify_backmatter_region(region)).items():
        role, force_kind = _BACKMATTER_MAP[category]
        classifications[idx] = classifications[idx].model_copy(
            update={"role": role, "force_kind": force_kind, "uncertain": False}
        )


async def _classify_tree(path: str | Path, *, ai: bool) -> ParsedTree:
    """Deterministic classify (free, instant) then, by default, the two
    whole-region Haiku passes — front matter (DD-54) and back matter (DD-56). The
    sync parse/build run in a thread executor (async standard); only the region
    passes await the LLM."""
    doc, classifications = await asyncio.to_thread(_read_and_classify, path)
    if ai:
        await _apply_region(doc, classifications)
        await _apply_backmatter_region(doc, classifications)
    return await asyncio.to_thread(_build_stamped, doc, classifications)


def _number_for(
    index: int, parent_index: int | None, numbers: dict[int, str], enums: dict[int, str]
) -> str:
    """Display number: decimal for clauses, parent-decimal + "(a)" for an auto-numbered
    enumerated item (e.g. "1.2.1(b)", DD-98/DD-99), else "" (unnumbered)."""
    if index in enums:
        parent_num = numbers.get(parent_index, "") if parent_index is not None else ""
        return f"{parent_num}{enums[index]}"
    return numbers.get(index, "")


def _preview_from_tree(tree: ParsedTree, path: str | Path) -> PreviewResponse:
    rows = tree_to_node_rows(tree)
    numbers = derive_numbers(tree)  # clause-role nodes only (DD-54)
    enums = derive_enumerators(tree)  # auto-numbered enumerated items (DD-99)
    depth_for = {n.index: n.depth for n in tree.nodes}
    nodes = [
        CandidateNode(
            index=r.index,
            parent_index=r.parent_index,
            order_index=r.order_index,
            depth=depth_for[r.index],
            number=_number_for(r.index, r.parent_index, numbers, enums),
            content_type=r.content_type,
            heading=r.heading,
            body=r.body,
            table_data=r.table_data,
            plain_text=r.plain_text,
            uncertain=r.uncertain,
            role=r.role,
            has_placeholder=r.has_placeholder,
            enumerator_format=r.enumerator_format,
        )
        for r in rows
    ]
    insertions, deletions = count_tracked_changes(path)
    return PreviewResponse(
        nodes=nodes,
        node_count=len(nodes),
        uncertain_count=sum(1 for n in nodes if n.uncertain),
        tracked_changes=TrackedChangeReport(
            insertions=insertions,
            deletions=deletions,
            flattened=(insertions + deletions) > 0,
        ),
    )


async def _detect_candidates(rows: list[NodeRow]) -> dict[int, Extraction]:
    out: dict[int, Extraction] = {}
    for r in rows:
        if r.content_type != "prose" or not r.plain_text:
            continue
        out[r.index] = await detect_entities(r.plain_text)
    return out


async def _store_staging_images(conn: Any, contract_id: str, tree: ParsedTree) -> None:
    """Write image bytes from attachment nodes into staging_node_images so the
    commit endpoint can persist them to node_images after insert_nodes returns
    the real node UUIDs. Keyed by TreeNode.index (sequential flat position),
    which matches the id_map key returned by insert_nodes. ON CONFLICT replaces
    so a re-preview (same contract_id) refreshes stale staging rows."""
    for n in tree.nodes:
        if n.image_data is None:
            continue
        await conn.execute(
            """INSERT INTO staging_node_images
               (contract_id, node_index, mime_type, cx_emu, cy_emu, data)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (contract_id, node_index) DO UPDATE
               SET mime_type = EXCLUDED.mime_type,
                   cx_emu    = EXCLUDED.cx_emu,
                   cy_emu    = EXCLUDED.cy_emu,
                   data      = EXCLUDED.data""",
            contract_id,
            n.index,
            n.image_mime or "image/png",
            n.image_cx_emu,
            n.image_cy_emu,
            n.image_data,
        )


async def preview_docx(
    path: str | Path,
    *,
    ai: bool = True,
    conn: Any = None,
    contract_id: str | None = None,
) -> PreviewResponse:
    """Parse a .docx into the F04 candidate tree (numbers + uncertain flags +
    tracked-change report) without persisting. The sync parse runs in a thread
    executor (async standard).

    When `conn` and `contract_id` are both provided the parsed image bytes are
    staged in `staging_node_images` so the two-step commit endpoint can later
    persist them to `node_images` (they are absent from the NodeRow payload).

    The Haiku residue pass (DD-54) runs by default (`ai=True`) over the
    uncertain front-matter; pass `ai=False` to skip the LLM (offline/tests)."""
    tree = await _classify_tree(path, ai=ai)
    if conn is not None and contract_id is not None:
        await _store_staging_images(conn, contract_id, tree)
    return await asyncio.to_thread(_preview_from_tree, tree, path)


async def import_docx(
    conn: Any,
    contract_id: str,
    path: str | Path,
    *,
    detect: bool = False,
    ai: bool = True,
) -> ImportResult:
    tree = await _classify_tree(path, ai=ai)
    rows = await asyncio.to_thread(tree_to_node_rows, tree)
    id_map = await insert_nodes(conn, contract_id, rows)
    await insert_node_images(conn, tree, id_map)
    entity_candidates = await _detect_candidates(rows) if detect else None
    return ImportResult(
        contract_id=contract_id,
        node_count=len(rows),
        root_count=sum(1 for r in rows if r.parent_index is None),
        uncertain_count=sum(1 for r in rows if r.uncertain),
        entity_candidates=entity_candidates,
    )
