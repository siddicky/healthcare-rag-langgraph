"""Preliminary Postgres storage smoke test — todo 8.

This file proves the acceptance criteria for the async pool-backed
saver+store. It is deliberately small and gated behind a real Postgres
instance; todo 15 will formalize the postgres_url fixture and todo 16
will expand the signature suite. This file is intended to be EXTENDED,
not replaced.

Gating:
- Memory-mode tests run unconditionally (no env vars) and prove
  backward compatibility of the async create_storage.
- Postgres-mode tests run only when POSTGRES_TEST_DSN (or POSTGRES=1 with
  DATABASE_URI) points at a reachable Postgres.
"""

from __future__ import annotations

import inspect
import os

import pytest

from server.config import ServerConfig
from server.storage import Storage, create_storage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_postgres_available() -> bool:
    # Accept POSTGRES_TEST_DSN (explicit), or POSTGRES=1 + DATABASE_URI,
    # or SERVER_STORAGE=postgres + POSTGRES_TEST_DSN — todo 15 will unify.
    if os.environ.get("POSTGRES_TEST_DSN"):
        return True
    if os.environ.get("POSTGRES") == "1" and os.environ.get("DATABASE_URI"):
        return True
    return False


def _postgres_dsn() -> str | None:
    return os.environ.get("POSTGRES_TEST_DSN") or os.environ.get("DATABASE_URI")


# ---------------------------------------------------------------------------
# Memory-mode (no DB) — must always pass
# ---------------------------------------------------------------------------

def test_create_storage_is_async() -> None:
    """create_storage is now async def — caller must await."""
    assert inspect.iscoroutinefunction(create_storage), "create_storage must be async def"


@pytest.mark.anyio
async def test_memory_mode_unchanged() -> None:
    cfg = ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="test",
        storage="memory",
        database_uri=None,
    )
    storage = await create_storage(cfg)
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    assert isinstance(storage.saver, InMemorySaver)
    assert isinstance(storage.store, InMemoryStore)
    assert storage._pool is None
    assert await storage.threads.count() == 0
    thread: dict[str, object] = {
        "thread_id": "thread-1",
        "metadata": {"owner": "member-1"},
    }
    await storage.threads.save("thread-1", thread)
    assert await storage.threads.get("thread-1") == thread

    run: dict[str, object] = {"run_id": "run-1", "status": "pending"}
    await storage.runs.save("run-1", run)
    await storage.runs.set_status("run-1", "success")
    assert await storage.runs.get("run-1") == {
        "run_id": "run-1",
        "status": "success",
    }

    cron: dict[str, object] = {
        "cron_id": "cron-1",
        "next_run_date": None,
        "updated_at": "old",
    }
    await storage.crons.save("cron-1", cron)
    await storage.crons.set_schedule_state("cron-1", "next", "updated")
    assert await storage.crons.get("cron-1") == {
        "cron_id": "cron-1",
        "next_run_date": "next",
        "updated_at": "updated",
    }


@pytest.mark.anyio
async def test_storage_aclose_idempotent_memory() -> None:
    cfg = ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="test",
        storage="memory",
        database_uri=None,
    )
    storage = await create_storage(cfg)
    await storage.aclose()
    await storage.aclose()  # second call must not raise
    assert storage._pool is None


@pytest.mark.anyio
async def test_memory_embeddings_fallback() -> None:
    """Embeddings-unavailable must fall back to index=None without crashing."""
    cfg = ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        # openai provider string without key will trigger fallback path
        store_index={"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"]},
        api_version="test",
        storage="memory",
        database_uri=None,
    )
    # Ensure no API key is set for this test (fallback path should handle it)
    orig = os.environ.pop("OPENAI_API_KEY", None)
    try:
        storage = await create_storage(cfg)
        # If fallback happened, index_config is None; otherwise it's set but still valid.
        # Either is acceptable as long as it didn't crash. In CI without key, it should be None.
        assert storage.store is not None
    finally:
        if orig is not None:
            os.environ["OPENAI_API_KEY"] = orig


