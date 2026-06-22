"""Prompt rendering — the ONLY place slot-filling happens (CLAUDE.md).

Templates are plain `.txt` files in this directory with `{variable}` slots.
Literal braces in a template (e.g. JSON examples) must be escaped as `{{` `}}`.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def render(template_name: str, /, **variables: str) -> str:
    template = (_PROMPTS_DIR / template_name).read_text(encoding="utf-8")
    return template.format(**variables)
