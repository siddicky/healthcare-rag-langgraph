from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from server.config import ServerConfig

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres import AsyncPostgresStore
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("MedicalRAG")


@dataclass(slots=True)
class Storage:
    saver: InMemorySaver | AsyncPostgresSaver  # type: ignore[no-redef]
    store: InMemoryStore | AsyncPostgresStore  # type: ignore[no-redef]
    threads: dict[str, dict[str, object]] = field(default_factory=dict)
    runs: dict[str, dict[str, object]] = field(default_factory=dict)
    crons: dict[str, dict[str, object]] = field(default_factory=dict)
    _pool: AsyncConnectionPool | None = field(default=None, repr=False, compare=False)  # type: ignore[no-redef]

    async def aclose(self) -> None:
        pool = self._pool
        if pool is None:
            return
        self._pool = None
        try:
            await pool.close()  # type: ignore[union-attr]
        except Exception:
            logger.warning("storage pool close failed", exc_info=True)


def _is_embeddings_unavailable(exc: BaseException) -> bool:
    return (
        isinstance(exc, (ValueError, ImportError, RuntimeError))
        or exc.__class__.__name__
        in (
            "OpenAIError",
            "AuthenticationError",
            "PermissionDeniedError",
        )
        or "api_key" in str(exc).lower()
    )


def _is_vector_extension_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    name = exc.__class__.__name__.lower()
    # Covers: "extension \"vector\" does not exist", "type \"vector\" does not exist",
    # "could not open extension control file", pgvector missing, etc.
    vector_kw = "vector" in msg or "vector" in name or "pgvector" in msg
    extension_kw = (
        "extension" in msg
        or "extname" in msg
        or "pg_extension" in msg
        or "vector_migrations" in msg
    )
    # Require vector keyword plus extension signal, or explicit pgvector messages.
    if vector_kw and extension_kw:
        return True
    if "vector" in msg and ("does not exist" in msg or "not available" in msg or "not found" in msg):
        return True
    if "pgvector" in msg:
        return True
    return False


async def create_storage(config: ServerConfig) -> Storage:
    # Memory branch: UNCHANGED behavior
    if config.storage != "postgres":
        saver = InMemorySaver()
        index_cfg = config.store_index or {"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"]}
        if "embed" not in index_cfg:
            index_cfg = {"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"], **index_cfg}
        try:
            store = InMemoryStore(index=index_cfg)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        except Exception as exc:
            if not _is_embeddings_unavailable(exc):
                raise
            logger.warning(
                "store index disabled: embeddings unavailable (%s: %s) — falling back to index=None; semantic search will be lexical only",
                exc.__class__.__name__,
                exc,
            )
            store = InMemoryStore(index=None)
        return Storage(saver=saver, store=store)

    # Postgres branch: one owned pool for both saver and store
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres import AsyncPostgresStore
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # Env-tuned sizing
    try:
        min_size = int(os.environ.get("SERVER_PG_POOL_MIN", "1"))
    except ValueError:
        min_size = 1
    try:
        max_size = int(os.environ.get("SERVER_PG_POOL_MAX", "10"))
    except ValueError:
        max_size = 10

    # config.database_uri is guaranteed non-None when storage==postgres (validated in load_config)
    assert config.database_uri is not None, "DATABASE_URI required for postgres storage"

    pool: AsyncConnectionPool = AsyncConnectionPool(  # type: ignore[no-untyped-call]
        conninfo=config.database_uri,
        open=False,
        min_size=min_size,
        max_size=max_size,
        max_idle=300,
        max_lifetime=3600,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await pool.open()

    saver = AsyncPostgresSaver(conn=pool)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    index_cfg = config.store_index or {"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"]}
    if "embed" not in index_cfg:
        index_cfg = {"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"], **index_cfg}

    # Try with semantic index; preserve embeddings-unavailable fallback
    try:
        store: AsyncPostgresStore | InMemoryStore = AsyncPostgresStore(conn=pool, index=index_cfg)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    except Exception as exc:
        if _is_vector_extension_error(exc):
            logger.warning(
                "store index disabled: vector extension unavailable (%s: %s) — falling back to index=None; semantic search will be lexical only",
                exc.__class__.__name__,
                exc,
            )
            store = AsyncPostgresStore(conn=pool, index=None)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        elif _is_embeddings_unavailable(exc):
            logger.warning(
                "store index disabled: embeddings unavailable (%s: %s) — falling back to index=None; semantic search will be lexical only",
                exc.__class__.__name__,
                exc,
            )
            store = AsyncPostgresStore(conn=pool, index=None)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
        else:
            # Must not leak pool if we re-raise
            try:
                await pool.close()
            except Exception:
                pass
            raise

    # Eagerly probe vector extension availability when index is active.
    # store.setup() executes CREATE EXTENSION IF NOT EXISTS vector; if that fails
    # (extension not available on this Postgres instance), fall back to index=None
    # so boot never crashes over semantic search unavailability.
    if getattr(store, "index_config", None) is not None:
        try:
            await store.setup()  # type: ignore[union-attr]
        except Exception as exc:
            if _is_vector_extension_error(exc):
                logger.warning(
                    "store index disabled: vector extension unavailable (%s: %s) — falling back to index=None; semantic search will be lexical only",
                    exc.__class__.__name__,
                    exc,
                )
                store = AsyncPostgresStore(conn=pool, index=None)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
                # Ensure non-vector tables exist (store_migrations etc.)
                try:
                    await store.setup()  # type: ignore[union-attr]
                except Exception:
                    logger.warning("store setup (index=None) failed", exc_info=True)
            else:
                # Don't swallow non-vector errors; propagate so deploy fails visibly.
                # But preserve pool ownership — caller will aclose().
                raise
        # Note: saver.setup() is NOT called here; app.py lifespan calls
        # _setup_component on both saver and store via setup()/asetup().

    return Storage(saver=saver, store=store, _pool=pool)
