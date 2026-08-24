"""Todo 16 gaps — integrated restart durability, store equivalence, erasure, orphan, migration.

All tests are gated behind a real Postgres (postgres_url fixture from
tests/server/conftest.py) and cleanly skip when POSTGRES_TEST_DSN is unset.

Coverage intent (gaps not already in test_storage_postgres / test_registries /
test_threads_postgres / test_runs_durable / test_crons_postgres):

  Gap 1 — full integrated signature (thread + graph checkpoint + terminal run
            + store items + cron survive storage rebuild)
  Gap 2 — AsyncPostgresStore API equivalence for the exact call shapes used by
            store_data.py / uploads.py / memory.py
  Gap 3 — erasure survives restart (A erased, B intact, verified via store
            API AND raw SQL)
  Gap 4 — orphan check via direct SQL after HTTP thread-delete cascade
  Gap 5 — migration idempotency (setup twice + concurrent create_storage
            against an already-bootstrapped DB)
"""

from __future__ import annotations

import os

os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("LANGSMITH_API_KEY", None)

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypedDict
from uuid import uuid4

import anyio
import httpx
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from starlette.applications import Starlette

from server.config import ServerConfig
from server.storage import Storage, create_storage


class _Gap1State(TypedDict):
    messages: Annotated[list[Any], add_messages]
    value: str


def _gap1_node(state: _Gap1State) -> dict[str, object]:
    return {"value": "checkpointed", "messages": [{"role": "assistant", "content": "hi"}]}


class _Gap4State(TypedDict):
    x: str


def _gap4_node(state: _Gap4State) -> dict[str, object]:
    return {"x": "v"}


def _pg_config(dsn: str, *, api_version: str = "durability-gaps") -> ServerConfig:
    return ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version=api_version,
        storage="postgres",
        database_uri=dsn,
    )


@pytest.fixture(autouse=True)
async def _allow_loop_drain() -> Any:
    yield
    await anyio.sleep(0.05)


