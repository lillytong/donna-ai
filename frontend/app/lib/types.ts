// The import-review screen works on the parsed *candidate* tree (pre-commit),
// which carries the `uncertain` flag the parser sets. This is distinct from the
// persisted node shape — it exists only during the review step (F04).

export type NodeType = "heading" | "prose" | "table" | "appendix";

export interface CandidateNode {
  id: string;
  number: string; // derived clause number, e.g. "3.1.2"
  text: string;
  depth: number;
  contentType: NodeType;
  uncertain: boolean;
}

export interface SourcePara {
  number: string;
  text: string;
}
