from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from langgraph_sdk import Auth
from starlette.applications import Starlette
from starlette.routing import Route

from server.auth import AuthMiddleware
from server.config import ServerConfig
from server.storage import Storage, create_storage
from server.threads import routes as thread_routes
from server.runs import routes as run_routes


def _make_auth(identity: str = "member-1") -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):  # type: ignore[no-untyped-def]
        del method, path, headers, authorization
        return {"identity": identity, "is_authenticated": True, "role": "member"}

    @auth.on.threads.create
    async def create_thread(ctx: Any, value: Any):  # type: ignore[no-untyped-def]
        if ctx.user.identity == identity:
            # mirror real behavior: inject user_id
            meta = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            # ensure metadata exists
            if not isinstance(value.get("metadata"), dict):
                value["metadata"] = {}
            value["metadata"]["user_id"] = ctx.user.identity
            return None
        return False

    @auth.on.threads.read
    async def read_thread(ctx: Any, value: Any):  # type: ignore[no-untyped-def]
        del value
        return {"user_id": ctx.user.identity}

    @auth.on.threads.search
    async def search_thread(ctx: Any, value: Any):  # type: ignore[no-untyped-def]
        del value
        return {"user_id": ctx.user.identity}

    @auth.on.threads.delete
    async def delete_thread(ctx: Any, value: Any):  # type: ignore[no-untyped-def]
        del value
        return {"user_id": ctx.user.identity}

    # allow run creation for cascade test (re-use threads.create_run scope)
    @auth.on.threads.create_run
    async def create_run(ctx: Any, value: Any):  # type: ignore[no-untyped-def]
        del value
        return {"user_id": ctx.user.identity}

    return auth


async def _app_with_auth(auth: Auth) -> Starlette:
    config = ServerConfig(graphs={}, auth_path=None, http_app=None, http_flags={}, store_index={}, api_version="test")
    storage = await create_storage(config)
    # Build app with threads + runs routes
    all_routes: list[Route] = []
    all_routes.extend(thread_routes)
    all_routes.extend(run_routes)

    app = Starlette(routes=all_routes, middleware=[AuthMiddleware.as_starlette(auth, local_dev=False)])
    app.state.storage = storage  # type: ignore[attr-defined]
    # Provide dummy auth_engine via middleware engine reference
    # AuthMiddleware stores engine internally; we also set app.state.auth_engine for handlers
    from server.auth import AuthPolicyEngine

    app.state.auth_engine = AuthPolicyEngine(auth)  # type: ignore[attr-defined]
    # Provide graphs empty; run_engine needed for delete cascade best-effort but not required for thread tests
    # Create a minimal run_engine stub with runtime dict
    class _StubEngine:
        def __init__(self) -> None:
            self.storage = storage
            self.runtime: dict[str, Any] = {}
            self.queues: dict[str, Any] = {}

    app.state.run_engine = _StubEngine()  # type: ignore[attr-defined]
    app.state.graphs = {}  # type: ignore[attr-defined]
    return app


@pytest.fixture
async def client():
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, app


@pytest.fixture
async def client_other():
    # second user for isolation checks - not used as fixture but helper
    pass


