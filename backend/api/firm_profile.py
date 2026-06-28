"""Global firm-profile route (F32 v1, DD-90) — thin (CLAUDE.md): validate, call the repo,
return. The operator-authored, firm-level free-text mandate the future Settings editor
reads/writes and Donna's revision-recommend grounding injects.

  * GET /firm-profile        -> {content}
  * PUT /firm-profile {content} -> the updated {content} (singleton upsert)
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.db import acquire
from backend.models.firm_profile import FirmProfile
from backend.services.firm_profile_repo import get_firm_profile, set_firm_profile

router = APIRouter()


@router.get("/firm-profile", response_model=FirmProfile)
async def read_firm_profile() -> FirmProfile:
    async with acquire() as conn:
        return FirmProfile(content=await get_firm_profile(conn))


@router.put("/firm-profile", response_model=FirmProfile)
async def write_firm_profile(payload: FirmProfile) -> FirmProfile:
    async with acquire() as conn:
        await set_firm_profile(conn, payload.content)
        return FirmProfile(content=await get_firm_profile(conn))