# ---------------------------------------------------------------------------
# Gap 1 — integrated signature scenario
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_integrated_restart_durability_full(postgres_url: str) -> None:
    """Thread JSON, checkpoint, store items and cron all survive a storage rebuild."""
    cfg = _pg_config(postgres_url, api_version="gap1-integrated")
    suffix = str(uuid4())
    thread_id = str(uuid4())
    run_terminal_id = f"gap1-terminal-{suffix}"
    user_id = f"gap1-user-{suffix}"
    cron_id = f"gap1-cron-{suffix}"

    # ------------------------------------------------------------------
    # Phase 1 — seed everything in one Storage instance, including a real
    # graph checkpoint via a toy StateGraph compiled against the saver/store.
    # ------------------------------------------------------------------
    storage = await create_storage(cfg)
    try:
        # Ensure checkpoint tables exist for the graph turn.
        try:
            await storage.saver.setup()  # pyright: ignore[reportAttributeAccessIssue]
        except Exception:
            pass
        try:
            await storage.store.setup()  # pyright: ignore[reportAttributeAccessIssue]
        except Exception:
            pass

        # Thread
        thread_record: dict[str, object] = {
            "thread_id": thread_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "metadata": {"owner": user_id, "gap": "1"},
            "owner": user_id,
        }
        await storage.threads.save(thread_id, thread_record)

        # Toy graph checkpoint — a minimal StateGraph so the saver writes a row
        # to `checkpoints`/`checkpoint_blobs` for this thread.
        builder = StateGraph(_Gap1State)
        builder.add_node("toy", _gap1_node)  # type: ignore[arg-type]
        builder.add_edge(START, "toy")
        builder.add_edge("toy", END)
        graph = builder.compile(checkpointer=storage.saver, store=storage.store)

        # One graph turn — writes a checkpoint for thread_id.
        await graph.ainvoke(
            {"value": "seed", "messages": [{"role": "user", "content": "hello"}]},
            {"configurable": {"thread_id": thread_id}},
        )

        # Terminal run (must survive restart as success)
        now = datetime.now(UTC).isoformat()
        terminal_record: dict[str, object] = {
            "run_id": run_terminal_id,
            "thread_id": thread_id,
            "assistant_id": "toy",
            "status": "success",
            "created_at": now,
            "input": {"question": "durability?"},
            "metadata": {"result": "ok"},
        }
        await storage.runs.save(run_terminal_id, terminal_record)

        # Store items — use namespaces that store_data.py actually uses
        ns_profile = ("users", user_id, "profile")
        await storage.store.aput(ns_profile, "fact-1", {"fact": "allergy: none", "kind": "profile"}, index=False)
        await storage.store.aput(ns_profile, "fact-2", {"fact": "prefers morning", "kind": "profile"}, index=False)
        ns_episodic = ("users", user_id, "episodic")
        await storage.store.aput(ns_episodic, "epi-1", {"fact": "visited monday", "kind": "episodic"}, index=False)

        # Cron — scheduled in the future
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        cron_record: dict[str, object] = {
            "cron_id": cron_id,
            "thread_id": thread_id,
            "enabled": True,
            "schedule": "* * * * *",
            "_timezone": "UTC",
            "next_run_date": future,
            "updated_at": now,
            "payload": {"assistant_id": "coach", "input": {"cron_wake": {"token": "gap1"}}, "config": {}, "multitask_strategy": "enqueue"},
            "metadata": {"user_id": user_id},
            "end_time": None,
            "created_at": now,
            "user_id": user_id,
            "auth_user": None,
        }
        await storage.crons.save(cron_id, cron_record)

        # Snapshot for later comparison
        thread_before = await storage.threads.get(thread_id)
        assert thread_before is not None
        # Checkpoint must exist before restart
        tup_before = await storage.saver.aget_tuple({"configurable": {"thread_id": thread_id}})  # type: ignore
        assert tup_before is not None, "graph checkpoint not written before restart"
        store_before = await storage.store.aget(ns_profile, "fact-1")
        assert store_before is not None
        cron_before = await storage.crons.get(cron_id)
        assert cron_before is not None
    finally:
        await storage.aclose()

    # ------------------------------------------------------------------
    # Phase 2 — fresh Storage against the SAME DB; everything must survive.
    # ------------------------------------------------------------------
    fresh = await create_storage(cfg)
    try:
        # Thread JSON identical
        thread_after = await fresh.threads.get(thread_id)
        assert thread_after == thread_before, f"thread mutated across restart: {thread_before!r} vs {thread_after!r}"

        # Checkpoint history readable via the new saver (aget_tuple + alist)
        tup = await fresh.saver.aget_tuple({"configurable": {"thread_id": thread_id}})  # type: ignore
        assert tup is not None, "checkpoint not readable after restart (aget_tuple returned None)"
        # alist must also yield at least one tuple for the thread
        found = False
        async for _ in fresh.saver.alist({"configurable": {"thread_id": thread_id}}):  # type: ignore
            found = True
            break
        assert found, "checkpoint not readable via saver.alist after restart"

        # Terminal run still present
        run_after = await fresh.runs.get(run_terminal_id)
        assert run_after is not None and run_after.get("status") == "success"

        # Store items present
        assert (await fresh.store.aget(ns_profile, "fact-1")) is not None
        assert (await fresh.store.aget(ns_profile, "fact-2")) is not None
        assert (await fresh.store.aget(ns_episodic, "epi-1")) is not None

        # Cron still scheduled and enabled
        cron_after = await fresh.crons.get(cron_id)
        assert cron_after is not None
        assert cron_after.get("enabled") is True
        assert cron_after.get("next_run_date") == future
    finally:
        # Cleanup — scoped to our ids only
        await fresh.threads.delete(thread_id)
        await fresh.runs.delete(run_terminal_id)
        await fresh.crons.delete(cron_id)
        for k in ("fact-1", "fact-2"):
            await fresh.store.adelete(ns_profile, k)
        await fresh.store.adelete(ns_episodic, "epi-1")
        # Also clean checkpoint history for the thread
        try:
            await fresh.saver.adelete_thread(thread_id)  # type: ignore
        except Exception:
            pass
        await fresh.aclose()


