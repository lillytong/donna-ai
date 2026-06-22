"""Entity detection parses structured model output. The LLM call is mocked
(integration boundary) — real API calls are exercised in system tests."""

from __future__ import annotations

from typing import Any

from backend.models.extraction import Extraction
from backend.services.import_ import detect

# A realistic response, including the markdown fence a model often adds.
_CANNED = """```json
{"defined_terms": [{"term": "Offtake Price", "definition_hint": "price per ton"}],
 "cross_references": [{"text": "Section 4.2", "target_hint": "4.2"}],
 "parameters": [{"mention": "USD 15/ton", "kind": "price"}]}
```"""


async def test_detect_entities_parses_structured_output(monkeypatch: Any) -> None:
    async def fake_complete(**_kwargs: Any) -> str:
        return _CANNED

    monkeypatch.setattr(detect, "complete", fake_complete)

    result = await detect.detect_entities(
        "The Offtake Price is set under Section 4.2 at USD 15/ton."
    )

    assert isinstance(result, Extraction)
    assert result.defined_terms[0].term == "Offtake Price"
    assert result.cross_references[0].text == "Section 4.2"
    assert result.parameters[0].mention == "USD 15/ton"


async def test_detect_entities_tolerates_unfenced_json(monkeypatch: Any) -> None:
    async def fake_complete(**_kwargs: Any) -> str:
        return 'Here you go: {"defined_terms": [], "cross_references": [], "parameters": []}'

    monkeypatch.setattr(detect, "complete", fake_complete)

    result = await detect.detect_entities("No entities here.")
    assert result.defined_terms == []
