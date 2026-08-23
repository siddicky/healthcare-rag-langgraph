from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal
from uuid import UUID, uuid4

logger = logging.getLogger("MedicalRAG")

from langgraph_sdk import Auth


def _to_jsonable(obj: object) -> object:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}  # type: ignore[arg-type]
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]  # type: ignore[arg-type]
    if hasattr(obj, "model_dump"):
        try:
            return _to_jsonable(obj.model_dump(mode="json"))  # type: ignore[attr-defined]
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return _to_jsonable(obj.dict())  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from server.auth import merge_scope_filter, require_scope_match
from server.storage import Storage

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _now().isoformat()


def _parse_expires_at(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            # fromisoformat handles +00:00; handle trailing Z
            iso = value.replace("Z", "+00:00") if value.endswith("Z") else value
            return datetime.fromisoformat(iso)
        except ValueError:
            return None
    return None


def _is_expired(record: dict[str, object]) -> bool:
    raw = record.get("expires_at")
    if raw is None:
        return False
    expires = _parse_expires_at(raw)
    if expires is None:
        return False
    return _now() >= expires


def _purge_expired(storage: Storage) -> None:
    expired = [tid for tid, rec in storage.threads.items() if _is_expired(rec)]
    for tid in expired:
        storage.threads.pop(tid, None)


def _purge_if_expired(storage: Storage, thread_id: str) -> bool:
    rec = storage.threads.get(thread_id)
    if rec is None:
        return False
    if _is_expired(rec):
        storage.threads.pop(thread_id, None)
        return True
    return False


def _validate_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TTLModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    strategy: Literal["delete"]
    ttl: int = Field(ge=1)


class CreateThreadRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    thread_id: str | None = None
    metadata: dict[str, object] | None = None
    if_exists: str | None = None
    ttl: TTLModel | None = None


class PatchThreadRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    metadata: dict[str, object] | None = None


class SearchThreadRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")
    metadata: dict[str, object] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    offset: int | None = Field(default=None, ge=0)
    select: list[str] | None = None
    sort_by: str | None = None
    sort_order: str | None = None


_ALLOWED_IF_EXISTS = frozenset({"raise", "do_nothing", "reuse", "overwrite"})
_ALLOWED_SORT_BY = frozenset({"thread_id", "created_at", "updated_at"})
_ALLOWED_SORT_ORDER = frozenset({"asc", "desc"})


def _filter_term_matches(actual: object, expected: object) -> bool:
    # Mirror server.auth._filter_term_matches for search merge semantics
    if not isinstance(expected, dict):
        return actual == expected
    if "$eq" in expected:
        return actual == expected["$eq"]
    if "$contains" in expected:
        contained = expected["$contains"]
        if not isinstance(actual, list | tuple):
            return False
        if isinstance(contained, list | tuple) and not isinstance(contained, str | bytes):
            return all(item in actual for item in contained)
        return contained in actual  # type: ignore[operator]
    return False


def _record_matches(record: dict[str, object], filt: dict[str, object]) -> bool:
    for key, expected in filt.items():
        actual = record.get(key)
        if not _filter_term_matches(actual, expected):
            return False
    return True


# ---------------------------------------------------------------------------
# endpoint implementations
# ---------------------------------------------------------------------------


async def create_thread(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    _purge_expired(storage)
    try:
        body_bytes = await request.body()
        if not body_bytes:
            payload_dict: dict[str, object] = {}
        else:
            loaded = json.loads(body_bytes)
            payload_dict = loaded if isinstance(loaded, dict) else {}  # type: ignore[assignment]
            if not isinstance(payload_dict, dict):
                return JSONResponse({"detail": "Invalid body"}, status_code=422)
    except json.JSONDecodeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # Validate via pydantic
    try:
        parsed = CreateThreadRequest.model_validate(payload_dict)
    except ValidationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # Validate if_exists value
    if_exists = parsed.if_exists
    if if_exists is not None and if_exists not in _ALLOWED_IF_EXISTS:
        return JSONResponse({"detail": f"Invalid if_exists: {if_exists}"}, status_code=422)

    # Validate thread_id UUID
    supplied_id = parsed.thread_id
    if supplied_id is not None and not _validate_uuid(supplied_id):
        return JSONResponse({"detail": "thread_id must be a UUID"}, status_code=422)

    # Prepare policy value (mutated by auth for metadata injection)
    incoming_metadata: dict[str, object] = dict(parsed.metadata) if isinstance(parsed.metadata, dict) else {}
    policy_value: dict[str, object] = {"metadata": dict(incoming_metadata)}
    if supplied_id is not None:
        policy_value["thread_id"] = supplied_id

    # Run policy threads.create
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "create", request.user, policy_value  # type: ignore[arg-type]
        )
        # policy may have mutated policy_value["metadata"]
        final_metadata: dict[str, object] = dict(policy_value.get("metadata", {})) if isinstance(policy_value.get("metadata"), dict) else {}  # type: ignore[arg-type]
        if scope_filter is not None:
            # For create, scope filter is not directly applied as 404; but if deny returns dict we treat as filter? deny is 403 already.
            # If scope filter present, future search would merge; no match check needed on create.
            pass
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # Resolve thread_id to use
    thread_id = supplied_id if supplied_id is not None else str(uuid4())

    # Handle if_exists logic if thread already exists (and not expired)
    existing = storage.threads.get(thread_id)
    if existing is not None and _purge_if_expired(storage, thread_id):
        existing = None

    if existing is not None:
        if if_exists == "raise":
            return JSONResponse({"detail": "Thread already exists"}, status_code=409)
        if if_exists in ("do_nothing", "reuse"):
            return JSONResponse(existing)
        if if_exists == "overwrite":
            # fall through to overwrite
            pass
        elif if_exists is None:
            # default behavior: if client supplied id and no if_exists, treat as raise (409) to avoid silent overwrite
            return JSONResponse({"detail": "Thread already exists"}, status_code=409)

    now = _now()
    now_iso = now.isoformat()
    record: dict[str, object] = {}
    if existing is not None and if_exists == "overwrite":
        # Preserve created_at from existing
        record = dict(existing)
        record["updated_at"] = now_iso
        record["metadata"] = dict(final_metadata)
        # spread top-level
        for k, v in final_metadata.items():
            record[k] = v
        # remove stale top-level keys that are no longer in metadata? Keep simple: overwrite add, but remove old keys that were previously spread and now absent
        # Determine old metadata keys
        old_meta = existing.get("metadata")
        if isinstance(old_meta, dict):
            for k in list(old_meta.keys()):
                if k not in final_metadata and k in record and k not in ("thread_id", "created_at", "updated_at", "expires_at", "metadata"):
                    record.pop(k, None)
    else:
        record = {
            "thread_id": thread_id,
            "created_at": existing.get("created_at") if existing is not None and if_exists in ("do_nothing", "reuse") else now_iso,
            "updated_at": now_iso,
            "metadata": dict(final_metadata),
        }
        for k, v in final_metadata.items():
            record[k] = v

    # Handle TTL -> expires_at
    if parsed.ttl is not None:
        expires = now + timedelta(minutes=parsed.ttl.ttl)
        record["expires_at"] = expires.isoformat()
    elif existing is not None and if_exists in ("do_nothing", "reuse"):
        # keep existing expires_at
        pass
    elif existing is not None and if_exists == "overwrite" and "expires_at" not in record:
        # if overwrite without ttl, clear expires_at
        record.pop("expires_at", None)

    # If this is a new creation, ensure thread_id key set
    record["thread_id"] = thread_id
    if "created_at" not in record:
        record["created_at"] = now_iso
    if "updated_at" not in record:
        record["updated_at"] = now_iso

    storage.threads[thread_id] = record
    # Return 200 for reuse/do_nothing existing, otherwise 200 with record (200 is fine for create)
    return JSONResponse(record)


async def get_thread(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    thread_id = request.path_params["thread_id"]
    if not _validate_uuid(thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if _purge_if_expired(storage, thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    record = storage.threads.get(thread_id)
    if record is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # policy
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "read", request.user, {"thread_id": thread_id}  # type: ignore[arg-type]
        )
        if scope_filter is not None:
            require_scope_match(record, scope_filter)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return JSONResponse(record)


async def patch_thread(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    thread_id = request.path_params["thread_id"]
    if not _validate_uuid(thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if _purge_if_expired(storage, thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    record = storage.threads.get(thread_id)
    if record is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "Invalid body"}, status_code=422)
    try:
        parsed = PatchThreadRequest.model_validate(body)
    except ValidationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # policy check before mutation (use read scope semantics or update)
    # Try update first, fallback to read scope check for 404 hiding
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "update", request.user, {"thread_id": thread_id, "metadata": parsed.metadata}  # type: ignore[arg-type]
        )
        if scope_filter is None:
            # No update handler, try read scope as fallback for hiding
            scope_filter = await request.app.state.auth_engine.run_policy(
                "threads", "read", request.user, {"thread_id": thread_id}  # type: ignore[arg-type]
            )
        if scope_filter is not None:
            require_scope_match(record, scope_filter)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    if parsed.metadata is not None:
        incoming = dict(parsed.metadata)
        existing_meta = record.get("metadata")
        merged_meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
        merged_meta.update(incoming)
        record["metadata"] = merged_meta
        for k, v in incoming.items():
            record[k] = v
        # Note: keys removed from metadata are not automatically removed top-level unless explicitly overwritten with None
    record["updated_at"] = _now().isoformat()
    storage.threads[thread_id] = record
    return JSONResponse(record)


async def delete_thread(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    thread_id = request.path_params["thread_id"]
    if not _validate_uuid(thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # Check expired -> treat as 404 (already gone)
    if _purge_if_expired(storage, thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    record = storage.threads.get(thread_id)
    if record is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "delete", request.user, {"thread_id": thread_id}  # type: ignore[arg-type]
        )
        if scope_filter is not None:
            require_scope_match(record, scope_filter)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # Cascade delete
    storage.threads.pop(thread_id, None)
    # checkpoints
    try:
        await storage.saver.adelete_thread(thread_id)
    except Exception as exc:  # noqa: BLE001 - checkpoint cascade is best-effort over langgraph saver boundary; any failure must remain observable
        logger.warning(
            "delete_thread checkpoint cascade failed",
            extra={"thread_id": thread_id, "error": str(exc), "exc_type": type(exc).__name__},
        )
    # runs cascade
    to_delete = [rid for rid, rec in storage.runs.items() if rec.get("thread_id") == thread_id]
    for rid in to_delete:
        storage.runs.pop(rid, None)
    # thread-crons cascade placeholder (todo 6 owns real crons)
    try:
        cron_ids = [cid for cid, rec in storage.crons.items() if rec.get("thread_id") == thread_id]
        for cid in cron_ids:
            storage.crons.pop(cid, None)
    except Exception as exc:  # noqa: BLE001 - cron cascade iterates over in-memory dict; any failure is unexpected and must be observable
        logger.warning(
            "delete_thread cron cascade failed",
            extra={"thread_id": thread_id, "error": str(exc), "exc_type": type(exc).__name__},
        )
    # also clean run_engine runtime if present (best-effort)
    try:
        engine = getattr(request.app.state, "run_engine", None)
        if engine is not None:
            for rid in to_delete:
                engine.runtime.pop(rid, None)
                # also remove from queues if pending
                for q in getattr(engine, "queues", {}).values():
                    try:
                        q.remove(rid)  # type: ignore[attr-defined]
                    except ValueError:
                        pass
    except Exception as exc:  # noqa: BLE001 - runtime cleanup crosses RunEngine boundary; any failure must remain observable
        logger.warning(
            "delete_thread runtime cascade failed",
            extra={"thread_id": thread_id, "error": str(exc), "exc_type": type(exc).__name__},
        )
    return Response(status_code=204)


async def search_threads(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    _purge_expired(storage)
    try:
        body_bytes = await request.body()
        payload: dict[str, object] = json.loads(body_bytes) if body_bytes else {}
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "Invalid body"}, status_code=422)
    except json.JSONDecodeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # Parse with permissive model (allow select/sort etc)
    try:
        parsed = SearchThreadRequest.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # Validate select/sort params if present (must not crash)
    if parsed.select is not None:
        if not isinstance(parsed.select, list) or not parsed.select:
            return JSONResponse({"detail": "Invalid select"}, status_code=422)
    if parsed.sort_by is not None and parsed.sort_by not in _ALLOWED_SORT_BY:
        return JSONResponse({"detail": "Invalid sort_by"}, status_code=422)
    if parsed.sort_order is not None and parsed.sort_order not in _ALLOWED_SORT_ORDER:
        return JSONResponse({"detail": "Invalid sort_order"}, status_code=422)

    requested_filter: dict[str, object] = dict(parsed.metadata) if isinstance(parsed.metadata, dict) else {}

    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "search", request.user, {"metadata": dict(requested_filter)}  # type: ignore[arg-type]
        )
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    merged: dict[str, object] = dict(requested_filter)
    if scope_filter is not None:
        merged = merge_scope_filter(requested_filter, scope_filter)

    # Filter
    items = [rec for rec in storage.threads.values() if _record_matches(rec, merged)]

    # Sort
    sort_by = parsed.sort_by
    sort_order = parsed.sort_order or "desc"
    if sort_by is not None:
        reverse = sort_order == "desc"
        items.sort(key=lambda r: str(r.get(sort_by, "")), reverse=reverse)
    else:
        # default sort by created_at desc for determinism
        items.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)

    limit = parsed.limit if parsed.limit is not None else 10
    offset = parsed.offset if parsed.offset is not None else 0
    sliced = items[offset : offset + limit]

    # select projection (benignly handled)
    if parsed.select is not None:
        projected = []
        for rec in sliced:
            proj = {k: rec[k] for k in parsed.select if k in rec}  # type: ignore[union-attr]
            # Always include thread_id if not in select? Deployed smoke expects select controls visibility, but include for test stability
            if "thread_id" not in proj and "thread_id" in rec:
                # Keep requested projection strict; do not inject
                pass
            projected.append(proj)
        return JSONResponse(projected)

    return JSONResponse(sliced)


def _thread_graph(request: Request, storage: Storage, thread_id: str) -> object | None:
    graphs: Mapping[str, object] = request.app.state.graphs
    if not graphs:
        return None
    for record in reversed(list(storage.runs.values())):
        if record.get("thread_id") != thread_id:
            continue
        graph = graphs.get(str(record.get("assistant_id")))
        if graph is not None:
            return graph
    return next(iter(graphs.values()))


async def get_thread_state(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    thread_id = request.path_params["thread_id"]
    if not _validate_uuid(thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if _purge_if_expired(storage, thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    record = storage.threads.get(thread_id)
    if record is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "read", request.user, {"thread_id": thread_id}  # type: ignore[arg-type]
        )
        if scope_filter is not None:
            require_scope_match(record, scope_filter)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    checkpoint_id = request.query_params.get("checkpoint_id")
    config: dict[str, object] = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id is not None:
        # Validate UUID for checkpoint_id is not strictly required; treat as opaque
        config["configurable"] = {"thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": checkpoint_id}  # type: ignore[dict-item]

    graph = _thread_graph(request, storage, thread_id)
    if graph is None:
        return JSONResponse({"values": {}, "next": [], "interrupts": []})
    try:
        snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
    except Exception:
        # A state-plane fault must surface (500 on a live server), never
        # masquerade as an empty thread state. Fresh threads return an empty
        # snapshot without raising and keep the 200-empty parity shape.
        logger.warning(
            "state lookup failed for thread %s: snapshot fault", thread_id, exc_info=True
        )
        raise
    # Pending interrupts live on the graph snapshot's tasks, never in channel
    # values — the oracle surfaces them as {id, value} entries.
    interrupts = [
        {"id": pending.id, "value": pending.value}
        for task in snapshot.tasks
        for pending in (task.interrupts or [])
    ]
    payload: dict[str, object] = {
        "values": snapshot.values,
        "next": list(snapshot.next),
        "interrupts": interrupts,
    }
    return JSONResponse(_to_jsonable(payload))  # type: ignore[arg-type]


async def copy_thread(request: Request) -> Response:
    storage: Storage = request.app.state.storage
    thread_id = request.path_params["thread_id"]
    if not _validate_uuid(thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if _purge_if_expired(storage, thread_id):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    source = storage.threads.get(thread_id)
    if source is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # scope check on source
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "read", request.user, {"thread_id": thread_id}  # type: ignore[arg-type]
        )
        if scope_filter is not None:
            require_scope_match(source, scope_filter)
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # Also check create permission for target (re-use create policy with source metadata)
    source_meta = source.get("metadata")
    meta_copy = dict(source_meta) if isinstance(source_meta, dict) else {}
    policy_value = {"metadata": dict(meta_copy)}
    try:
        await request.app.state.auth_engine.run_policy(
            "threads", "create", request.user, policy_value  # type: ignore[arg-type]
        )
        final_meta = dict(policy_value.get("metadata", {})) if isinstance(policy_value.get("metadata"), dict) else meta_copy  # type: ignore[arg-type]
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    new_id = str(uuid4())
    now_iso = _now().isoformat()
    new_record: dict[str, object] = {
        "thread_id": new_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "metadata": dict(final_meta),
    }
    for k, v in final_meta.items():
        new_record[k] = v
    # Do not copy expires_at (new thread should not inherit TTL)
    storage.threads[new_id] = new_record
    # Copy checkpoint history
    try:
        # Prefer adelete_thread semantic: copy_thread on saver if available
        copier = getattr(storage.saver, "acopy_thread", None) or getattr(storage.saver, "copy_thread", None)
        if callable(copier):
            res = copier(thread_id, new_id)
            if inspect.isawaitable(res):
                await res
    except Exception:
        # The thread record copy above already succeeded; a checkpoint-copy
        # failure degrades history, it does not invalidate the copy. But it
        # must be visible, not silent (matches the delete-cascade warnings).
        logger.warning(
            "checkpoint history copy %s -> %s failed", thread_id, new_id, exc_info=True
        )
    return JSONResponse(new_record)


routes: list[Route] = [
    Route("/threads", create_thread, methods=["POST"]),
    Route("/threads/search", search_threads, methods=["POST"]),
    Route("/threads/{thread_id}", get_thread, methods=["GET"]),
    Route("/threads/{thread_id}", patch_thread, methods=["PATCH"]),
    Route("/threads/{thread_id}", delete_thread, methods=["DELETE"]),
    Route("/threads/{thread_id}/state", get_thread_state, methods=["GET"]),
    Route("/threads/{thread_id}/copy", copy_thread, methods=["POST"]),
]
