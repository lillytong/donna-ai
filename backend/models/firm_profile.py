"""The global firm-profile document (F32 v1 / DD-90) — the operator-authored, firm-level
free-text mandate the Settings editor reads/writes and Donna's revision-recommend grounding
injects. Used as both the GET response and the PUT body (`content` is the whole document)."""

from __future__ import annotations

from pydantic import BaseModel


class FirmProfile(BaseModel):
    content: str
