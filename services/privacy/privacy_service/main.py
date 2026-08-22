"""HTTP surface for the privacy engine.

Routes:
  GET  /ok       liveness, unauthenticated, no body details
  GET  /health   readiness + pinned versions + entity inventory (bearer)
  POST /analyze  presidio spans for one text (bearer)

The service never logs request text. Every failure leaves as a stable code.
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Final

from anyio import to_thread
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from privacy_service.engine import (
    DEFAULT_SCORE_THRESHOLD,
    ENTITY_TYPES,
    MAX_INPUT_BYTES,
    Engine,
    EngineError,
)

logger = logging.getLogger("privacy_service")
TOKEN_ENV: Final = "PRIVACY_SERVICE_TOKEN"


class AnalyzeRequest(BaseModel):
    text: str = Field(max_length=MAX_INPUT_BYTES)
    entities: list[str] | None = None
    score_threshold: float = Field(default=DEFAULT_SCORE_THRESHOLD, ge=0.0, le=1.0)


class SpanOut(BaseModel):
    start: int
    end: int
    entity_type: str
    score: float


class AnalyzeResponse(BaseModel):
    results: list[SpanOut]


class HealthResponse(BaseModel):
    status: str
    analyzer_version: str
    spacy_version: str
    model_name: str
    model_version: str
    entities: list[str]


def _required_token() -> str:
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is not set")
    return token


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.token = _required_token()
    app.state.engine = await to_thread.run_sync(Engine)
    logger.info("privacy engine ready", extra={"entities": len(app.state.engine.info.entities)})
    yield


app = FastAPI(title="healthcare-rag privacy service", lifespan=lifespan, docs_url=None, redoc_url=None)
_bearer = HTTPBearer(auto_error=False)


def authorize(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected: str = request.app.state.token
    presented = credentials.credentials if credentials is not None else ""
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


@app.get("/ok")
async def ok() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(authorize)])
async def health(request: Request) -> HealthResponse:
    info = request.app.state.engine.info
    return HealthResponse(
        status="ready",
        analyzer_version=info.analyzer_version,
        spacy_version=info.spacy_version,
        model_name=info.model_name,
        model_version=info.model_version,
        entities=list(info.entities),
    )


@app.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(authorize)])
async def analyze(request: Request, body: AnalyzeRequest) -> AnalyzeResponse:
    if len(body.text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="PRIVACY_INPUT_TOO_LARGE")
    entities = body.entities or list(ENTITY_TYPES)
    unknown = set(entities) - set(ENTITY_TYPES)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="PRIVACY_UNKNOWN_ENTITY")
    engine: Engine = request.app.state.engine
    try:
        spans = await to_thread.run_sync(engine.analyze, body.text, entities, body.score_threshold)
    except EngineError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.code) from None
    return AnalyzeResponse(
        results=[SpanOut(start=s.start, end=s.end, entity_type=s.entity_type, score=s.score) for s in spans]
    )
