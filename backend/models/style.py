"""Style-config model — the DD-37 JSONB schema as a typed view.

`contracts.style_config` is an open JSONB blob (passthrough dict at the settings
layer); the renderer needs structured access to it, so this model parses that
blob into typed per-depth level styles. Unknown keys are ignored and missing
fields fall back to the DD-37 defaults, so a `{}` config renders with house-style
defaults rather than failing.

`caps` is a render-time uppercase transform, never a stored mutation (DD-37 /
§2.1): the renderer applies it as the Word `w:caps` run property (all-caps
*display*), leaving the underlying text original-case so round-tripping recovers
it exactly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NumberingScheme = Literal["decimal", "mixed"]


class LevelStyle(BaseModel):
    """Formatting for one outline depth (DD-37 `levels.<depth>`)."""

    bold: bool = False
    caps: bool = False
    underline: bool = False
    font_size_pt: int | None = None


class StyleConfig(BaseModel):
    """The DD-37 style schema. `levels` keys are integer outline depths
    (0 = article, 1 = section, …); JSON string keys are coerced on parse."""

    font: str = "Times New Roman"
    numbering_scheme: NumberingScheme = "decimal"
    body_font_size_pt: int = 10
    indent_per_level_pt: int = 18
    page_breaks_before_articles: bool = False
    levels: dict[int, LevelStyle] = Field(default_factory=dict)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> StyleConfig:
        data = {k: v for k, v in raw.items() if k in cls.model_fields}
        levels_raw = data.get("levels") or {}
        data["levels"] = {int(k): LevelStyle(**v) for k, v in levels_raw.items()}
        return cls(**data)

    def level(self, depth: int) -> LevelStyle:
        """Style for `depth`: exact match, else the deepest defined level above it
        (a depth-4 sub-clause inherits the depth-3 body style), else the default."""
        if depth in self.levels:
            return self.levels[depth]
        below = [d for d in self.levels if d < depth]
        if below:
            return self.levels[max(below)]
        return LevelStyle()
