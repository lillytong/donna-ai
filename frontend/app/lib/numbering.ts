// Re-derive positional outline numbers from a depth-ordered node list — mirrors
// the backend `derive_numbers`. Used to renumber live when a node is promoted or
// demoted, so the number always follows the structure (DD-02).

export function deriveNumbers(depths: number[]): string[] {
  const counters: number[] = [];
  return depths.map((d) => {
    counters.length = d + 1; // drop any deeper counters
    for (let i = 0; i <= d; i++) if (counters[i] === undefined) counters[i] = 0;
    counters[d] += 1;
    return counters.slice(0, d + 1).join(".");
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
