from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from healthcare_rag.agent.feedback import post_feedback
from healthcare_rag.agent.perimeter_middleware import MemberPerimeterMiddleware
from healthcare_rag.agent.uploads import (
    RESERVATION_NS,
    get_upload_status,
    post_upload,
    reservation_id,
)


async def internal_version(request: Request) -> Response:
    """Return the remote Agent Server version only to the dual-secret principal."""
    if request.user.identity != "internal" or request.user.get("role") != "internal":
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    from langgraph_api import __version__

    return JSONResponse({"version": __version__})


app = Starlette(
    routes=[
        Route("/coach/internal/version", internal_version, methods=["GET"]),
        Route("/coach/uploads", post_upload, methods=["POST"]),
        Route(
            "/coach/uploads/{upload_id:str}/status", get_upload_status, methods=["GET"]
        ),
        Route("/coach/feedback", post_feedback, methods=["POST"]),
    ],
)
app.add_middleware(MemberPerimeterMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("COACH_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


__all__ = [
    "RESERVATION_NS",
    "app",
    "internal_version",
    "reservation_id",
]
