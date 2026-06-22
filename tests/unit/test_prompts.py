"""Prompt rendering fills slots and leaves escaped JSON braces intact."""

from __future__ import annotations

from backend.prompts.utils import render


def test_render_fills_slot_and_keeps_json_braces() -> None:
    out = render("extract_entities_v1.txt", clause_text="UNIQUE_MARKER_123")
    assert "UNIQUE_MARKER_123" in out
    assert "{clause_text}" not in out  # slot was filled
    assert '"term"' in out  # escaped {{ }} survived as single braces
