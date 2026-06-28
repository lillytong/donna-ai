// House legal-doc bolding style guide — the SINGLE SOURCE OF TRUTH for both import
// panes (the parse-review "Source" panel and the revision-review document pane). Bold
// quoted defined terms ("Agreement") everywhere; in front/back matter additionally bold
// an allowlisted set of ALL-CAPS legal connectives (WHEREAS, NOW, THEREFORE, …).
//
// Why an allowlist and not a length rule: acronyms (DBO, JVA) are deliberately NOT in
// the list so they stay plain. No length rule can separate them from same-length
// connectives like AND / NOW — only an explicit allowlist can. The match is
// case-SENSITIVE (no `i` flag) so only the uppercase token matches ("NOW" bolds, "Now"
// does not).

// Curly quotes written as \u escapes so the source stays ASCII-only (TS1127 guard).
const QUOTED_TERM = "[\u201c\u201d\"][^\u201c\u201d\"]+[\u201c\u201d\"]";

const LEGAL_CAPS_WORDS = [
  "WHEREAS",
  "NOW",
  "THEREFORE",
  "BY",
  "AND",
  "BETWEEN",
  "AMONG",
  "IN",
  "WITNESS",
  "WHEREOF",
  "WITNESSETH",
  "RECITALS",
  "BACKGROUND",
  "PREAMBLE",
  "HEREBY",
  "HERETO",
  "HEREOF",
  "THEREOF",
];
const LEGAL_CAPS = "\\b(?:" + LEGAL_CAPS_WORDS.join("|") + ")\\b";

export function renderRich(
  text: string,
  capsBold: boolean,
  boldClassName: string,
): React.ReactNode {
  const re = new RegExp(capsBold ? `${QUOTED_TERM}|${LEGAL_CAPS}` : QUOTED_TERM, "g");
  const out: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <strong key={key++} className={boldClassName}>
        {m[0]}
      </strong>,
    );
    last = m.index + m[0].length;
    if (m.index === re.lastIndex) re.lastIndex++; // guard against a zero-length match
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
