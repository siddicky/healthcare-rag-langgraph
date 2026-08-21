from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from healthcare_rag.agent.feedback import post_feedback
from healthcare_rag.agent.perimeter_middleware import MemberPerimeterMiddleware
from healthcare_rag.agent.uploads import (
    RESERVATION_NS,
    get_upload_status,
    post_upload,
    reservation_id,
)

app = Starlette(
    routes=[
        Route("/coach/uploads", post_upload, methods=["POST"]),
        Route(
            "/coach/uploads/{upload_id:str}/status", get_upload_status, methods=["GET"]
        ),
        Route("/coach/feedback", post_feedback, methods=["POST"]),
    ]
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


__all__ = ["RESERVATION_NS", "app", "reservation_id"]