@pytest.mark.anyio
async def test_supplied_id_create_and_uuid_validation() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        resp = await c.post("/threads", json={"thread_id": tid, "metadata": {"foo": "bar"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == tid
        # flat contract: user_id top-level present
        assert data["user_id"] == "member-1"
        assert data["metadata"]["user_id"] == "member-1"
        # bad uuid
        bad = await c.post("/threads", json={"thread_id": "not-a-uuid"})
        assert bad.status_code == 422
        # get with non-uuid
        notfound = await c.get("/threads/not-a-uuid")
        assert notfound.status_code == 404


@pytest.mark.anyio
async def test_if_exists_modes() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        first = await c.post("/threads", json={"thread_id": tid, "metadata": {"a": "1"}})
        assert first.status_code == 200
        # raise -> 409
        dup_raise = await c.post("/threads", json={"thread_id": tid, "if_exists": "raise"})
        assert dup_raise.status_code == 409
        # default (no if_exists) should also 409 when supplied id exists
        dup_default = await c.post("/threads", json={"thread_id": tid})
        assert dup_default.status_code == 409
        # do_nothing -> reuse existing unchanged
        dup_do_nothing = await c.post("/threads", json={"thread_id": tid, "if_exists": "do_nothing", "metadata": {"a": "2"}})
        assert dup_do_nothing.status_code == 200
        assert dup_do_nothing.json()["metadata"]["a"] == "1"  # not overwritten
        # reuse alias same as do_nothing
        dup_reuse = await c.post("/threads", json={"thread_id": tid, "if_exists": "reuse"})
        assert dup_reuse.status_code == 200
        assert dup_reuse.json()["thread_id"] == tid
        # overwrite -> updates metadata
        overw = await c.post("/threads", json={"thread_id": tid, "if_exists": "overwrite", "metadata": {"a": "9"}})
        assert overw.status_code == 200
        assert overw.json()["metadata"]["a"] == "9"
        # bad if_exists value
        bad = await c.post("/threads", json={"thread_id": str(uuid4()), "if_exists": "invalid"})
        assert bad.status_code == 422


@pytest.mark.anyio
async def test_ttl_expiry_404(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    # inject controllable clock
    base = datetime.now(UTC)
    # patch server.threads._now
    import server.threads as th

    current = {"t": base}

    def fake_now() -> datetime:
        return current["t"]

    monkeypatch.setattr(th, "_now", fake_now)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        resp = await c.post("/threads", json={"thread_id": tid, "ttl": {"strategy": "delete", "ttl": 15}})
        assert resp.status_code == 200
        # before expiry should be accessible
        got = await c.get(f"/threads/{tid}")
        assert got.status_code == 200
        # after expiry should 404
        current["t"] = base + timedelta(minutes=16)
        expired = await c.get(f"/threads/{tid}")
        assert expired.status_code == 404
        # search should not include expired
        search = await c.post("/threads/search", json={"metadata": {}, "limit": 10, "offset": 0})
        assert search.status_code == 200
        ids = [r.get("thread_id") for r in search.json()]
        assert tid not in ids


@pytest.mark.anyio
async def test_search_scope_merge_and_params() -> None:
    # member-1 creates threads
    auth1 = _make_auth("member-1")
    app1 = await _app_with_auth(auth1)
    transport1 = httpx.ASGITransport(app=app1)
    # we need shared storage for isolation test with two principals against same storage
    # So we create one app with two possible auth identities via custom authenticate that reads header?
    # Simpler: test scope isolation by using two separate apps sharing same storage object
    from server.auth import AuthPolicyEngine

    storage = app1.state.storage
    # create second app sharing storage but with member-2 auth
    auth2 = _make_auth("member-2")
    app2 = Starlette(routes=thread_routes, middleware=[AuthMiddleware.as_starlette(auth2, local_dev=False)])
    app2.state.storage = storage  # type: ignore[attr-defined]
    app2.state.auth_engine = AuthPolicyEngine(auth2)  # type: ignore[attr-defined]
    app2.state.run_engine = app1.state.run_engine  # type: ignore[attr-defined]
    app2.state.graphs = {}  # type: ignore[attr-defined]

    t1 = httpx.ASGITransport(app=app1)
    t2 = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=t1, base_url="http://test") as c1, httpx.AsyncClient(transport=t2, base_url="http://test") as c2:
        tid1 = str(uuid4())
        tid2 = str(uuid4())
        await c1.post("/threads", json={"thread_id": tid1})
        await c2.post("/threads", json={"thread_id": tid2})
        # member-1 search should only see tid1
        s1 = await c1.post("/threads/search", json={"limit": 10, "offset": 0})
        assert s1.status_code == 200
        ids1 = [r["thread_id"] for r in s1.json()]
        assert tid1 in ids1 and tid2 not in ids1
        # member-2 search only tid2
        s2 = await c2.post("/threads/search", json={"limit": 10, "offset": 0})
        assert tid2 in [r["thread_id"] for r in s2.json()] and tid1 not in [r["thread_id"] for r in s2.json()]
        # select/sort params must be accepted even if benignly ignored
        sel = await c1.post("/threads/search", json={"select": ["thread_id"], "limit": 1, "offset": 0, "sort_by": "thread_id", "sort_order": "asc"})
        assert sel.status_code == 200
        # limit/offset slicing
        # create additional threads to test pagination
        for _ in range(3):
            await c1.post("/threads", json={})
        paged = await c1.post("/threads/search", json={"limit": 2, "offset": 1, "sort_by": "created_at", "sort_order": "asc"})
        assert paged.status_code == 200
        assert len(paged.json()) <= 2
        # metadata filter + scope merge: search with metadata containing user_id other than own should yield 0 (scope overrides)
        filtered = await c1.post("/threads/search", json={"metadata": {"user_id": "member-2"}, "limit": 10})
        # member-1's scope is user_id=member-1, merged overrides requested user_id, so result should not include member-2's thread
        assert all(r.get("user_id") == "member-1" for r in filtered.json())


@pytest.mark.anyio
async def test_patch_thread() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid, "metadata": {"a": "1"}})
        patch = await c.patch(f"/threads/{tid}", json={"metadata": {"b": "2"}})
        assert patch.status_code == 200
        assert patch.json()["metadata"]["a"] == "1"
        assert patch.json()["metadata"]["b"] == "2"
        assert patch.json()["b"] == "2"  # flat contract
        # patch non-existent -> 404
        assert (await c.patch(f"/threads/{str(uuid4())}", json={"metadata": {}})).status_code == 404


@pytest.mark.anyio
async def test_delete_cascade() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid})
        # add fake run
        run_id = str(uuid4())
        app.state.storage.runs[run_id] = {"run_id": run_id, "thread_id": tid, "status": "success"}  # type: ignore[attr-defined]
        # add fake cron
        cron_id = str(uuid4())
        app.state.storage.crons[cron_id] = {"cron_id": cron_id, "thread_id": tid}  # type: ignore[attr-defined]
        del_resp = await c.delete(f"/threads/{tid}")
        assert del_resp.status_code == 204
        # thread gone
        assert (await c.get(f"/threads/{tid}")).status_code == 404
        # runs cascade
        assert run_id not in app.state.storage.runs
        # crons cascade placeholder
        assert cron_id not in app.state.storage.crons


