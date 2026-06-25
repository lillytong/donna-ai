"use client";

// Rearrange mode (lazy-loaded — @dnd-kit only enters the bundle when the operator
// flips "Rearrange"). A sortable CLAUSE tree following dnd-kit's flatten + projected-
// depth pattern: dragging a row drags its whole sub-tree (descendants fold out of
// the list while dragging), and the horizontal drag offset projects a new depth →
// a new parent, so reorder AND reparent happen in one gesture. The dragged row is
// the live drop indicator: it indents to the projected depth at the projected spot.
//
// Rows arrive collapse-filtered from the parent (the read tree's visibleRows), so a
// collapsed section is ONE draggable row and its hidden sub-tree rides along via the
// backend's parent_id — projected depth/parent and the drop anchor are computed over
// these VISIBLE rows, each keeping its TRUE parent_id/depth. A ▸/▾ twirl toggles the
// parent's shared `collapsed` set: collapse for a short top-level reorder, expand to
// drop a node into a section.
//
// On drop we translate the projected {depth, parentId, position} into the backend
// move contract ({parent_id, after_node_id|before_node_id}) and call moveNode. The
// PARENT owns selection + the flash — on success it refetches the tree and selects
// + flashes the moved node (props `selectedId` / `flashId`). A 422 (cycle) reverts
// the optimistic order and shows a gentle inline message.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  type DragMoveEvent,
  type DragOverEvent,
  type DragStartEvent,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  type UniqueIdentifier,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import styles from "./cockpit.module.css";
import { moveNode } from "../lib/api";

// Horizontal pixels per nesting level — must equal the read tree's 22px step so a
// drag's reparent reads at the same indentation the operator already knows.
const INDENT = 22;

// One row the parent already flattened (document order + derived clause number).
// Structurally a subset of the cockpit's FlatNode, so `body` rows pass straight in.
export interface RearrangeRow {
  id: string;
  depth: number;
  number: string;
  text: string;
  isHeading: boolean;
}

interface Item {
  id: string;
  depth: number;
  parentId: string | null;
  number: string;
  text: string;
  isHeading: boolean;
}

interface Props {
  contractId: string;
  // Already collapse-filtered by the parent (visibleRows over the body region): a
  // collapsed section arrives as ONE row, its hidden sub-tree absent. Moving that
  // row moves its children too — the backend carries them via parent_id, so we
  // never send the descendants.
  rows: RearrangeRow[];
  // Ids that have children in the FULL body tree — drives which rows show a twirl.
  // A collapsed parent looks like a leaf in `rows`, so leaf-vs-parent can't be read
  // from the visible depth sequence; this set is the source of truth.
  parentIds: ReadonlySet<string>;
  // Shared with the read tree (node-level collapse). The twirl toggles THIS set via
  // onToggleCollapse, so collapse state stays consistent when leaving Rearrange.
  collapsed: ReadonlySet<string>;
  onToggleCollapse: (id: string) => void;
  selectedId: string | null;
  flashId: string | null;
  onSelect: (id: string) => void;
  onMoved: (movedId: string) => void;
}

// Derive parentId from the contiguous depth sequence (children always follow their
// parent immediately, deeper). A depth-0 row is a top-level clause → parentId null.
function buildItems(rows: RearrangeRow[]): Item[] {
  const stack: Item[] = [];
  return rows.map((r) => {
    while (stack.length && stack[stack.length - 1].depth >= r.depth) stack.pop();
    const parentId = stack.length ? stack[stack.length - 1].id : null;
    const item: Item = { id: r.id, depth: r.depth, parentId, number: r.number, text: r.text, isHeading: r.isHeading };
    stack.push(item);
    return item;
  });
}

// The contiguous descendant block of `id` (every following row deeper than it).
function descendantsOf(items: Item[], id: string): Item[] {
  const idx = items.findIndex((i) => i.id === id);
  if (idx < 0) return [];
  const d = items[idx].depth;
  const out: Item[] = [];
  for (let j = idx + 1; j < items.length && items[j].depth > d; j++) out.push(items[j]);
  return out;
}

// The view while dragging: the active row's descendants are removed so the sub-tree
// folds and travels as one unit.
function foldActive(items: Item[], activeId: UniqueIdentifier | null): Item[] {
  if (!activeId) return items;
  const drop = new Set(descendantsOf(items, String(activeId)).map((i) => i.id));
  return items.filter((i) => !drop.has(i.id));
}

