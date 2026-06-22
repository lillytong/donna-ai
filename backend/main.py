"""FastAPI app assembly. Routes are thin and live in api/; logic lives in services/."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import health, imports, settings
from backend.db import close_pool, open_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await open_pool()
    yield
    await close_pool()


app = FastAPI(title="donna.ai", lifespan=lifespan)

# Local dev: the Next.js frontend (:3000) calls this API (:8000) cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(imports.router)
app.include_router(settings.router)