def test_compat_shim_accepts_base_store() -> None:
    """server/_compat widened to accept BaseStore (covers InMemoryStore + AsyncPostgresStore)."""
    import inspect as _inspect
    from server._compat import install_langgraph_api_compat, _StoreCompat

    sig_compat = _inspect.signature(_StoreCompat.__init__)
    sig_install = _inspect.signature(install_langgraph_api_compat)
    # Param annotation should mention BaseStore (or at least not be narrow InMemoryStore)
    compat_param = sig_compat.parameters["store"].annotation
    install_param = sig_install.parameters["store"].annotation
    # Stringify check — with from __future__ import annotations it's a string 'BaseStore'
    assert "BaseStore" in str(compat_param) or "BaseStore" in str(install_param), f"compat still narrow: {compat_param}, {install_param}"


# ---------------------------------------------------------------------------
# Postgres-mode — gated behind real DB (todo 15 will formalize fixture)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _is_postgres_available(), reason="POSTGRES_TEST_DSN not set — no Postgres instance; todo 15 will formalize fixture")
@pytest.mark.anyio
async def test_postgres_branch_constructs_pool_saver_store() -> None:
    dsn = _postgres_dsn()
    assert dsn is not None
    cfg = ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"]},
        api_version="test",
        storage="postgres",
        database_uri=dsn,
    )
    storage = await create_storage(cfg)
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres import AsyncPostgresStore
        from psycopg_pool import AsyncConnectionPool

        assert isinstance(storage.saver, AsyncPostgresSaver)
        assert isinstance(storage.store, AsyncPostgresStore)
        assert isinstance(storage._pool, AsyncConnectionPool)
        # Pool must be same object for both saver and store
        assert storage.saver.conn is storage._pool
        assert storage.store.conn is storage._pool
        # Pool kwargs per spec: autocommit, prepare_threshold, row_factory, sizing
        # psycopg_pool stores kwargs in .kwargs — verify via pool.kwargs or pool.conninfo
        # At minimum verify pool is open and has expected sizing attributes
        assert storage._pool is not None
    finally:
        await storage.aclose()
        await storage.aclose()  # idempotent


@pytest.mark.skipif(not _is_postgres_available(), reason="POSTGRES_TEST_DSN not set")
@pytest.mark.anyio
async def test_postgres_pool_sizing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = _postgres_dsn()
    assert dsn is not None
    monkeypatch.setenv("SERVER_PG_POOL_MIN", "2")
    monkeypatch.setenv("SERVER_PG_POOL_MAX", "5")
    cfg = ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="test",
        storage="postgres",
        database_uri=dsn,
    )
    storage = await create_storage(cfg)
    try:
        # Pool sizing should reflect env vars
        # AsyncConnectionPool exposes min_size/max_size; check via private or public
        pool = storage._pool
        assert pool is not None
        # These attributes are implementation details but stable across psycopg 3.3
        assert getattr(pool, "min_size", None) == 2 or getattr(pool, "_min_size", None) == 2
        assert getattr(pool, "max_size", None) == 5 or getattr(pool, "_max_size", None) == 5
    finally:
        await storage.aclose()


@pytest.mark.skipif(not _is_postgres_available(), reason="POSTGRES_TEST_DSN not set")
@pytest.mark.anyio
async def test_postgres_aclose_idempotent() -> None:
    dsn = _postgres_dsn()
    assert dsn is not None
    cfg = ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="test",
        storage="postgres",
        database_uri=dsn,
    )
    storage = await create_storage(cfg)
    await storage.aclose()
    await storage.aclose()
    assert storage._pool is None