@pytest.mark.anyio
async def test_state_shapes() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid})
        # empty state
        state = await c.get(f"/threads/{tid}/state")
        assert state.status_code == 200
        body = state.json()
        assert "values" in body and "next" in body
        # checkpoint_id query param should not crash
        state3 = await c.get(f"/threads/{tid}/state?checkpoint_id={str(uuid4())}")
        assert state3.status_code == 200
        # scope-404: other user cannot read state
        auth2 = _make_auth("member-2")
        app2 = Starlette(routes=thread_routes, middleware=[AuthMiddleware.as_starlette(auth2, local_dev=False)])
        app2.state.storage = app.state.storage  # type: ignore[attr-defined]
        from server.auth import AuthPolicyEngine

        app2.state.auth_engine = AuthPolicyEngine(auth2)  # type: ignore[attr-defined]
        app2.state.run_engine = app.state.run_engine  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as c2:
            cross = await c2.get(f"/threads/{tid}/state")
            assert cross.status_code == 404


@pytest.mark.anyio
async def test_copy_semantics() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid, "metadata": {"k": "v"}})
        copied = await c.post(f"/threads/{tid}/copy")
        assert copied.status_code == 200
        new = copied.json()
        assert new["thread_id"] != tid
        assert new["metadata"]["k"] == "v"
        assert new["user_id"] == "member-1"
        assert "expires_at" not in new
        # scope: copy of other user's thread should 404
        auth2 = _make_auth("member-2")
        app2 = Starlette(routes=thread_routes, middleware=[AuthMiddleware.as_starlette(auth2, local_dev=False)])
        app2.state.storage = app.state.storage  # type: ignore[attr-defined]
        from server.auth import AuthPolicyEngine

        app2.state.auth_engine = AuthPolicyEngine(auth2)  # type: ignore[attr-defined]
        app2.state.run_engine = app.state.run_engine  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as c2:
            assert (await c2.post(f"/threads/{tid}/copy")).status_code == 404


