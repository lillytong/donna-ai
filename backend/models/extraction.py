"""Structured output of the import-time entity detection step (Mode A step 2).

These are *candidates* — AI-detected, human-verified on import (DD-10/11/12).
They are resolved into defined_terms / cross_references / parameter_references
rows after the operator confirms, never written blindly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DetectedTerm(BaseModel):
    term: str
    definition_hint: str | None = None


class DetectedCrossRef(BaseModel):
    text: str
    target_hint: str | None = None


class DetectedParameter(BaseModel):
    mention: str
    kind: str | None = None


class Extraction(BaseModel):
    defined_terms: list[DetectedTerm] = Field(default_factory=list)
    cross_references: list[DetectedCrossRef] = Field(default_factory=list)
    parameters: list[DetectedParameter] = Field(default_factory=list)
