// Mock candidate tree for the function-first review screen. Generic clauses —
// no real contract content. Stands in until the parse-preview endpoint is wired.

import type { CandidateNode, SourcePara } from "./types";

export const candidateNodes: CandidateNode[] = [
  { id: "n1", number: "1.", text: "Definitions", depth: 0, contentType: "heading", uncertain: false },
  { id: "n2", number: "1.1", text: "“Agreement” means this agreement and all schedules to it.", depth: 1, contentType: "prose", uncertain: false },
  { id: "n3", number: "1.2", text: "“Confidential Information” means information disclosed by either party.", depth: 1, contentType: "prose", uncertain: false },
  { id: "n4", number: "2.", text: "Term", depth: 0, contentType: "heading", uncertain: false },
  { id: "n5", number: "2.1", text: "This Agreement commences on the Effective Date and continues for three years.", depth: 1, contentType: "prose", uncertain: false },
  { id: "n6", number: "3.", text: "Confidentiality", depth: 0, contentType: "heading", uncertain: false },
  { id: "n7", number: "3.1", text: "Each party shall keep the other’s Confidential Information secret.", depth: 1, contentType: "prose", uncertain: false },
  { id: "n8", number: "3.2", text: "Exceptions", depth: 0, contentType: "heading", uncertain: true },
  { id: "n9", number: "3.2(a)", text: "information that is or becomes public through no fault of the receiving party;", depth: 2, contentType: "prose", uncertain: true },
  { id: "n10", number: "3.2(b)", text: "information already known to the receiving party before disclosure.", depth: 2, contentType: "prose", uncertain: false },
  { id: "n11", number: "4.", text: "Charges", depth: 0, contentType: "heading", uncertain: false },
  { id: "n12", number: "4.1", text: "The fees payable are set out in the table below.", depth: 1, contentType: "prose", uncertain: false },
  { id: "n13", number: "4.2", text: "Fee schedule", depth: 1, contentType: "table", uncertain: true },
  { id: "n14", number: "5.", text: "GOVERNING LAW AND JURISDICTION", depth: 1, contentType: "prose", uncertain: true },
  { id: "n15", number: "Sch 1", text: "Service Levels", depth: 0, contentType: "appendix", uncertain: false },
];

export const sourceParas: SourcePara[] = [
  { number: "1.", text: "Definitions" },
  { number: "1.1", text: "“Agreement” means this agreement and all schedules to it." },
  { number: "1.2", text: "“Confidential Information” means information disclosed by either party, whether orally or in writing, that is designated as confidential or would reasonably be understood to be confidential." },
  { number: "2.", text: "Term" },
  { number: "2.1", text: "This Agreement commences on the Effective Date and continues for three years unless terminated earlier in accordance with clause 6." },
  { number: "3.", text: "Confidentiality" },
  { number: "3.1", text: "Each party shall keep the other’s Confidential Information secret and shall not use it except for the purpose of performing its obligations under this Agreement." },
  { number: "3.2", text: "Exceptions. The obligations in clause 3.1 do not apply to:" },
  { number: "3.2(a)", text: "information that is or becomes public through no fault of the receiving party;" },
  { number: "3.2(b)", text: "information already known to the receiving party before disclosure." },
  { number: "4.", text: "Charges" },
  { number: "4.1", text: "The fees payable are set out in the table below." },
  { number: "4.2", text: "Fee schedule: [table]" },
  { number: "5.", text: "GOVERNING LAW AND JURISDICTION. This Agreement is governed by the laws of England and Wales." },
  { number: "Schedule 1", text: "Service Levels" },
];
