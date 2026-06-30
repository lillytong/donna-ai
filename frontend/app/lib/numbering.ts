// Re-derive positional outline numbers from a depth-ordered node list — mirrors
// the backend `derive_numbers`. Used to renumber live when a node is promoted or
// demoted, so the number always follows the structure (DD-02).
//
// The numbering LEVEL is the clause-ancestry depth (nearest preceding shallower
// node = parent), computed by the same stack walk as `deriveParents`, NOT the raw
// depth. This makes a clause descend at most one numbering level below its nearest
// clause-ancestor, so a depth gap (clause nested under a non-clause lead-in/body, or
// after an operator demote/move) does not emit a phantom `.0` level. Contiguous
// (no-gap) sequences are unaffected: level == depth at every step.
export function deriveNumbers(depths: number[]): string[] {
  const counters: number[] = [];
  const stack: number[] = []; // tree-depths of the current clause-ancestor chain
  return depths.map((d) => {
    while (stack.length && stack[stack.length - 1] >= d) stack.pop();
    const level = stack.length; // numbering level = clause-ancestry depth (nearest-shallower = parent)
    stack.push(d);
    counters.length = level + 1; // drop any deeper counters
    for (let i = 0; i <= level; i++) if (counters[i] === undefined) counters[i] = 0;
    counters[level] += 1;
    return counters.slice(0, level + 1).join(".");
  });
}

// Re-derive each node's parent from the document-ordered (index, depth) sequence
// via a stack walk: a node's parent is the nearest preceding node at depth-1.
// This mirrors the backend tree_builder (parent = last_at_depth[depth-1]), so for
// untouched nodes it reproduces the original parent_index exactly, while honoring
// the operator's promote/demote (level ±) edits as real structural reparenting.
// Returns the parent node's `index` per row (null for a depth-0 root).
export function deriveParents(rows: { index: number; depth: number }[]): (number | null)[] {
  const stack: { index: number; depth: number }[] = [];
  return rows.map((r) => {
    while (stack.length && stack[stack.length - 1].depth >= r.depth) stack.pop();
    const parent = stack.length ? stack[stack.length - 1].index : null;
    stack.push(r);
    return parent;
  });
}