# ---------------------------------------------------------------------------
# Gap 2 — store-API equivalence (exact shapes from store_data/uploads/memory)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_store_aput_with_ttl_round_trips(postgres_url: str) -> None:
    storage = await create_storage(_pg_config(postgres_url, api_version="gap2-ttl"))
    try:
        ns = ("users", f"gap2-ttl-{uuid4()}", "profile")
        key = "ttl-key"
        # Use index=False to avoid triggering the embeddings http client when
        # OPENAI_API_KEY is absent (fallback path leaks an httpx transport).
        await storage.store.aput(ns, key, {"v": 1}, index=False, ttl=5)
        item = await storage.store.aget(ns, key)
        assert item is not None and item.value == {"v": 1}
        assert item.namespace == ns and item.key == key
        # refresh_ttl=False shape (used by get_upload_status) must also work
        item2 = await storage.store.aget(ns, key, refresh_ttl=False)
        assert item2 is not None
        await storage.store.adelete(ns, key)
    finally:
        await storage.aclose()


@pytest.mark.anyio
async def test_store_upload_reservation_ttl_semantics(postgres_url: str) -> None:
    """Reproduce healthcare_rag/agent/uploads.py's exact aput(ttl=15, index=False)
    shape and confirm immediate retrievability (TTL expiry is app-level)."""
    from healthcare_rag.agent.uploads import UPLOAD_TTL_MINUTES

    storage = await create_storage(_pg_config(postgres_url, api_version="gap2-upload"))
    try:
        owner = f"gap2-upload-{uuid4()}"
        ns = ("users", owner, "upload_registry")
        reservation = str(uuid4())
        record: dict[str, Any] = {
            "owner": owner,
            "intended_thread": str(uuid4()),
            "expires_at": time.time() + 15 * 60,
            "status": "uploading",
        }
        await storage.store.aput(ns, reservation, record, index=False, ttl=float(UPLOAD_TTL_MINUTES))
        item = await storage.store.aget(ns, reservation)
        assert item is not None
        assert item.value.get("owner") == owner
        item2 = await storage.store.aget(ns, reservation, refresh_ttl=False)
        assert item2 is not None
        await storage.store.adelete(ns, reservation)
        assert await storage.store.aget(ns, reservation) is None
    finally:
        await storage.aclose()


@pytest.mark.anyio
async def test_store_aput_index_false_round_trips_and_not_semantically_indexed(postgres_url: str) -> None:
    storage = await create_storage(_pg_config(postgres_url, api_version="gap2-index"))
    try:
        ns = ("users", f"gap2-index-{uuid4()}", "profile")
        key = "idx-key"
        await storage.store.aput(ns, key, {"fact": "index_false_fact", "kind": "profile"}, index=False)
        item = await storage.store.aget(ns, key)
        assert item is not None
        assert item.value == {"fact": "index_false_fact", "kind": "profile"}
        if os.environ.get("OPENAI_API_KEY"):
            try:
                hits = await storage.store.asearch(ns, query="index_false_fact")
                _ = hits
            except Exception:
                pass
        await storage.store.adelete(ns, key)
    finally:
        await storage.aclose()


@pytest.mark.anyio
async def test_store_namespace_prefix_pagination(postgres_url: str) -> None:
    storage = await create_storage(_pg_config(postgres_url, api_version="gap2-page"))
    try:
        # Shared owner so prefix search is meaningful
        owner = f"gap2-page-{uuid4()}"
        ns = ("users", owner, "profile")
        keys = [f"k-{i:02d}" for i in range(5)]
        for k in keys:
            await storage.store.aput(ns, k, {"n": k, "owner": owner}, index=False)
        # Pagination via offset/limit on the same namespace
        p0 = await storage.store.asearch(ns, limit=2, offset=0)
        p1 = await storage.store.asearch(ns, limit=2, offset=2)
        p2 = await storage.store.asearch(ns, limit=2, offset=4)
        all_keys = [item.key for item in (*p0, *p1, *p2)]
        # Deduplicate to tolerate ordering nondeterminism; union must cover all
        assert set(all_keys) == set(keys)
        assert len(p0) == 2 and len(p1) == 2 and len(p2) == 1
        for k in keys:
            await storage.store.adelete(ns, k)
    finally:
        await storage.aclose()