@pytest.mark.anyio
async def test_malformed_and_scope_isolation() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # bad if_exists
        assert (await c.post("/threads", json={"if_exists": "bad"})).status_code == 422
        # GET random uuid -> 404
        assert (await c.get(f"/threads/{str(uuid4())}")).status_code == 404
        # cross-scope read -> 404
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid})
        auth2 = _make_auth("member-2")
        app2 = Starlette(routes=thread_routes, middleware=[AuthMiddleware.as_starlette(auth2, local_dev=False)])
        app2.state.storage = app.state.storage  # type: ignore[attr-defined]
        from server.auth import AuthPolicyEngine

        app2.state.auth_engine = AuthPolicyEngine(auth2)  # type: ignore[attr-defined]
        app2.state.run_engine = app.state.run_engine  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as c2:
            assert (await c2.get(f"/threads/{tid}")).status_code == 404


@pytest.mark.anyio
async def test_crud_roundtrip_within_one_app() -> None:
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # create
        tid = str(uuid4())
        cr = await c.post("/threads", json={"thread_id": tid, "metadata": {"init": "1"}})
        assert cr.status_code == 200
        # get
        g = await c.get(f"/threads/{tid}")
        assert g.json()["thread_id"] == tid
        # patch
        p = await c.patch(f"/threads/{tid}", json={"metadata": {"extra": "2"}})
        assert p.json()["metadata"]["extra"] == "2"
        # copy
        cp = await c.post(f"/threads/{tid}/copy")
        new_id = cp.json()["thread_id"]
        # search sees both
        s = await c.post("/threads/search", json={"limit": 10})
        ids = [r["thread_id"] for r in s.json()]
        assert tid in ids and new_id in ids
        # delete original cascades
        await c.delete(f"/threads/{tid}")
        assert (await c.get(f"/threads/{tid}")).status_code == 404
        # copied still exists
        assert (await c.get(f"/threads/{new_id}")).status_code == 200


@pytest.mark.anyio
async def test_state_lookup_saver_fault_is_not_masked() -> None:
    # F2 regression: a state-plane fault during lookup must surface, never a
    # fake 200 {"values": {}, ...}. The route resolves state through
    # graph.aget_state, so the fault is raised from there.
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)

    class _BoomGraph:
        async def aget_state(self, config: object) -> object:
            raise RuntimeError("saver fault")

    app.state.graphs = {"toy": _BoomGraph()}  # type: ignore[attr-defined]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid})
        with pytest.raises(RuntimeError, match="saver fault"):
            await c.get(f"/threads/{tid}/state")


@pytest.mark.anyio
async def test_copy_logs_checkpoint_history_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # F2 regression: a checkpoint-history copy failure degrades the copy but
    # must be logged, never silent.
    auth = _make_auth("member-1")
    app = await _app_with_auth(auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tid = str(uuid4())
        await c.post("/threads", json={"thread_id": tid, "metadata": {"k": "v"}})

        async def boom(from_id: str, to_id: str) -> None:
            raise RuntimeError("checkpoint copy fault")

        monkeypatch.setattr(app.state.storage.saver, "acopy_thread", boom)  # type: ignore[attr-defined]
        with caplog.at_level("WARNING", logger="MedicalRAG"):
            copied = await c.post(f"/threads/{tid}/copy")
    assert copied.status_code == 200
    assert copied.json()["thread_id"] != tid
    assert any(
        "checkpoint history copy" in rec.getMessage() for rec in caplog.records
    )
