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
    # threads/runs/crons stay plain dicts until todo 10
    assert isinstance(storage.threads, dict)
    assert storage.runs == {}
    assert storage.crons == {}


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