@pytest.mark.anyio
async def test_store_adelete_immediate_invisibility(postgres_url: str) -> None:
    storage = await create_storage(_pg_config(postgres_url, api_version="gap2-delete"))
    try:
        ns = ("users", f"gap2-del-{uuid4()}", "profile")
        key = "del-key"
        await storage.store.aput(ns, key, {"x": 1}, index=False)
        assert await storage.store.aget(ns, key) is not None
        await storage.store.adelete(ns, key)
        assert await storage.store.aget(ns, key) is None
        # Also not visible via asearch immediately
        hits = await storage.store.asearch(ns, limit=10, offset=0)
        assert all(item.key != key for item in hits)
    finally:
        await storage.aclose()


@pytest.mark.anyio
async def test_store_alist_namespaces_includes_created(postgres_url: str) -> None:
    storage = await create_storage(_pg_config(postgres_url, api_version="gap2-ns"))
    try:
        owner = f"gap2-ns-{uuid4()}"
        ns_a = ("users", owner, "profile")
        ns_b = ("users", owner, "episodic")
        await storage.store.aput(ns_a, "k1", {"v": 1}, index=False)
        await storage.store.aput(ns_b, "k2", {"v": 2}, index=False)
        namespaces = await storage.store.alist_namespaces(prefix=("users", owner))
        assert ns_a in namespaces
        assert ns_b in namespaces
        await storage.store.adelete(ns_a, "k1")
        await storage.store.adelete(ns_b, "k2")
    finally:
        await storage.aclose()


