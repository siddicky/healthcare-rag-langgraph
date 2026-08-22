from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from server.config import ServerConfig, load_config
from server.graphs import attach_graphs, load_raw_graphs
from server.manifest import UNIMPLEMENTED_PATHS, UNIMPLEMENTED_PREFIXES
from server.routes.system import routes as system_routes
from server.storage import Storage, create_storage


class ReadinessState:
    """Extensible readiness registry.

    Usage:
        readiness = ReadinessState()
        readiness.register("config")
        readiness.register("graphs")
        # later todos:
        # readiness.register("auth")
        # readiness.register("scheduler")
        # readiness.register("custom_app")
        readiness.set_ready("config")
        readiness.is_ready()  # True only when ALL registered are ready

    Design ensures todos 2/6/7 can call register/set_ready without
    redesign. Call order beyond "config/graphs first" is not assumed.
    """

    def __init__(self) -> None:
        self._checks: dict[str, bool] = {}

    def register(self, name: str) -> None:
        if name not in self._checks:
            self._checks[name] = False

    def set_ready(self, name: str) -> None:
        self._checks[name] = True

    def set_not_ready(self, name: str) -> None:
        self._checks[name] = False

    def is_ready(self) -> bool:
        if not self._checks:
            return False
        return all(self._checks.values())

    @property
    def checks(self) -> dict[str, bool]:
        return dict(self._checks)


def _is_unimplemented(path: str) -> bool:
    if path in UNIMPLEMENTED_PATHS:
        return True
    for prefix in UNIMPLEMENTED_PREFIXES:
        if path.startswith(prefix):
            # exact prefixes already handled; avoid false positive on /metrics etc? already above
            # For prefixes, treat any path starting with prefix as unimplemented
            return True
    return False


async def _unimplemented_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"detail": "Not implemented"}, status_code=501)


async def _not_found_endpoint(request: Request) -> PlainTextResponse:
    return PlainTextResponse("Not Found", status_code=404)


def create_app(config: ServerConfig | None = None) -> Starlette:
    cfg = config or load_config()
    readiness = ReadinessState()
    # Register subsystems that exist now
    readiness.register("config")
    readiness.register("graphs")
    # Placeholders for future todos — registered but not required for this todo's test
    # They start as not-ready; is_ready() will be False until set. For THIS todo we
    # keep them out of the required set so create_app() + lifespan can become ready.
    # Downstream todos will call app.state.readiness.register("auth") etc.
    # To keep extensible, we do NOT register auth/scheduler/custom_app here.
    # Instead lifespan will set_ready for config/graphs and leave readiness true.

    storage: Storage | None = None
    graphs: dict[str, object] | None = None

    @asynccontextmanager
    async def lifespan(app: Starlette):  # type: ignore[no-untyped-def]
        nonlocal storage, graphs
        app.state.config = cfg  # type: ignore[attr-defined]
        app.state.readiness = readiness  # type: ignore[attr-defined]
        # Validate probe: if config is invalid, fail startup (tested via invalid langgraph.json)
        # Graphs setup
        try:
            storage = create_storage(cfg)
            app.state.storage = storage  # type: ignore[attr-defined]
            # saver/store setup if they have async setup
            saver = storage.saver
            if hasattr(saver, "asetup"):
                await saver.asetup()  # type: ignore[union-attr]
            elif hasattr(saver, "setup"):
                saver.setup()  # type: ignore[union-attr]
            store = storage.store
            if hasattr(store, "asetup"):
                await store.asetup()  # type: ignore[union-attr]
            elif hasattr(store, "setup"):
                store.setup()  # type: ignore[union-attr]

            raw = load_raw_graphs(cfg)
            graphs = attach_graphs(raw, storage)
            app.state.graphs = graphs  # type: ignore[attr-defined]
            app.state.raw_graphs = raw  # type: ignore[attr-defined]

            readiness.set_ready("config")
            readiness.set_ready("graphs")
            # If downstream todos have registered extra checks, they remain False until those subsystems set_ready
            yield
        finally:
            # teardown
            pass

    # Build routes
    routes: list[Route] = []
    routes.extend(system_routes)

    # catch-all for 501 manifest and 404
    async def catch_all(request: Request):  # type: ignore[no-untyped-def]
        path = request.url.path
        if _is_unimplemented(path):
            return JSONResponse({"detail": "Not implemented"}, status_code=501)
        # MCP/A2A not in manifest → 404 (or 405 handled by method not allowed)
        return PlainTextResponse("Not Found", status_code=404)

    # We need a route that matches everything else
    # Starlette matches in order; add a catch-all at the end
    # Use a path param
    from starlette.routing import Route as SRoute

    async def manifest_catch(request: Request) -> JSONResponse | PlainTextResponse:
        path = request.url.path
        if _is_unimplemented(path):
            return JSONResponse({"detail": "Not implemented"}, status_code=501)
        return PlainTextResponse("Not Found", status_code=404)

    routes.append(SRoute("/{path:path}", manifest_catch, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]))

    app = Starlette(routes=routes, lifespan=lifespan)
    # Attach for pre-lifespan access (is_ready checks)
    app.state.readiness = readiness  # type: ignore[attr-defined]
    app.state.config = cfg  # type: ignore[attr-defined]
    return app
