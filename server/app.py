from __future__ import annotations

import importlib
import inspect
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

from healthcare_rag.agent.perimeter_middleware import MemberPerimeterMiddleware
from server._compat import install_langgraph_api_compat
from server.assistants import routes as assistant_routes
from server.auth import (
    AuthMiddleware,
    AuthPolicyEngine,
    ScopeUser,
    load_auth_instance,
)
from server.config import ServerConfig, load_config
from server.crons import reconcile_crons
from server.crons import routes as cron_routes
from server.crons import start_scheduler
from server.graphs import attach_graphs, load_raw_graphs
from server.manifest import UNIMPLEMENTED_PATHS, UNIMPLEMENTED_PREFIXES
from server.routes.system import routes as system_routes
from server.run_engine import RunEngine
from server.run_engine import reconcile_interrupted_runs
from server.runs import routes as run_routes
from server.storage import create_storage
from server.store_routes import routes as store_item_routes
from server.threads import routes as thread_routes


class ReadinessState:
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
        return bool(self._checks) and all(self._checks.values())

    @property
    def checks(self) -> dict[str, bool]:
        return dict(self._checks)


class NativeCORSMiddleware:
    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        self.app: ASGIApp = app
        self.native: ASGIApp = CORSMiddleware(
            app,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.native(scope, receive, send)


class PublicInfoPrincipalMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("path") == "/info":
            scope["user"] = ScopeUser(
                {"identity": "langgraph-studio-user", "kind": "StudioUser"}
            )
        await self.app(scope, receive, send)


@dataclass(frozen=True, slots=True)
class CustomAppConfigurationError(RuntimeError):
    configured_path: str

    def __str__(self) -> str:
        return f"Configured custom HTTP app is not a Starlette application: {self.configured_path}"


def _is_unimplemented(path: str) -> bool:
    return path in UNIMPLEMENTED_PATHS or any(
        path.startswith(prefix) for prefix in UNIMPLEMENTED_PREFIXES
    )


def _custom_proxy_routes(custom_app: Starlette) -> list[Route]:
    return [
        Route(route.path, custom_app, methods=route.methods, name=route.name)
        for route in custom_app.routes
        if isinstance(route, Route)
    ]


async def _setup_component(component: Any) -> None:
    async_setup = getattr(component, "asetup", None)
    sync_setup = getattr(component, "setup", None)
    if callable(async_setup):
        result = async_setup()
        if inspect.isawaitable(result):
            await result
    elif callable(sync_setup):
        result = sync_setup()
        if inspect.isawaitable(result):
            await result


def create_app(config: ServerConfig | None = None) -> Starlette:
    cfg = config or load_config()
    auth_instance = load_auth_instance(cfg.auth_path)
    auth_engine = AuthPolicyEngine(auth_instance)
    if cfg.http_app is None:
        custom_app = Starlette()
    else:
        module_path, attribute = cfg.http_app.rsplit(":", 1)
        module_name = (
            module_path.removeprefix("./").removesuffix(".py").replace("/", ".")
        )
        loaded_custom_app = getattr(importlib.import_module(module_name), attribute)
        if not isinstance(loaded_custom_app, Starlette):
            raise CustomAppConfigurationError(cfg.http_app)
        custom_app = loaded_custom_app

    readiness = ReadinessState()
    for subsystem in ("config", "graphs", "auth", "scheduler", "custom_app", "storage"):
        readiness.register(subsystem)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        storage = await create_storage(cfg)
        app.state.storage = storage
        try:
            await _setup_component(storage.saver)
            await _setup_component(storage.store)
            readiness.set_ready("storage")
            await reconcile_interrupted_runs(storage)
            await reconcile_crons(storage)
            _ = install_langgraph_api_compat(storage.store, force=True)
            raw_graphs = load_raw_graphs(cfg)
            attached_graphs = attach_graphs(raw_graphs, storage)
            app.state.graphs = attached_graphs
            app.state.raw_graphs = raw_graphs
            readiness.set_ready("config")
            readiness.set_ready("graphs")
            readiness.set_ready("auth")

            async with custom_app.router.lifespan_context(custom_app):
                readiness.set_ready("custom_app")
                async with anyio.create_task_group() as tasks:
                    run_engine = RunEngine(storage, attached_graphs, tasks)
                    app.state.run_engine = run_engine
                    scheduler = start_scheduler(run_engine, storage)
                    app.state.scheduler_task = scheduler
                    readiness.set_ready("scheduler")
                    try:
                        yield
                    finally:
                        readiness.set_not_ready("scheduler")
                        scheduler.cancel()
                        with suppress(anyio.get_cancelled_exc_class()):
                            await scheduler
                        await run_engine.shutdown()
                        tasks.cancel_scope.cancel()
                readiness.set_not_ready("custom_app")
        finally:
            await storage.aclose()

    native_routes: list[Route] = []
    native_routes.extend(system_routes)
    native_routes.extend(run_routes)
    native_routes.extend(thread_routes)
    native_routes.extend(assistant_routes)
    native_routes.extend(store_item_routes)
    native_routes.extend(cron_routes)

    async def manifest_catch(request: Request) -> JSONResponse | PlainTextResponse:
        if _is_unimplemented(request.url.path):
            return JSONResponse({"detail": "Not implemented"}, status_code=501)
        return PlainTextResponse("Not Found", status_code=404)

    native_routes.append(
        Route(
            "/{path:path}",
            manifest_catch,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
        )
    )
    routes = [*_custom_proxy_routes(custom_app), *native_routes]
    origins = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if cfg.http_app is not None:
        coach_origins = [
            origin.strip()
            for origin in os.getenv("COACH_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        ]
        misaligned = [o for o in coach_origins if o not in origins]
        if misaligned:
            logger.warning(
                "COACH_ALLOWED_ORIGINS contains origins not in CORS_ALLOW_ORIGINS: %s",
                ", ".join(misaligned),
            )
    middleware: list[Middleware] = [
        Middleware(NativeCORSMiddleware, allow_origins=origins),
        AuthMiddleware.as_starlette(auth_instance, cfg.local_dev),
    ]
    if cfg.http_app is not None:
        middleware.extend(
            [
                Middleware(PublicInfoPrincipalMiddleware),
                Middleware(MemberPerimeterMiddleware),
            ]
        )
    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=middleware,
    )
    app.state.readiness = readiness
    app.state.config = cfg
    app.state.auth_engine = auth_engine
    return app
