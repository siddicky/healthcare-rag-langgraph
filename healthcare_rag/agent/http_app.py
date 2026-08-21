from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Final, override
from uuid import UUID

from anyio import to_thread
from langsmith import Client
from langsmith.utils import LangSmithError
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

FEEDBACK_PROJECT_ENV: Final = "LANGSMITH_FEEDBACK_PROJECT_ID"


class FeedbackProjectConfigurationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return f"{FEEDBACK_PROJECT_ENV}: {self.reason}"


def validate_feedback_project(
    probe: Callable[[str], None] | None = None,
) -> str:
    """Validate the dedicated run-less feedback project before serving traffic."""
    raw_project_id = os.getenv(FEEDBACK_PROJECT_ENV, "")
    try:
        project_id = str(UUID(raw_project_id))
    except ValueError:
        raise FeedbackProjectConfigurationError("missing or invalid UUID") from None
    try:
        if probe is None:
            _ = next(
                Client().list_feedback(
                    feedback_key=["member_feedback"], session=[project_id]
                ),
                None,
            )
        else:
            probe(project_id)
    except LangSmithError as error:
        raise FeedbackProjectConfigurationError(
            "project existence probe failed"
        ) from error
    return project_id


@asynccontextmanager
async def lifespan(_app: Starlette):
    _ = await to_thread.run_sync(validate_feedback_project)
    yield


async def internal_version(request: Request) -> Response:
    """Return the remote Agent Server version only to the dual-secret principal."""
    if request.user.identity != "internal" or request.user.get("role") != "internal":
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    from langgraph_api import __version__

    return JSONResponse({"version": __version__})


app = Starlette(
    lifespan=lifespan,
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
    "FeedbackProjectConfigurationError",
    "app",
    "internal_version",
    "reservation_id",
    "validate_feedback_project",
]
