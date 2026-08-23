from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import Auth
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from server.auth import merge_scope_filter, require_scope_match


def _assistant_record(graph_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "assistant_id": graph_id,
        "graph_id": graph_id,
        "name": graph_id,
        "metadata": {},
        "config": {},
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


def _all_assistants(request: Request) -> list[dict[str, Any]]:
    graphs: dict[str, str] = getattr(request.app.state, "config", None).graphs if hasattr(request.app.state, "config") else {}
    # fallback for test helper that sets config directly
    if not graphs:
        cfg = getattr(request.app.state, "config", None)
        if cfg is not None and hasattr(cfg, "graphs"):
            graphs = dict(cfg.graphs)
    return [_assistant_record(gid) for gid in graphs]


async def _run_policy(request: Request, value: dict[str, Any]) -> dict[str, Any] | None:
    engine = getattr(request.app.state, "auth_engine", None)
    user = request.scope.get("user")
    if engine is None or user is None:
        return None
    return await engine.run_policy("assistants", "read", user, value)  # type: ignore[arg-type]


async def search_assistants(request: Request) -> JSONResponse:
    try:
        body: dict[str, Any] = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    except (json.JSONDecodeError, Exception):
        body = {}
    # empty body with no json still {}
    if not isinstance(body, dict):
        body = {}

    graph_id = body.get("graph_id") if isinstance(body.get("graph_id"), str) else None
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    limit = body.get("limit") if isinstance(body.get("limit"), int) else 10
    offset = body.get("offset") if isinstance(body.get("offset"), int) else 0
    # also support legacy: metadata filter as in AssistantsSearch
    if not isinstance(metadata, dict):
        metadata = {}

    policy_value: dict[str, Any] = {"graph_id": graph_id, "metadata": metadata, "limit": limit, "offset": offset}
    try:
        scope_filter = await _run_policy(request, policy_value)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    requested_filter: dict[str, Any] = {}
    if graph_id is not None:
        requested_filter["graph_id"] = graph_id
    # metadata filtering for assistants is simple containment check
    if metadata:
        requested_filter.update(metadata)

    if scope_filter is not None:
        merged = merge_scope_filter(requested_filter, scope_filter)
    else:
        merged = requested_filter

    all_items = _all_assistants(request)

    def _matches(item: dict[str, Any], flt: dict[str, Any]) -> bool:
        for k, v in flt.items():
            if item.get(k) != v:
                # metadata keys are top-level for our minimal shape; use metadata dict if needed
                if k in item.get("metadata", {}):
                    if item["metadata"][k] != v:
                        return False
                else:
                    return False
        return True

    filtered = [a for a in all_items if _matches(a, merged)]
    # pagination
    sliced = filtered[offset : offset + limit] if limit is not None else filtered[offset:]
    return JSONResponse(sliced)


async def get_assistant(request: Request) -> JSONResponse:
    assistant_id = request.path_params.get("assistant_id", "")
    all_items = _all_assistants(request)
    target = next((a for a in all_items if a["assistant_id"] == assistant_id), None)
    if target is None:
        return JSONResponse({"detail": "Assistant not found"}, status_code=404)

    policy_value: dict[str, Any] = {"assistant_id": assistant_id, "metadata": target.get("metadata", {})}
    try:
        scope_filter = await _run_policy(request, policy_value)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    if scope_filter is not None:
        try:
            require_scope_match(target, scope_filter)
            # also check graph_id filter semantics: if scope says graph_id coach, require match
            if "graph_id" in scope_filter and target.get("graph_id") != scope_filter["graph_id"]:
                return JSONResponse({"detail": "Assistant not found"}, status_code=404)
        except Auth.exceptions.HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        # generic check via metadata
        for k, v in scope_filter.items():
            if target.get(k) != v and target.get("metadata", {}).get(k) != v:
                return JSONResponse({"detail": "Assistant not found"}, status_code=404)

    return JSONResponse(target)


routes: list[Route] = [
    Route("/assistants/search", search_assistants, methods=["POST"]),
    Route("/assistants/{assistant_id}", get_assistant, methods=["GET"]),
]
