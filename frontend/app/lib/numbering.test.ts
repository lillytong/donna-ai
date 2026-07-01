// Unit tests for lib/numbering deriveNumbers — the client-side mirror of the backend
// derive_numbers + derive_enumerators (F03f / DD-99). Run with Node's built-in test
// runner (no extra deps): `node --test app/lib/numbering.test.ts` (see package.json
// "test"). Excluded from the Next/tsc build via tsconfig "exclude".

import assert from "node:assert/strict";
import { test } from "node:test";

import { deriveNumbers, isBlockEnumerator, type NumberingNode } from "./numbering.ts";

const clause = (depth: number): NumberingNode => ({ depth, enumerated: false, enumeratorFormat: null });
const enumItem = (depth: number, fmt: string): NumberingNode => ({
  depth,
  enumerated: true,
  enumeratorFormat: fmt,
});

// A lead-in clause "1" with three enumerated children, per format.
const formatCases: Array<[string, string[]]> = [
  ["lowerLetter", ["1(a)", "1(b)", "1(c)"]],
  ["decimal", ["1(1)", "1(2)", "1(3)"]],
  ["upperLetter", ["1(A)", "1(B)", "1(C)"]],
  ["lowerRoman", ["1(i)", "1(ii)", "1(iii)"]],
  ["upperRoman", ["1(I)", "1(II)", "1(III)"]],
];

for (const [fmt, expected] of formatCases) {
  test(`enumerated children render the ${fmt} marker under the parent decimal`, () => {
    const nodes = [clause(0), enumItem(1, fmt), enumItem(1, fmt), enumItem(1, fmt)];
    const out = deriveNumbers(nodes);
    assert.deepEqual(out, ["1", ...expected]);
  });
}

test("a backbone decimal clause (1 / 1.1 / 1.2.1) is NOT swallowed by enumeration", () => {
  // 1, 1.1, 1.2, 1.2.1 — pure clauses keep their dotted outline.
  const nodes = [clause(0), clause(1), clause(1), clause(2)];
  const out = deriveNumbers(nodes);
  assert.deepEqual(out, ["1", "1.1", "1.2", "1.2.1"]);
});

test("backbone clause and an enumerated run coexist without cross-contamination", () => {
  // 1, 1.1 (lead-in), (a)(b)(c) under 1.1, then 1.2 (real sub-clause still numbers).
  const nodes = [
    clause(0), // 1
    clause(1), // 1.1 lead-in
    enumItem(2, "lowerLetter"), // 1.1(a)
    enumItem(2, "lowerLetter"), // 1.1(b)
    enumItem(2, "lowerLetter"), // 1.1(c)
    clause(1), // 1.2
  ];
  const out = deriveNumbers(nodes);
  assert.deepEqual(out, ["1", "1.1", "1.1(a)", "1.1(b)", "1.1(c)", "1.2"]);
});

test("deleting the first enumerated item renumbers the rest", () => {
  const full = [clause(0), enumItem(1, "lowerLetter"), enumItem(1, "lowerLetter"), enumItem(1, "lowerLetter")];
  assert.deepEqual(deriveNumbers(full), ["1", "1(a)", "1(b)", "1(c)"]);
  // Drop the first enumerated item (index 1) — survivors shift down.
  const survivors = [full[0], full[2], full[3]];
  assert.deepEqual(deriveNumbers(survivors), ["1", "1(a)", "1(b)"]);
});

test("a format change restarts the run at 1", () => {
  const nodes = [
    clause(0),
    enumItem(1, "lowerLetter"),
    enumItem(1, "lowerLetter"),
    enumItem(1, "lowerRoman"),
    enumItem(1, "lowerRoman"),
  ];
  assert.deepEqual(deriveNumbers(nodes), ["1", "1(a)", "1(b)", "1(i)", "1(ii)"]);
});

test("two runs split by a plain clause each restart at (a) (restart-per-run)", () => {
  const nodes = [
    clause(0), // 1
    enumItem(1, "lowerLetter"), // 1(a)
    enumItem(1, "lowerLetter"), // 1(b)
    clause(1), // 1.1 breaks the run
    enumItem(1, "lowerLetter"), // 1(a) again
    enumItem(1, "lowerLetter"), // 1(b)
  ];
  assert.deepEqual(deriveNumbers(nodes), ["1", "1(a)", "1(b)", "1.1", "1(a)", "1(b)"]);
});

test("a list resuming after a deeper sub-list continues, not restarts ((a)(A)(B)(b)(c))", () => {
  // 1.1 lead-in, (a) with a (A)(B) sub-list, then (b)(c) resume at (a)'s depth.
  // The deeper upperLetter run must NOT reset the lowerLetter run: (b) follows (a).
  const nodes = [
    clause(0), // 1
    clause(1), // 1.1 lead-in
    enumItem(2, "lowerLetter"), // 1.1(a)
    enumItem(3, "upperLetter"), // 1.1(A)
    enumItem(3, "upperLetter"), // 1.1(B)
    enumItem(2, "lowerLetter"), // 1.1(b) — resumes the (a) run, NOT a second (a)
    enumItem(2, "lowerLetter"), // 1.1(c)
  ];
  assert.deepEqual(deriveNumbers(nodes), [
    "1",
    "1.1",
    "1.1(a)",
    "1.1(A)",
    "1.1(B)",
    "1.1(b)",
    "1.1(c)",
  ]);
});

test("re-indent: a stray (b) at the sub-list depth derives (a), outdenting it fixes to (b)", () => {
  // Bug state: the would-be (b) sits one level too deep (at the (A)(B) sub-list depth),
  // so it starts a fresh lowerLetter run there and renders a SECOND (a).
  const buggy: NumberingNode[] = [
    clause(0), // 1
    clause(1), // 1.1
    enumItem(2, "lowerLetter"), // 1.1(a)
    enumItem(3, "upperLetter"), // 1.1(A)
    enumItem(3, "upperLetter"), // 1.1(B)
    enumItem(3, "lowerLetter"), // stray: deeper than its siblings -> derives 1.1(a)
  ];
  assert.deepEqual(deriveNumbers(buggy), [
    "1",
    "1.1",
    "1.1(a)",
    "1.1(A)",
    "1.1(B)",
    "1.1(a)", // the bug: a phantom second (a)
  ]);

  // Operator outdents the stray item up to its siblings' depth — it re-derives as (b).
  const fixed = buggy.map((n, i) => (i === 5 ? { ...n, depth: 2 } : n));
  assert.deepEqual(deriveNumbers(fixed), [
    "1",
    "1.1",
    "1.1(a)",
    "1.1(A)",
    "1.1(B)",
    "1.1(b)", // re-indent corrects the marker
  ]);
});

test("a literal-marker enumerated item carries no derived number (marker is in body)", () => {
  // enumerated true, no captured format -> "" (the "(a)" already lives in body text).
  const nodes: NumberingNode[] = [
    clause(0),
    { depth: 1, enumerated: true, enumeratorFormat: null },
  ];
  assert.deepEqual(deriveNumbers(nodes), ["1", ""]);
});

test("isBlockEnumerator accepts standard markers, rejects parenthetical words", () => {
  assert.ok(isBlockEnumerator("(a) the term"));
  assert.ok(isBlockEnumerator("(iv) the clause"));
  assert.ok(isBlockEnumerator("(B) recital"));
  assert.ok(!isBlockEnumerator("(the) parties"));
  assert.ok(!isBlockEnumerator("plain text"));
});