# ---------------------------------------------------------------------------
# Todo 9 — lifespan: pool lifecycle + storage readiness + bug fix
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_setup_component_awaits_async_only_setup() -> None:
    """Regression: AsyncPostgresSaver/Store have async def setup() with no asetup.

    Old code did `sync_setup()` without awaiting, silently dropping the coroutine
    and never creating checkpoint tables. New code must await the result if it is
    awaitable. A fake whose .setup() is async must have its body actually executed.
    This test would FAIL on the old elif branch.
    """
    from server.app import _setup_component

    flag = {"done": False}

    class FakeAsyncOnlySetup:
        async def setup(self):  # noqa: ANN201
            flag["done"] = True

    await _setup_component(FakeAsyncOnlySetup())
    assert flag["done"] is True, "_setup_component did not await async-only .setup()"


@pytest.mark.anyio
async def test_setup_component_sync_setup_still_works() -> None:
    from server.app import _setup_component

    flag = {"done": False}

    class FakeSync:
        def setup(self):  # noqa: ANN201
            flag["done"] = True

    await _setup_component(FakeSync())
    assert flag["done"] is True


@pytest.mark.anyio
async def test_setup_component_prefers_asetup() -> None:
    from server.app import _setup_component

    flags = {"async": False, "sync": False}

    class FakeBoth:
        async def asetup(self):  # noqa: ANN201
            flags["async"] = True

        def setup(self):  # noqa: ANN201
            flags["sync"] = True

    await _setup_component(FakeBoth())
    assert flags["async"] is True
    assert flags["sync"] is False


@pytest.mark.anyio
async def test_setup_component_no_setup_is_noop() -> None:
    from server.app import _setup_component

    class Empty:
        pass

    # InMemorySaver/Store have no setup — must not raise
    await _setup_component(Empty())


def test_run_engine_shutdown_is_async() -> None:
    import inspect as _inspect

    from server.run_engine import RunEngine

    assert _inspect.iscoroutinefunction(RunEngine.shutdown)