// dnd-kit's projected-depth maths: clamp the horizontal drag offset to a legal depth
// between the row below (minDepth) and one past the row above (maxDepth), then read
// the new parent from the row above at that depth.
function getProjection(
  items: Item[],
  activeId: UniqueIdentifier,
  overId: UniqueIdentifier,
  dragOffset: number,
): { depth: number; parentId: string | null } {
  const overItemIndex = items.findIndex((i) => i.id === overId);
  const activeItemIndex = items.findIndex((i) => i.id === activeId);
  const activeItem = items[activeItemIndex];
  const newItems = arrayMove(items, activeItemIndex, overItemIndex);
  const previousItem = newItems[overItemIndex - 1];
  const nextItem = newItems[overItemIndex + 1];
  const dragDepth = Math.round(dragOffset / INDENT);
  const projectedDepth = activeItem.depth + dragDepth;
  const maxDepth = previousItem ? previousItem.depth + 1 : 0;
  const minDepth = nextItem ? nextItem.depth : 0;
  let depth = projectedDepth;
  if (projectedDepth >= maxDepth) depth = maxDepth;
  else if (projectedDepth < minDepth) depth = minDepth;

  const parentId = ((): string | null => {
    if (depth === 0 || !previousItem) return null;
    if (depth === previousItem.depth) return previousItem.parentId;
    if (depth > previousItem.depth) return previousItem.id;
    const above = newItems.slice(0, overItemIndex).reverse().find((i) => i.depth === depth);
    return above?.parentId ?? null;
  })();

  return { depth, parentId };
}

// The anchor (after / before) of `activeId` among its same-depth siblings in a flat
// list — descendants (depth > depth) are skipped; a shallower row ends the group.
function anchorIn(list: Item[], activeId: string, depth: number): { after: string | null; before: string | null } {
  const ai = list.findIndex((i) => i.id === activeId);
  let after: string | null = null;
  for (let i = ai - 1; i >= 0; i--) {
    if (list[i].depth < depth) break;
    if (list[i].depth === depth) {
      after = list[i].id;
      break;
    }
  }
  if (after) return { after, before: null };
  let before: string | null = null;
  for (let i = ai + 1; i < list.length; i++) {
    if (list[i].depth < depth) break;
    if (list[i].depth === depth) {
      before = list[i].id;
      break;
    }
  }
  return { after: null, before };
}

function SortableRow(props: {
  item: Item;
  depth: number;
  selected: boolean;
  flashed: boolean;
  isDropTarget: boolean;
  hasChildren: boolean;
  isCollapsed: boolean;
  onToggleCollapse: (id: string) => void;
  onSelect: (id: string) => void;
  registerRef: (id: string, el: HTMLElement | null) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: props.item.id,
    animateLayoutChanges: () => false,
  });
  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition: transition ?? undefined,
    paddingLeft: 10 + props.depth * INDENT,
  };
  return (
    <div
      ref={(el) => {
        setNodeRef(el);
        props.registerRef(props.item.id, el);
      }}
      style={style}
      className={[
        styles.dragRow,
        props.selected ? styles.selected : "",
        props.flashed ? styles.flash : "",
        props.isDropTarget ? styles.dragRowGhost : "",
      ].join(" ")}
      onClick={() => props.onSelect(props.item.id)}
    >
      <button
        type="button"
        className={styles.dragHandle}
        aria-label="Drag to move clause"
        title="Drag to reorder or nest"
        {...attributes}
        {...listeners}
        onClick={(e) => e.stopPropagation()}
      >
        ⠿
      </button>
      {props.hasChildren ? (
        // Twirl shares the parent's `collapsed` set. Drag activation lives on the
        // handle's listeners only, so this never starts a drag; stopPropagation on
        // pointerdown + click keeps it from selecting/activating the row either.
        <button
          type="button"
          className={styles.twirl}
          aria-label={props.isCollapsed ? "Expand" : "Collapse"}
          aria-expanded={!props.isCollapsed}
          title={props.isCollapsed ? "Expand" : "Collapse"}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            props.onToggleCollapse(props.item.id);
          }}
        >
          {props.isCollapsed ? "▸" : "▾"}
        </button>
      ) : (
        <span className={styles.twirlSpace} aria-hidden />
      )}
      <span className={styles.num}>{props.item.number || "—"}</span>
      <span className={[styles.text, props.item.isHeading ? styles.headingText : ""].join(" ")}>
        {props.item.text || <em>(no text)</em>}
      </span>
      {isDragging && <span className={styles.dragDepthCue} aria-hidden />}
    </div>
  );
}