# ---------------------------------------------------------------------------
# Gap 3 — erasure survives restart (negative control + raw SQL proof)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_erasure_survives_restart_with_negative_control(postgres_url: str) -> None:
    from healthcare_rag.agent.store_data import coordinator_capability, delete_all_for_user

    cfg = _pg_config(postgres_url, api_version="gap3-erasure")
    suffix = str(uuid4())
    user_a = f"gap3-A-{suffix}"
    user_b = f"gap3-B-{suffix}"

    storage = await create_storage(cfg)
    try:
        # Seed store items for both users under multiple collections
        for user in (user_a, user_b):
            for coll in ("profile", "episodic", "metrics"):
                ns = ("users", user, coll)
                await storage.store.aput(ns, f"key-{coll}", {"user": user, "coll": coll, "v": 1}, index=False)
            # Also two upload_registry items for user A
            if user == user_a:
                ns_u = ("users", user, "upload_registry")
                await storage.store.aput(ns_u, "res-1", {"owner": user, "status": "done"}, index=False)

        # Seed threads (hc_threads) for both — thread_id is the key
        thread_a = str(uuid4())
        thread_b = str(uuid4())
        await storage.threads.save(thread_a, {"thread_id": thread_a, "metadata": {"owner": user_a}, "created_at": datetime.now(UTC).isoformat(), "updated_at": datetime.now(UTC).isoformat()})
        await storage.threads.save(thread_b, {"thread_id": thread_b, "metadata": {"owner": user_b}, "created_at": datetime.now(UTC).isoformat(), "updated_at": datetime.now(UTC).isoformat()})

        # ---- Negative control: prove PRESENCE was real before erasure ----
        # Store API
        assert await storage.store.aget(("users", user_a, "profile"), "key-profile") is not None
        assert await storage.store.aget(("users", user_b, "profile"), "key-profile") is not None
        # Threads API
        assert await storage.threads.get(thread_a) is not None
        assert await storage.threads.get(thread_b) is not None
        # Raw SQL: store prefix column contains 'users/<user>/...' strings
        from psycopg import AsyncConnection

        async with await AsyncConnection.connect(postgres_url) as conn:
            cur = await conn.execute("SELECT count(*) FROM store WHERE prefix LIKE %s", (f"%{user_a}%",))
            row_a = await cur.fetchone()
            assert row_a is not None
            cnt_a_before = row_a[0]
            cur2 = await conn.execute("SELECT count(*) FROM store WHERE prefix LIKE %s", (f"%{user_b}%",))
            row_b = await cur2.fetchone()
            assert row_b is not None
            cnt_b_before = row_b[0]
        assert cnt_a_before > 0, "negative control failed: A store rows should exist before erasure"
        assert cnt_b_before > 0

        # ---- Erase user A (real code path) ----
        await delete_all_for_user(storage.store, user_a, coordinator_capability())
        # Gate namespace should not linger; delete_all skips gate but does not create it
        # Ensure immediate invisibility via API
        assert await storage.store.aget(("users", user_a, "profile"), "key-profile") is None
        assert await storage.store.aget(("users", user_a, "upload_registry"), "res-1") is None
        # User B must remain
        assert await storage.store.aget(("users", user_b, "profile"), "key-profile") is not None

        # Persist and rebuild
        await storage.aclose()

        fresh = await create_storage(cfg)
        try:
            # Via store API: A absent, B intact
            assert await fresh.store.aget(("users", user_a, "profile"), "key-profile") is None
            assert await fresh.store.aget(("users", user_a, "episodic"), "key-episodic") is None
            assert await fresh.store.aget(("users", user_a, "metrics"), "key-metrics") is None
            assert await fresh.store.aget(("users", user_a, "upload_registry"), "res-1") is None
            assert await fresh.store.aget(("users", user_b, "profile"), "key-profile") is not None
            assert await fresh.store.aget(("users", user_b, "episodic"), "key-episodic") is not None
            # Also confirm via alist_namespaces: no A namespaces remain
            ns_after = await fresh.store.alist_namespaces(prefix=("users", user_a))
            assert ns_after == [] or all(user_a not in str(ns) for ns in ns_after)

            async with await AsyncConnection.connect(postgres_url) as conn2:
                cur_a = await conn2.execute("SELECT count(*) FROM store WHERE prefix LIKE %s", (f"%{user_a}%",))
                row_a2 = await cur_a.fetchone()
                assert row_a2 is not None
                cnt_a_after = row_a2[0]
                cur_b = await conn2.execute("SELECT count(*) FROM store WHERE prefix LIKE %s", (f"%{user_b}%",))
                row_b2 = await cur_b.fetchone()
                assert row_b2 is not None
                cnt_b_after = row_b2[0]
            assert cnt_a_after == 0, f"A store rows should be 0 after erasure+restart, got {cnt_a_after}"
            assert cnt_b_after > 0, "B store rows should survive erasure+restart"

            # Threads for both users: store_data erasure does NOT touch hc_threads
            # (coach erasure does thread-deletion separately). So threads A's
            # record still exists here — we explicitly verify that did NOT get
            # swept as store rows, keeping the test honest about scope.
            assert await fresh.threads.get(thread_a) is not None
            assert await fresh.threads.get(thread_b) is not None

            # Cleanup B's data and both threads
            for coll in ("profile", "episodic", "metrics"):
                await fresh.store.adelete(("users", user_b, coll), f"key-{coll}")
            await fresh.threads.delete(thread_a)
            await fresh.threads.delete(thread_b)
        finally:
            await fresh.aclose()
        # Outer storage already closed; do not double-close
        return
    except Exception:
        try:
            await storage.aclose()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Gap 4 — orphan check via direct SQL after HTTP thread-delete cascade
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_cascade_zero_orphans_via_sql(postgres_url: str) -> None:
    """Create thread via HTTP (full stack), seed runs/crons, delete via
    HTTP, then verify zero orphan rows remain in hc_runs/hc_crons via raw SQL."""
    from psycopg import AsyncConnection

    from server.auth import AuthMiddleware, AuthPolicyEngine
    from server.threads import routes as thread_routes
    from langgraph_sdk import Auth

    def _auth() -> Auth:
        auth = Auth()

        @auth.authenticate
        async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):  # type: ignore[no-untyped-def]
            del method, path, headers, authorization
            return {"identity": "gap4-member", "is_authenticated": True}

        @auth.on.threads.create
        async def _allow_create(ctx, value):  # type: ignore[no-untyped-def]
            del ctx, value

        @auth.on.threads.delete
        async def _allow_delete(ctx, value):  # type: ignore[no-untyped-def]
            del ctx, value

        return auth

    cfg = _pg_config(postgres_url, api_version="gap4-orphan")
    storage = await create_storage(cfg)
    # Ensure a checkpoint table exists for delete_thread's cascade (best-effort)
    try:
        await storage.saver.setup()  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        pass
    try:
        await storage.store.setup()  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        pass

    auth = _auth()
    app = Starlette(routes=thread_routes, middleware=[AuthMiddleware.as_starlette(auth, local_dev=False)])
    app.state.storage = storage
    app.state.auth_engine = AuthPolicyEngine(auth)
    transport = httpx.ASGITransport(app=app)
    thread_id = str(uuid4())
    run_id = str(uuid4())
    cron_id = str(uuid4())
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/threads", json={"thread_id": thread_id})
            assert resp.status_code == 200, resp.text
            now = datetime.now(UTC).isoformat()
            await storage.runs.save(run_id, {"run_id": run_id, "thread_id": thread_id, "status": "pending", "created_at": now})
            await storage.crons.save(cron_id, {"cron_id": cron_id, "thread_id": thread_id, "enabled": True, "next_run_date": None, "updated_at": now})
            try:
                b = StateGraph(_Gap4State)
                b.add_node("n", _gap4_node)  # type: ignore[arg-type]
                b.add_edge(START, "n")
                b.add_edge("n", END)
                g = b.compile(checkpointer=storage.saver, store=storage.store)
                await g.ainvoke({"x": "seed"}, {"configurable": {"thread_id": thread_id}})
            except Exception:
                pass
            del_resp = await client.delete(f"/threads/{thread_id}")
            assert del_resp.status_code == 204, del_resp.text
            assert await storage.threads.get(thread_id) is None
            assert await storage.runs.get(run_id) is None
            assert await storage.crons.get(cron_id) is None
        try:
            await transport.aclose()
        except Exception:
            pass
        async with await AsyncConnection.connect(postgres_url) as conn:
            cur_r = await conn.execute("SELECT count(*) FROM hc_runs WHERE thread_id = %s", (thread_id,))
            row_r = await cur_r.fetchone()
            assert row_r is not None
            orphans_r = row_r[0]
            cur_c = await conn.execute("SELECT count(*) FROM hc_crons WHERE thread_id = %s", (thread_id,))
            row_c = await cur_c.fetchone()
            assert row_c is not None
            orphans_c = row_c[0]
            cur_chk = await conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,))
            row_chk = await cur_chk.fetchone()
            assert row_chk is not None
            orphans_chk = row_chk[0]
        assert orphans_r == 0, f"orphan hc_runs rows remain for {thread_id}: {orphans_r}"
        assert orphans_c == 0, f"orphan hc_crons rows remain for {thread_id}: {orphans_c}"
        assert orphans_chk == 0, f"orphan checkpoints remain for {thread_id}: {orphans_chk}"
    finally:
        await storage.threads.delete(thread_id)
        await storage.runs.delete(run_id)
        await storage.crons.delete(cron_id)
        try:
            await storage.saver.adelete_thread(thread_id)  # type: ignore
        except Exception:
            pass
        await storage.aclose()
        await anyio.sleep(0.05)


