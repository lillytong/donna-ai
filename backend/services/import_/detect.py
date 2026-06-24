"""AI-assisted entity detection for import (Mode A step 2, DD-10/11/12).

Low-consequence extraction (DD-35) — runs on Haiku, output is human-verified in
the import-review UI, so the model tier matches the stakes. Returns structured
candidates; never mutates the tree.
"""

from __future__ import annotations

from backend.models.extraction import Extraction
from backend.prompts.utils import render
from backend.services.llm import complete


def _extract_json(text: str) -> str:
    """Tolerate a model that wraps JSON in prose or ```json fences."""
    s = text.strip()
    if "```" in s:
        block = s.split("```", 2)[1]
        s = block[4:] if block.startswith("json") else block
    start, end = s.find("{"), s.rfind("}")
    return s[start : end + 1] if start != -1 and end != -1 else s


async def detect_entities(clause_text: str) -> Extraction:
    prompt = render("extract_entities_v1.txt", clause_text=clause_text)
    result = await complete(
        tier="low",
        messages=[{"role": "user", "content": prompt}],
        caller="import.detect_entities",
    )
    return Extraction.model_validate_json(_extract_json(result.text))
