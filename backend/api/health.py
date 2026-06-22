"""Health route — thin (CLAUDE.md): validate, call, return. Proves DB connectivity."""

from fastapi import APIRouter

from backend.db import acquire

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    async with acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "db": "ok"}