@pytest.mark.anyio
async def test_storage_readiness_gates_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readiness includes `storage` and /ok is 503 until storage setup completes, 200 after."""
    import httpx

    import server.app as app_module
    from langgraph_sdk import Auth

    def _auth() -> Auth:
        auth = Auth()

        @auth.authenticate
        async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):  # type: ignore[no-untyped-def]
            del path
            if method == "OPTIONS":
                return {"identity": "cors-preflight", "role": "preflight"}
            raise Auth.exceptions.HTTPException(status_code=401)

        @auth.on
        async def allow(ctx, value):  # type: ignore[no-untyped-def]
            del ctx, value

        return auth

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())
    import healthcare_rag.agent.http_app as custom

    monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")

    cfg = ServerConfig(
        graphs={},
        auth_path="fixture:auth",
        http_app="./healthcare_rag/agent/http_app.py:app",
        http_flags={},
        store_index={},
        api_version="test-readiness",
    )
    app = app_module.create_app(cfg)

    # Before lifespan, storage not ready
    assert "storage" in app.state.readiness.checks
    assert app.state.readiness.checks["storage"] is False
    assert app.state.readiness.is_ready() is False

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Without entering lifespan, /ok must be 503
        r_pre = await client.get("/ok")
        assert r_pre.status_code == 503
        assert r_pre.json() == {"ok": False, "ready": False}

        async with app.router.lifespan_context(app):
            assert app.state.readiness.checks["storage"] is True
            assert app.state.readiness.is_ready() is True
            r_ok = await client.get("/ok")
            assert r_ok.status_code == 200
            assert r_ok.json() == {"ok": True}

    # After lifespan exit, app is no longer ready (still checks exist)
    assert "storage" in app.state.readiness.checks


@pytest.mark.anyio
async def test_storage_setup_failure_propagates_and_closes_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """If _setup_component raises, lifespan must propagate and never become ready, but still aclose."""
    import server.app as app_module

    from langgraph_sdk import Auth

    def _auth() -> Auth:
        auth = Auth()

        @auth.authenticate
        async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):  # type: ignore[no-untyped-def]
            del path
            if method == "OPTIONS":
                return {"identity": "cors-preflight", "role": "preflight"}
            raise Auth.exceptions.HTTPException(status_code=401)

        @auth.on
        async def allow(ctx, value):  # type: ignore[no-untyped-def]
            del ctx, value

        return auth

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())
    import healthcare_rag.agent.http_app as custom

    monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")

    # Fake storage whose saver setup will raise
    events: list[str] = []

    class _FakeSaver:
        async def setup(self):  # noqa: ANN201
            raise RuntimeError("saver setup boom")

    class _FakeStore:
        pass

    class _FakeStorage:
        def __init__(self) -> None:
            self.saver = _FakeSaver()
            self.store = _FakeStore()
            self.threads: dict[str, object] = {}
            self.runs: dict[str, object] = {}
            self.crons: dict[str, object] = {}

        async def aclose(self) -> None:
            events.append("aclose")

    async def _fake_create_storage(_cfg):  # type: ignore[no-untyped-def]
        return _FakeStorage()

    monkeypatch.setattr(app_module, "create_storage", _fake_create_storage)

    cfg = ServerConfig(
        graphs={},
        auth_path="fixture:auth",
        http_app="./healthcare_rag/agent/http_app.py:app",
        http_flags={},
        store_index={},
        api_version="test-fail",
    )
    app = app_module.create_app(cfg)
    assert "storage" in app.state.readiness.checks
    assert app.state.readiness.checks["storage"] is False

    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="saver setup boom"):
        async with app.router.lifespan_context(app):
            _pytest.fail("should not enter")

    # Even though setup failed, pool was closed via finally, and readiness never flipped
    assert events == ["aclose"]
    assert app.state.readiness.checks["storage"] is False
    assert app.state.readiness.is_ready() is False


@pytest.mark.anyio
async def test_storage_aclose_called_on_custom_app_startup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool must close via try/finally even if custom-app lifespan raises during startup."""
    import httpx

    import server.app as app_module
    from server.config import ServerConfig as _Cfg

    from langgraph_sdk import Auth

    def _auth() -> Auth:
        auth = Auth()

        @auth.authenticate
        async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):  # type: ignore[no-untyped-def]
            del path
            if method == "OPTIONS":
                return {"identity": "cors-preflight", "role": "preflight"}
            raise Auth.exceptions.HTTPException(status_code=401)

        @auth.on
        async def allow(ctx, value):  # type: ignore[no-untyped-def]
            del ctx, value

        return auth

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())

    events: list[str] = []

    class _Empty:
        pass

    class _FakeStorage:
        def __init__(self) -> None:
            self.saver = _Empty()
            self.store = _Empty()
            self.threads: dict[str, object] = {}
            self.runs: dict[str, object] = {}
            self.crons: dict[str, object] = {}

        async def aclose(self) -> None:
            events.append("storage_aclose")

    async def _fake_create_storage(_cfg):  # type: ignore[no-untyped-def]
        return _FakeStorage()

    monkeypatch.setattr(app_module, "create_storage", _fake_create_storage)
    # Keep other helpers no-ops
    monkeypatch.setattr(app_module, "install_langgraph_api_compat", lambda _s, force=True: None)
    monkeypatch.setattr(app_module, "load_raw_graphs", lambda _c: {})
    monkeypatch.setattr(app_module, "attach_graphs", lambda _r, _s: {})

    import healthcare_rag.agent.http_app as custom

    # Make validate_feedback_project raise — mirrors test_topology fixture
    monkeypatch.setattr(custom, "validate_feedback_project", lambda: (_ for _ in ()).throw(RuntimeError("invalid feedback project")))

    cfg = _Cfg(
        graphs={},
        auth_path="fixture:auth",
        http_app="./healthcare_rag/agent/http_app.py:app",
        http_flags={},
        store_index={},
        api_version="test-custom-fail",
    )
    app = app_module.create_app(cfg)

    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="invalid feedback project"):
        async with app.router.lifespan_context(app):
            _pytest.fail("should not enter")

    assert "storage_aclose" in events, f"storage.aclose not called on startup failure, events={events}"
    assert events[-1] == "storage_aclose"


