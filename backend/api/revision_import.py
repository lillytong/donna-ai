"""Mode B revision-import route (F03b) — thin (CLAUDE.md): validate, call the
service, return the session summary.

`POST /contracts/{contract_id}/revisions/import?source=counterparty|legal` with the
clean revision .docx as the raw request body (application/octet-stream) — same
upload convention as the Mode A import route, which avoids the `python-multipart`
dependency. `filename` is an optional query param recorded on the session.

The service raises typed `RevisionImportError`s (tracked-changes → 422, no baseline
→ 409, session already open → 409); they are mapped to HTTP here so the service
stays framework-free.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from backend.db import acquire
from backend.models.revision_import import (
    RevisionImportRequest,
    RevisionImportResponse,
    RevisionSource,
)
from backend.services.donna.revision_recommend import recommend_on_import
from backend.services.import_.revision_import import RevisionImportError, import_revision

router = APIRouter()

_DOCX_MAGIC = b"PK\x03\x04"  # .docx is a ZIP container


def _write_temp(data: bytes) -> str:
    fd, name = tempfile.mkstemp(suffix=".docx")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return name


@router.post("/contracts/{contract_id}/revisions/import", response_model=RevisionImportResponse)
async def import_revision_route(
    contract_id: str,
    source: RevisionSource,
    request: Request,
    background: BackgroundTasks,
    filename: str | None = None,
) -> RevisionImportResponse:
    data = await request.body()
    if not data.startswith(_DOCX_MAGIC):
        raise HTTPException(status_code=400, detail="expected .docx (ZIP) bytes")
    path = await asyncio.to_thread(_write_temp, data)
    try:
        async with acquire() as conn:
            response = await import_revision(
                conn,
                contract_id,
                path,
                RevisionImportRequest(source=source, source_filename=filename),
            )
    except RevisionImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        await asyncio.to_thread(os.unlink, path)
    # F03c auto-run: now that the import has committed, pre-analyse the freshly staged changes
    # in the background so the two-pane review opens with Donna's verdict / counter-language
    # already populated. Post-commit, non-blocking, failure-isolated (mirrors F30's
    # distill_on_issue_close) — a recommendation error can never fail the already-successful
    # import, and the cost guard inside the task skips oversized diffs.
    background.add_task(recommend_on_import, response.session_id, response.changes_count)
    return response
