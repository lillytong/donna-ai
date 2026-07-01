// Re-derive positional outline numbers from a depth-ordered node list — mirrors
// the backend `derive_numbers` + `derive_enumerators` + `_number_for`. Used to
// renumber live when a node is promoted, demoted, or deleted so the number always
// follows the structure (DD-02), and so block enumerated items keep their derived
// "(a)"/"(1)" marker rather than being decimal-renumbered (F03f / DD-99).
//
// The numbering LEVEL of a decimal clause is its clause-ancestry depth (nearest
// preceding shallower numbered node = parent), computed by a stack walk, NOT the raw
// depth — so a depth gap (clause under a non-clause lead-in, or after a demote/move)
// does not emit a phantom `.0` level. An enumerated node consumes NO clause position:
// it is displayed as `<parent decimal> + (marker)` where the marker is derived from
// its ordinal among consecutive same-format enumerated siblings (restart per run),
// mirroring the backend exactly. A literal-marker enumerated item (no captured
// format — the "(a)" lives in its body text) is skipped entirely and carries no
// number, since its marker is already in the text.

export interface NumberingNode {
  depth: number;
  // True for a block enumerated item: a captured auto-numbered list format OR a
  // literal paren marker in the body (see isBlockEnumerator). Either way it is not
  // decimal-renumbered.
  enumerated: boolean;
  // Captured Word list format for an auto-numbered item; null for an ordinary clause
  // or a literal-marker item. Selects the marker glyph.
  enumeratorFormat: string | null;
}

const ROMAN_LABELS: ReadonlySet<string> = new Set([
  "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
  "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
]);

const ENUM_MARKER = /^\s*\(([A-Za-z]{1,4})\)/;

// Mirror of backend numbering.is_block_enumerator: text opens with a single alpha
// letter or a roman numeral in parens. Curated so a parenthetical word ("(or) …")
// is never mistaken for an enumerator (DD-98).
export function isBlockEnumerator(text: string): boolean {
  const m = ENUM_MARKER.exec(text);
  if (m === null) return false;
  const label = m[1].toLowerCase();
  return (label.length === 1 && /[a-z]/.test(label)) || ROMAN_LABELS.has(label);
}

// 1->a, 2->b, … 26->z, 27->aa (bijective base-26, lowercase). Mirrors backend _alpha.
function alpha(n: number): string {
  let s = "";
  let x = n;
  while (x > 0) {
    const r = (x - 1) % 26;
    x = Math.floor((x - 1) / 26);
    s = String.fromCharCode(97 + r) + s;
  }
  return s;
}

// Standard lowercase roman numeral for n>=1. Mirrors backend _roman.
function roman(n: number): string {
  const table: Array<[number, string]> = [
    [1000, "m"], [900, "cm"], [500, "d"], [400, "cd"], [100, "c"],
    [90, "xc"], [50, "l"], [40, "xl"], [10, "x"],
    [9, "ix"], [5, "v"], [4, "iv"], [1, "i"],
  ];
  let out = "";
  let x = n;
  for (const [value, sym] of table) {
    while (x >= value) {
      out += sym;
      x -= value;
    }
  }
  return out;
}

// The parenthesised marker glyph for the ordinal-th (1-based) item of a list in
// `fmt`. Mirrors backend format_enumerator (incl. the decimal branch, DD-99 amended).
export function formatEnumerator(ordinal: number, fmt: string | null): string {
  switch (fmt) {
    case "lowerLetter": return `(${alpha(ordinal)})`;
    case "upperLetter": return `(${alpha(ordinal).toUpperCase()})`;
    case "lowerRoman": return `(${roman(ordinal)})`;
    case "upperRoman": return `(${roman(ordinal).toUpperCase()})`;
    case "decimal": return `(${ordinal})`;
    default: return "";
  }
}

export function deriveNumbers(nodes: NumberingNode[]): string[] {
  const counters: number[] = [];
  const stack: Array<{ depth: number; number: string }> = []; // numbered-clause ancestors
  // Enumeration run state PER DEPTH (not one flat run): a list that resumes after a deeper
  // sub-list must continue, not restart. runFmt[d]/runCount[d] hold the open run at depth d;
  // returning to a shallower node drops the deeper entries, and a non-enumerated node at
  // depth d breaks the run there — mirroring backend derive_enumerators (group by sibling).
  // This makes (a)(A)(B)(b)(c) number correctly, and a re-indent (bringing a stray second
  // (a) up to its siblings' depth) re-derive it to (b).
  const runFmt: Array<string | null> = [];
  const runCount: number[] = [];

  return nodes.map((node) => {
    const d = node.depth;
    while (stack.length && stack[stack.length - 1].depth >= d) stack.pop();
    // Close runs deeper than this node.
    runFmt.length = d + 1;
    runCount.length = d + 1;

    if (node.enumerated) {
      if (node.enumeratorFormat === null) return ""; // literal marker — already in body
      const parentNumber = stack.length ? stack[stack.length - 1].number : "";
      if (runFmt[d] === node.enumeratorFormat) runCount[d] += 1;
      else {
        runCount[d] = 1;
        runFmt[d] = node.enumeratorFormat;
      }
      return `${parentNumber}${formatEnumerator(runCount[d], node.enumeratorFormat)}`;
    }

    // Ordinary clause: breaks any enumeration run at this depth.
    runFmt[d] = null;
    runCount[d] = 0;

    const level = stack.length; // numbering level = numbered-ancestry depth
    counters.length = level + 1; // drop any deeper counters
    for (let i = 0; i <= level; i++) if (counters[i] === undefined) counters[i] = 0;
    counters[level] += 1;
    const number = counters.slice(0, level + 1).join(".");
    stack.push({ depth: d, number });
    return number;
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