export default function RearrangeTree({
  contractId,
  rows,
  parentIds,
  collapsed,
  onToggleCollapse,
  selectedId,
  flashId,
  onSelect,
  onMoved,
}: Props) {
  const [items, setItems] = useState<Item[]>(() => buildItems(rows));
  const [activeId, setActiveId] = useState<UniqueIdentifier | null>(null);
  const [overId, setOverId] = useState<UniqueIdentifier | null>(null);
  const [offsetLeft, setOffsetLeft] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLElement>());

  // Resync from the authoritative tree whenever the parent refetches (post-move) or
  // edits land — props are the source of truth; local `items` only holds the brief
  // optimistic order between drop and refetch.
  useEffect(() => {
    setItems(buildItems(rows));
  }, [rows]);

  // Scroll the flashed (just-moved) row into view once it exists in the resynced list.
  useEffect(() => {
    if (!flashId) return;
    rowRefs.current.get(flashId)?.scrollIntoView({ block: "center", behavior: "auto" });
  }, [flashId, items]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const foldedItems = useMemo(() => foldActive(items, activeId), [items, activeId]);
  const sortedIds = useMemo(() => foldedItems.map((i) => i.id), [foldedItems]);
  const projected =
    activeId && overId ? getProjection(foldedItems, activeId, overId, offsetLeft) : null;

  function resetDrag() {
    setActiveId(null);
    setOverId(null);
    setOffsetLeft(0);
  }

  function onDragStart({ active }: DragStartEvent) {
    setError(null);
    setActiveId(active.id);
    setOverId(active.id);
  }
  function onDragMove({ delta }: DragMoveEvent) {
    setOffsetLeft(delta.x);
  }
  function onDragOver({ over }: DragOverEvent) {
    setOverId(over?.id ?? null);
  }

  async function onDragEnd({ active, over }: DragEndEvent) {
    const snapshot = items;
    const folded = foldActive(items, active.id);
    resetDrag();
    if (!over) return;

    const proj = getProjection(folded, active.id, over.id, offsetLeft);
    const activeStr = String(active.id);
    const activeItem = items.find((i) => i.id === activeStr);
    if (!activeItem) return;

    // Optimistic flat list: arrayMove the active row, retag its depth/parent, then
    // reinsert its (saved) descendants right after it with the same depth shift.
    const depthDelta = proj.depth - activeItem.depth;
    const saved = descendantsOf(items, activeStr);
    const activeIdx = folded.findIndex((i) => i.id === activeStr);
    const overIdx = folded.findIndex((i) => i.id === over.id);
    const moved = arrayMove(folded, activeIdx, overIdx);
    const rebuilt: Item[] = [];
    for (const it of moved) {
      if (it.id === activeStr) {
        rebuilt.push({ ...it, depth: proj.depth, parentId: proj.parentId });
        for (const d of saved) rebuilt.push({ ...d, depth: d.depth + depthDelta });
      } else {
        rebuilt.push(it);
      }
    }

    const next = anchorIn(rebuilt, activeStr, proj.depth);
    const prev = anchorIn(items, activeStr, activeItem.depth);
    const unchanged =
      proj.parentId === activeItem.parentId && next.after === prev.after && next.before === prev.before;
    if (unchanged) return;

    setItems(rebuilt);
    try {
      const res = await moveNode(contractId, activeStr, {
        parent_id: proj.parentId,
        after_node_id: next.after,
        before_node_id: next.before,
      });
      if (res.moved) onMoved(res.node_id);
      else setItems(snapshot);
    } catch (e) {
      setItems(snapshot);
      setError(
        e instanceof Error && e.message ? e.message : "Can't move a clause inside itself.",
      );
    }
  }

  if (items.length === 0) {
    return <p className={styles.rearrangeEmpty}>No clauses to rearrange.</p>;
  }

  return (
    <div className={styles.rearrange}>
      <div className={styles.rearrangeBanner}>
        Drag the handle to reorder; drag sideways to nest under another clause. Front- and
        back-matter stay put.
      </div>
      {error && (
        <p className={styles.rearrangeError} role="alert">
          {error}
        </p>
      )}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
        // Edge auto-scroll (FIX 3): @dnd-kit walks up from the dragged row and binds
        // to the nearest scrollable ancestor — the cockpit tree panel (.tree,
        // overflow-y:auto). y-only (x:0) so a sideways reparent drag never scrolls
        // horizontally; a drag near the top/bottom edge scrolls the long list.
        autoScroll={{ threshold: { x: 0, y: 0.2 }, acceleration: 12 }}
        onDragStart={onDragStart}
        onDragMove={onDragMove}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={resetDrag}
      >
        <SortableContext items={sortedIds} strategy={verticalListSortingStrategy}>
          {foldedItems.map((item) => (
            <SortableRow
              key={item.id}
              item={item}
              depth={item.id === activeId && projected ? projected.depth : item.depth}
              selected={selectedId === item.id}
              flashed={flashId === item.id}
              isDropTarget={item.id === activeId && !!projected}
              hasChildren={parentIds.has(item.id)}
              isCollapsed={collapsed.has(item.id)}
              onToggleCollapse={onToggleCollapse}
              onSelect={onSelect}
              registerRef={(id, el) => {
                if (el) rowRefs.current.set(id, el);
                else rowRefs.current.delete(id);
              }}
            />
          ))}
        </SortableContext>
      </DndContext>
    </div>
  );
}