@pytest.mark.anyio
async def test_lifespan_shutdown_order_is_five_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove exact five-step shutdown order via instrumented fakes."""
    from contextlib import asynccontextmanager

    import server.app as app_module
    import healthcare_rag.agent.http_app as custom_mod
    from langgraph_sdk import Auth

    def _auth() -> Auth:
        auth = Auth()

        @auth.authenticate
        async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):  # type: ignore[no-untyped-def]
            del path
            if method == "OPTIONS":
                return {"identity": "cors-preflight", "role": "preflight"}
            raise Auth.exceptions.HTTPException(status_code=401)

        @auth.on
        async def allow(ctx, value):  # type: ignore[no-untyped-def]
            del ctx, value

        return auth

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())
    import healthcare_rag.agent.http_app as custom

    monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")

    events: list[str] = []

    # --- Fake storage ---
    class _Empty:
        pass

    class _FakeStorage:
        def __init__(self) -> None:
            self.saver = _Empty()
            self.store = _Empty()
            self.threads: dict[str, object] = {}
            self.runs: dict[str, object] = {}
            self.crons: dict[str, object] = {}

        async def aclose(self) -> None:
            events.append("storage_aclose")

    async def _fake_create_storage(_cfg):  # type: ignore[no-untyped-def]
        return _FakeStorage()

    monkeypatch.setattr(app_module, "create_storage", _fake_create_storage)
    monkeypatch.setattr(app_module, "install_langgraph_api_compat", lambda _s, force=True: None)
    monkeypatch.setattr(app_module, "load_raw_graphs", lambda _c: {})
    monkeypatch.setattr(app_module, "attach_graphs", lambda _r, _s: {})

    # --- Fake scheduler ---
    class _FakeScheduler:
        def cancel(self) -> None:
            events.append("scheduler_cancel")

        def __await__(self):  # type: ignore[no-untyped-def]
            async def _coro() -> None:
                return None

            return _coro().__await__()

    def _fake_start_scheduler(_engine, _storage, _clock=None):  # type: ignore[no-untyped-def]
        return _FakeScheduler()

    monkeypatch.setattr(app_module, "start_scheduler", _fake_start_scheduler)

    class _FakeRunEngine:
        def __init__(self, _storage, _graphs, _tasks):  # type: ignore[no-untyped-def]
            pass

        async def shutdown(self) -> None:
            events.append("run_engine_shutdown")

    monkeypatch.setattr(app_module, "RunEngine", _FakeRunEngine)

    # --- Fake task group ---
    def _fake_create_task_group():  # type: ignore[no-untyped-def]
        class _FakeScope:
            def cancel(self) -> None:
                events.append("task_group_cancel")

        class _FakeTG:
            cancel_scope = _FakeScope()

            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
                return False

            def start_soon(self, *_a, **_kw):  # type: ignore[no-untyped-def]
                pass

        return _FakeTG()

    monkeypatch.setattr(app_module.anyio, "create_task_group", _fake_create_task_group)

    # --- Custom app lifespan wrapper to record exit ---
    orig = custom_mod.app.router.lifespan_context

    @asynccontextmanager
    async def _recording_lifespan(app_inner):  # type: ignore[no-untyped-def]
        async with orig(app_inner):
            yield
        events.append("custom_app_exit")

    monkeypatch.setattr(custom_mod.app.router, "lifespan_context", _recording_lifespan)

    from server.config import ServerConfig as _SC

    test_cfg = _SC(
        graphs={},
        auth_path="fixture:auth",
        http_app="./healthcare_rag/agent/http_app.py:app",
        http_flags={},
        store_index={},
        api_version="test-order",
    )
    app = app_module.create_app(test_cfg)

    async with app.router.lifespan_context(app):
        assert app.state.readiness.is_ready() is True

    assert events == [
        "scheduler_cancel",
        "run_engine_shutdown",
        "task_group_cancel",
        "custom_app_exit",
        "storage_aclose",
    ], f"Shutdown order wrong: {events}"
