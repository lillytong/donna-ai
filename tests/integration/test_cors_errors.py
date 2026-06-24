"""Unhandled-500 responses must still carry the CORS header.

ServerErrorMiddleware sits outside CORSMiddleware, so without the registered Exception
handler a 500 reaches the browser headerless and shows as a misleading "Failed to fetch".
These tests reuse the real wiring (DEV_ORIGIN + handler from backend.main) on a fresh app
with a route that raises, so the assertions exercise the actual fix. TestClient is built
with raise_server_exceptions=False so we observe the 500 response instead of re-raising.
"""

from __future__ import annotations

from backend.main import DEV_ORIGIN, _unhandled_exception_handler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


def _boom_app() -> TestClient:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[DEV_ORIGIN],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom secret detail")

    return TestClient(app, raise_server_exceptions=False)


def test_500_carries_cors_header_for_allowed_origin() -> None:
    client = _boom_app()
    resp = client.get("/boom", headers={"Origin": DEV_ORIGIN})
    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == DEV_ORIGIN


def test_500_body_hides_traceback_and_exception_detail() -> None:
    client = _boom_app()
    resp = client.get("/boom", headers={"Origin": DEV_ORIGIN})
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal Server Error"}
    assert "Traceback" not in resp.text
    assert "kaboom secret detail" not in resp.text


def test_500_omits_cors_header_for_disallowed_origin() -> None:
    client = _boom_app()
    resp = client.get("/boom", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 500
    assert "access-control-allow-origin" not in resp.headers
