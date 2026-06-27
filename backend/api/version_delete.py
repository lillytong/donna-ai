"""Version-delete route (DD-85 / DD-87 §5) — thin (CLAUDE.md).

`DELETE /contracts/{id}/snapshots/{snapshot_id}?confirm=<bool>` mirrors Mark-as-sent's
acknowledge two-call: `confirm=false` (default) returns a no-mutation PREVIEW (the
DD-85 warnings + what would happen); `confirm=true` executes the wipe and rolls the
working copy back when the latest version is deleted. 404 if the snapshot isn't the
contract's; 409 when a revision-session baseline rests on it (DD-87 §4d).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.db import acquire
from backend.models.version_delete import SnapshotDeleteResponse
from backend.services.version_delete import VersionDeleteConflict, delete_version

router = APIRouter()


@router.delete("/contracts/{contract_id}/snapshots/{snapshot_id}")
async def delete_snapshot_version(
    contract_id: str, snapshot_id: str, confirm: bool = False
) -> SnapshotDeleteResponse:
    async with acquire() as conn:
        try:
            result = await delete_version(conn, contract_id, snapshot_id, confirm=confirm)
        except VersionDeleteConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="snapshot not found for contract")
    return result
