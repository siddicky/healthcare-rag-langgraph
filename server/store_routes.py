from __future__ import annotations

from typing import Any

from langgraph_sdk import Auth
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def _validate_namespace(labels: list[str] | tuple[str, ...] | None) -> None:
    if labels is None:
        return
    if not isinstance(labels, (list, tuple)):
        raise ValueError("namespace must be list")
    for label in labels:
        if not isinstance(label, str) or not label:
            from starlette.exceptions import HTTPException as StarletteHTTP

            raise StarletteHTTP(status_code=422, detail="Namespace labels cannot be empty")
        if "." in label:
            from starlette.exceptions import HTTPException as StarletteHTTP

            raise StarletteHTTP(status_code=422, detail=f"Namespace labels cannot contain periods. Received: {'.'.join(labels)}")


def _store(request: Request):
    st = getattr(request.app.state, "storage", None)
    if st is None:
        raise RuntimeError("storage not initialized")
    return st.store


async def _run_policy(request: Request, action: str, value: dict[str, Any]) -> dict[str, Any] | None:
    engine = getattr(request.app.state, "auth_engine", None)
    user = request.scope.get("user")
    if engine is None or user is None:
        return None
    return await engine.run_policy("store", action, user, value)  # type: ignore[arg-type]


def _item_to_api(item) -> dict[str, Any] | None:
    if item is None:
        return None
    # Item has value, key, namespace, created_at, updated_at
    ns = list(item.namespace) if hasattr(item, "namespace") else []
    return {
        "namespace": ns,
        "key": item.key,
        "value": item.value,
        "created_at": item.created_at.isoformat() if hasattr(item.created_at, "isoformat") else str(item.created_at),
        "updated_at": item.updated_at.isoformat() if hasattr(item.updated_at, "isoformat") else str(item.updated_at),
    }


async def put_item(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "Invalid body"}, status_code=422)
    namespace = body.get("namespace")
    key = body.get("key")
    value = body.get("value")
    if not isinstance(namespace, list):
        return JSONResponse({"detail": "namespace is required and must be list"}, status_code=422)
    if not isinstance(key, str) or not key:
        return JSONResponse({"detail": "key is required"}, status_code=422)
    if not isinstance(value, dict):
        return JSONResponse({"detail": "value is required and must be object"}, status_code=422)
    try:
        _validate_namespace(namespace)
    except Exception as exc:
        status = getattr(exc, "status_code", 422)
        detail = getattr(exc, "detail", str(exc))
        return JSONResponse({"detail": detail}, status_code=status)

    policy_value: dict[str, Any] = {"namespace": namespace, "key": key, "value": value}
    try:
        await _run_policy(request, "put", policy_value)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    store = _store(request)
    # ttl and index passthrough if present
    ttl = body.get("ttl")
    index = body.get("index")
    kwargs: dict[str, Any] = {}
    if ttl is not None:
        kwargs["ttl"] = ttl
    if index is not None:
        kwargs["index"] = index
    await store.aput(tuple(namespace), key, value, **kwargs)  # type: ignore[arg-type]
    return Response(status_code=204)


async def get_item(request: Request) -> Response:
    # SDK sends namespace as dot-joined string: ?namespace=a.b&key=k
    ns_param = request.query_params.get("namespace")
    key = request.query_params.get("key")
    # also support repeated namespace params? starlette query_params.get returns first
    # fallback: if namespace not in query, try to parse body (unlikely)
    if key is None:
        return JSONResponse({"detail": "key is required"}, status_code=422)
    if ns_param is None:
        # try to accept namespace as list via ?namespace=a&namespace=b is not SDK's format
        # but we support empty prefix case?
        return JSONResponse({"detail": "namespace is required"}, status_code=422)
    namespace = ns_param.split(".") if ns_param else []
    # validate that no empty labels came from split
    if ns_param and any(not lbl for lbl in namespace):
        return JSONResponse({"detail": "Namespace labels cannot be empty"}, status_code=422)
    try:
        _validate_namespace(namespace)
    except Exception as exc:
        status = getattr(exc, "status_code", 422)
        detail = getattr(exc, "detail", str(exc))
        return JSONResponse({"detail": detail}, status_code=status)

    policy_value: dict[str, Any] = {"namespace": namespace, "key": key}
    try:
        await _run_policy(request, "get", policy_value)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    store = _store(request)
    item = await store.aget(tuple(namespace), key)
    if item is None:
        return JSONResponse({"detail": "Item not found"}, status_code=404)
    mapped = _item_to_api(item)
    return JSONResponse(mapped)


async def search_items(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "Invalid body"}, status_code=422)
    namespace_prefix = body.get("namespace_prefix", [])
    if not isinstance(namespace_prefix, list):
        return JSONResponse({"detail": "namespace_prefix must be list"}, status_code=422)
    for lbl in namespace_prefix:
        if not isinstance(lbl, str):
            return JSONResponse({"detail": "namespace_prefix labels must be strings"}, status_code=422)
    try:
        _validate_namespace(namespace_prefix)
    except Exception as exc:
        status = getattr(exc, "status_code", 422)
        detail = getattr(exc, "detail", str(exc))
        return JSONResponse({"detail": detail}, status_code=status)

    filt = body.get("filter")
    limit = body.get("limit", 10)
    offset = body.get("offset", 0)
    query = body.get("query")
    if filt is not None and not isinstance(filt, dict):
        return JSONResponse({"detail": "filter must be object"}, status_code=422)
    if not isinstance(limit, int) or limit < 0:
        return JSONResponse({"detail": "limit must be non-negative int"}, status_code=422)
    if not isinstance(offset, int) or offset < 0:
        return JSONResponse({"detail": "offset must be non-negative int"}, status_code=422)
    if query is not None and not isinstance(query, str):
        return JSONResponse({"detail": "query must be string"}, status_code=422)

    policy_value: dict[str, Any] = {
        "namespace": namespace_prefix,
        "namespace_prefix": namespace_prefix,
        "filter": filt,
        "limit": limit,
        "offset": offset,
        "query": query,
    }
    try:
        await _run_policy(request, "search", policy_value)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    store = _store(request)
    items = await store.asearch(tuple(namespace_prefix), filter=filt, limit=limit, offset=offset, query=query)
    mapped = [_item_to_api(it) for it in items]
    return JSONResponse({"items": mapped})


async def delete_item(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "Invalid body"}, status_code=422)
    namespace = body.get("namespace", [])
    key = body.get("key")
    if not isinstance(namespace, list):
        return JSONResponse({"detail": "namespace must be list"}, status_code=422)
    if not isinstance(key, str) or not key:
        return JSONResponse({"detail": "key is required"}, status_code=422)
    try:
        _validate_namespace(namespace)
    except Exception as exc:
        status = getattr(exc, "status_code", 422)
        detail = getattr(exc, "detail", str(exc))
        return JSONResponse({"detail": detail}, status_code=status)

    policy_value: dict[str, Any] = {"namespace": namespace, "key": key}
    try:
        await _run_policy(request, "delete", policy_value)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    store = _store(request)
    await store.adelete(tuple(namespace), key)
    return Response(status_code=204)


routes: list[Route] = [
    Route("/store/items", put_item, methods=["PUT"]),
    Route("/store/items", get_item, methods=["GET"]),
    Route("/store/items/search", search_items, methods=["POST"]),
    Route("/store/items", delete_item, methods=["DELETE"]),
]
