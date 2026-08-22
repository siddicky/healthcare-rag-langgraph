from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def ok_endpoint(request: Request) -> JSONResponse:
    readiness = request.app.state.readiness  # type: ignore[attr-defined]
    if not readiness.is_ready():
        return JSONResponse({"ok": False, "ready": False}, status_code=503)
    return JSONResponse({"ok": True})


async def info_endpoint(request: Request) -> JSONResponse:
    config = request.app.state.config  # type: ignore[attr-defined]
    return JSONResponse({"api_version": config.api_version})


routes = [
    Route("/ok", ok_endpoint, methods=["GET"]),
    Route("/info", info_endpoint, methods=["GET"]),
]