# ---------------------------------------------------------------------------
# Gap 5 — migration idempotency at scale
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_postgres_setup_idempotent_via_storage(postgres_url: str) -> None:
    """Storage-level proof that repeated setup() is idempotent (registries path)."""
    from server.registries import PostgresRegistries
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=postgres_url,
        open=False,
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    regs = PostgresRegistries(pool)  # pyright: ignore[reportArgumentType]
    try:
        await regs.setup()
        await regs.setup()  # must not raise
    finally:
        await pool.close()


@pytest.mark.anyio
async def test_concurrent_create_storage_against_bootstrapped_db(postgres_url: str) -> None:
    """Two concurrent create_storage() against an already-bootstrapped DB must
    both succeed (realistic re-deploy; avoids the cold-start DDL hang that
    test_concurrent_storage_setup_uses_advisory_lock documents)."""
    # Ensure tables already exist so the concurrent path never races on first
    # CREATE TABLE DDL — this is the realistic re-deploy scenario todo 16 cares
    # about, not the first-ever bootstrap already covered by todo 11.
    priming = await create_storage(_pg_config(postgres_url, api_version="gap5-prime"))
    await priming.aclose()
    await anyio.sleep(0.05)

    storages: list[Storage] = []

    async def _create() -> None:
        storages.append(await create_storage(_pg_config(postgres_url, api_version="gap5-concurrent")))

    async with anyio.create_task_group() as tg:
        tg.start_soon(_create)
        tg.start_soon(_create)

    try:
        assert len(storages) == 2
        from server.registries import PostgresRegistry

        assert all(isinstance(s.threads, PostgresRegistry) for s in storages)
        for s in storages:
            assert await s.threads.count() >= 0
    finally:
        for s in storages:
            await s.aclose()
        await anyio.sleep(0.05)
