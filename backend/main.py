"""FastAPI app assembly. Routes are thin and live in api/; logic lives in services/."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api import (
    audit,
    clause_draft,
    clause_search,
    cross_references,
    defined_terms,
    donna,
    donna_recommendations,
    export,
    health,
    imports,
    issue_export,
    issues,
    nodes,
    redline,
    settings,
)
from backend.db import close_pool, open_pool

# Local dev: the Next.js frontend calls this API cross-origin. Single source of truth
# for the allowed origin — reused by the CORS middleware and the 500 handler below.
DEV_ORIGIN = "http://localhost:3000"


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a 500 that still carries the CORS header.

    ServerErrorMiddleware sits outside CORSMiddleware, so a 500 it raises never passes
    back through the inner CORS layer and would reach the browser headerless — surfacing
    as a misleading "Failed to fetch". Re-attach the header here (echoing the origin only
    when allowed) so the frontend sees the real error. The body stays generic: no
    exception detail or traceback is leaked to the client.
    """
    headers: dict[str, str] = {}
    if request.headers.get("origin") == DEV_ORIGIN:
        headers["Access-Control-Allow-Origin"] = DEV_ORIGIN
    return JSONResponse(
        status_code=500, content={"detail": "Internal Server Error"}, headers=headers
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await open_pool()
    yield
    await close_pool()


app = FastAPI(title="donna.ai", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[DEV_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
    # Cross-origin JS can't read a response header unless it's explicitly exposed. The
    # .docx export routes carry the real filename in Content-Disposition; without this the
    # browser hides it and the frontend falls back to a generic name ("contract.docx").
    expose_headers=["Content-Disposition"],
)
app.add_exception_handler(Exception, _unhandled_exception_handler)

app.include_router(health.router)
app.include_router(imports.router)
app.include_router(settings.router)
app.include_router(issues.router)
app.include_router(nodes.router)
app.include_router(audit.router)
app.include_router(clause_search.router)
app.include_router(export.router)
app.include_router(defined_terms.router)
app.include_router(issue_export.router)
app.include_router(redline.router)
app.include_router(donna.router)
app.include_router(donna_recommendations.router)
app.include_router(clause_draft.router)
app.include_router(cross_references.router)
