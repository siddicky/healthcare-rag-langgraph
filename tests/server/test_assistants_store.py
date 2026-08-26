from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langgraph.store.memory import InMemoryStore
from langgraph_sdk import Auth
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from server.auth import AuthMiddleware, ScopeUser
from server.config import ServerConfig
from server.manifest import UNIMPLEMENTED_PATHS
from server.storage import Storage

# ---------------------------------------------------------------------------
# Helpers to build minimal apps mounting the routes under test
# ---------------------------------------------------------------------------

def _make_graph_config() -> ServerConfig:
    return ServerConfig(
        graphs={
            "healthcare_rag": "./healthcare_rag/graph/__init__.py:graph",
            "coach": "./healthcare_rag/agent/__init__.py:coach",
        },
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"]},
        api_version="0.12.6",
        storage="memory",
        port=8000,
        local_dev=False,
        raw={},
    )


def _make_stub_auth(*, assistants_scope_for_member: bool = True, store_deny_member: bool = True) -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):
        del method, path, headers
        if authorization == "Bearer good":
            return {"identity": "member-1", "is_authenticated": True, "role": "member"}
        if authorization == "Bearer studio":
            return {"identity": "langgraph-studio-user", "is_authenticated": True, "kind": "StudioUser"}
        raise Auth.exceptions.HTTPException(status_code=401)

    @auth.on
    async def deny_all(ctx, value):
        del value
        kind = ctx.user["kind"] if "kind" in ctx.user else None
        return None if kind == "StudioUser" else False

    @auth.on.assistants.read
    async def read_assistants(ctx, value):
        del value
        if ctx.user.get("kind") == "StudioUser":
            return None
        return {"graph_id": "coach"}

    # Ensure search also filtered (task expects search to honour coach scope)
    @auth.on.assistants.search  # type: ignore[attr-defined]
    async def search_assistants(ctx, value):  # pragma: no cover
        del value
        if ctx.user.get("kind") == "StudioUser":
            return None
        return {"graph_id": "coach"}

    # store handlers — rely on deny_all for members, explicit allow for studio
    # we don't register store handlers so deny_all applies

    return auth


def _make_real_like_assistants_auth() -> Auth:
    # Minimal auth that mirrors healthcare_rag/agent/auth.py:239-247 plus deny_all
    auth = Auth()

    @auth.authenticate
    async def authenticate(method: str, path: str, headers: dict[bytes, bytes], authorization: str | None):
        del method, path, headers
        if authorization == "Bearer member":
            return {"identity": "member-1", "is_authenticated": True}
        if authorization == "Bearer studio":
            return {"identity": "langgraph-studio-user", "is_authenticated": True, "kind": "StudioUser"}
        raise Auth.exceptions.HTTPException(status_code=401)

    @auth.on
    async def deny_all(ctx, value):
        del value
        kind = ctx.user["kind"] if "kind" in ctx.user else None
        return None if kind == "StudioUser" else False

    @auth.on.assistants.read
    async def read_coach(ctx, value):
        del ctx, value
        # mirror agent/auth.py:239-247 logic using _is_studio
        # our deny_all handles studio, but read handler for non-studio returns coach filter
        # need to check kind manually
        # We implement as: studio -> None (allow all), else coach filter
        # However deny_all already covers studio allow, but read handler is checked first
        # so we must replicate: if studio allow all, else coach
        # The caller will pass ScopeUser; check kind
        # This is simplified: always return coach filter for non-studio
        # In real code, _is_studio check is inside handler
        return {"graph_id": "coach"}

    # But we need studio to bypass filter: patch to handle studio correctly
    # We'll monkey the handler to check studio via context
    orig = read_coach
    async def _patched(ctx, value):
        if ctx.user.get("kind") == "StudioUser":
            return None
        return {"graph_id": "coach"}
    # re-register
    auth._handlers[("assistants", "read")] = [_patched]
    auth._handler_cache.clear()
    return auth


def _build_app_with_routes(routes, auth: Auth, *, local_dev: bool = False, config: ServerConfig | None = None, storage: Storage | None = None, raw_graphs: dict[str, Any] | None = None) -> Starlette:
    from server.auth import AuthPolicyEngine

    cfg = config or _make_graph_config()
    st = storage or Storage(saver=__import__("langgraph.checkpoint.memory", fromlist=["InMemorySaver"]).InMemorySaver(), store=InMemoryStore(index=None))
    app = Starlette(routes=routes, middleware=[AuthMiddleware.as_starlette(auth, local_dev)])
    app.state.config = cfg  # type: ignore[attr-defined]
    app.state.storage = st  # type: ignore[attr-defined]
    if raw_graphs is not None:
        app.state.raw_graphs = raw_graphs  # type: ignore[attr-defined]
    engine = AuthPolicyEngine(auth)
    app.state.auth_engine = engine  # type: ignore[attr-defined]
    class _R:
        def is_ready(self): return True
    app.state.readiness = _R()  # type: ignore[attr-defined]
    return app


# ---------------------------------------------------------------------------
# Fake embeddings for deterministic semantic ranking
# ---------------------------------------------------------------------------

class FakeEmbeddings:
    def __init__(self, mapping: dict[str, list[float]] | None = None):
        self.mapping = mapping or {
            "cats meow": [1.0, 0.0, 0.0],
            "dogs bark": [0.0, 1.0, 0.0],
            "cars drive": [0.0, 0.0, 1.0],
            "feline meow cats": [0.95, 0.02, 0.02],
            "cats": [1.0, 0.0, 0.0],
            "dogs": [0.0, 1.0, 0.0],
        }
        self._dims = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            # texts are json dumps of values? InMemoryStore extracts field values
            # For our test, store values are {"text": "..."} or {"value": "..."} etc.
            # The extracted text is the stringified json of those fields, but our
            # InMemoryStore with fields=["$"] will embed the whole json string.
            # So we need to map substrings
            low = t.lower()
            if "cats" in low or "feline" in low or "meow" in low:
                out.append([1.0, 0.0, 0.0])
            elif "dogs" in low or "bark" in low:
                out.append([0.0, 1.0, 0.0])
            elif "cars" in low or "drive" in low:
                out.append([0.0, 0.0, 1.0])
            else:
                out.append([0.33, 0.33, 0.33])
        return out

    def embed_query(self, text: str) -> list[float]:
        docs = self.embed_documents([text])
        return docs[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


def _make_semantic_store() -> Storage:
    # Always fake: the offline suite must not depend on an OpenAI credential
    # (a placeholder key from .env.example would still reach the network), and
    # fake embeddings make the ranking assertion deterministic.
    fake = FakeEmbeddings()
    # InMemoryStore expects Embeddings object with embed_documents etc.
    # Wrap FakeEmbeddings as Embeddings-like via duck typing: InMemoryStore checks via ensure_embeddings
    # It will accept instance of Embeddings, else wraps via EmbeddingsLambda.
    # Our FakeEmbeddings is not Embeddings subclass, but EmbeddingsLambda will wrap it if we pass as embed string?
    # Instead directly set store.embeddings after construction with index=None and fake.
    store = InMemoryStore(index=None)
    # Manually inject fake
    store.index_config = {"embed": "fake", "dims": 3, "fields": ["$"], "__tokenized_fields": [("$", "$")]}
    store.embeddings = fake  # type: ignore[assignment]
    from langgraph.checkpoint.memory import InMemorySaver
    return Storage(saver=InMemorySaver(), store=store)


# ---------------------------------------------------------------------------
# Tests — these must FAIL before implementation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_assistants_search_studio_returns_both():
    from server.assistants import routes

    auth = _make_stub_auth()
    cfg = _make_graph_config()
    from langgraph.checkpoint.memory import InMemorySaver
    storage = Storage(saver=InMemorySaver(), store=InMemoryStore(index=None))
    app = _build_app_with_routes(routes, auth, local_dev=True, config=cfg, storage=storage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # local_dev with no auth -> StudioUser
        resp = await client.post("/assistants/search", json={})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items = body.get("items") if isinstance(body, dict) and "items" in body else body
        assert isinstance(items, list)
        graph_ids = {a.get("graph_id") for a in items if isinstance(a, dict)}
        assert graph_ids == {"healthcare_rag", "coach"}, f"studio should see both, got {graph_ids}"
        print(json.dumps({"items": items}, indent=2))


@pytest.mark.anyio
async def test_assistants_search_member_coach_only():
    from server.assistants import routes

    auth = _make_stub_auth()
    cfg = _make_graph_config()
    from langgraph.checkpoint.memory import InMemorySaver
    storage = Storage(saver=InMemorySaver(), store=InMemoryStore(index=None))
    app = _build_app_with_routes(routes, auth, local_dev=False, config=cfg, storage=storage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/assistants/search", json={}, headers={"authorization": "Bearer good"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items = body.get("items") if isinstance(body, dict) and "items" in body else body
        assert isinstance(items, list)
        graph_ids = {a.get("graph_id") for a in items if isinstance(a, dict)}
        assert graph_ids == {"coach"}, f"member filtered to coach only, got {graph_ids}"
        # also test with explicit graph_id filter
        resp2 = await client.post("/assistants/search", json={"graph_id": "healthcare_rag"}, headers={"authorization": "Bearer good"})
        assert resp2.status_code == 200
        body2 = resp2.json()
        items2 = body2.get("items") if isinstance(body2, dict) and "items" in body2 else body2
        # coach filter should override requested healthcare_rag, so still coach or empty
        gids2 = {a.get("graph_id") for a in items2 if isinstance(a, dict)}
        assert gids2 == set() or gids2 == {"coach"}, f"scope filter should block healthcare_rag for member, got {gids2}"


@pytest.mark.anyio
async def test_assistants_get_by_id():
    from server.assistants import routes

    auth = _make_stub_auth()
    cfg = _make_graph_config()
    from langgraph.checkpoint.memory import InMemorySaver
    storage = Storage(saver=InMemorySaver(), store=InMemoryStore(index=None))
    app = _build_app_with_routes(routes, auth, local_dev=True, config=cfg, storage=storage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Studio can fetch both
        for gid in ["coach", "healthcare_rag"]:
            resp = await client.get(f"/assistants/{gid}")
            assert resp.status_code == 200, f"GET {gid} studio failed: {resp.text}"
            body = resp.json()
            assert body.get("assistant_id") == gid or body.get("graph_id") == gid

        # Member can fetch coach but not healthcare_rag
        app2 = _build_app_with_routes(routes, auth, local_dev=False, config=cfg, storage=storage)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as c2:
            ok = await c2.get("/assistants/coach", headers={"authorization": "Bearer good"})
            assert ok.status_code == 200
            missing = await c2.get("/assistants/healthcare_rag", headers={"authorization": "Bearer good"})
            assert missing.status_code in (403, 404), f"member healthcare_rag should be hidden, got {missing.status_code} {missing.text}"
            notfound = await c2.get("/assistants/does_not_exist", headers={"authorization": "Bearer good"})
            assert notfound.status_code == 404


@pytest.mark.anyio
async def test_assistants_get_graph():
    from healthcare_rag.agent import coach
    from healthcare_rag.graph import graph as healthcare_rag_graph
    from server.assistants import routes

    auth = _make_stub_auth()
    cfg = _make_graph_config()
    from langgraph.checkpoint.memory import InMemorySaver
    storage = Storage(saver=InMemorySaver(), store=InMemoryStore(index=None))
    raw_graphs = {"coach": coach, "healthcare_rag": healthcare_rag_graph}

    # Studio can fetch the graph topology for both assistants
    app = _build_app_with_routes(routes, auth, local_dev=True, config=cfg, storage=storage, raw_graphs=raw_graphs)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for gid in ["coach", "healthcare_rag"]:
            resp = await client.get(f"/assistants/{gid}/graph")
            assert resp.status_code == 200, f"GET {gid}/graph studio failed: {resp.text}"
            body = resp.json()
            assert "nodes" in body and "edges" in body, f"missing nodes/edges: {body}"
            assert len(body["nodes"]) > 0, f"expected at least one node for {gid}"
            for node in body["nodes"]:
                data = node.get("data")
                if isinstance(data, dict):
                    assert "id" not in data, f"node data.id should be stripped: {node}"

        # Unknown assistant -> 404
        notfound = await client.get("/assistants/does_not_exist/graph")
        assert notfound.status_code == 404

        # Invalid xray -> 422
        bad_xray = await client.get("/assistants/coach/graph", params={"xray": "not-a-bool-or-int"})
        assert bad_xray.status_code == 422, bad_xray.text

    # Member scoped to coach can fetch coach's graph but not healthcare_rag's
    app2 = _build_app_with_routes(routes, auth, local_dev=False, config=cfg, storage=storage, raw_graphs=raw_graphs)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as c2:
        ok = await c2.get("/assistants/coach/graph", headers={"authorization": "Bearer good"})
        assert ok.status_code == 200, ok.text
        missing = await c2.get("/assistants/healthcare_rag/graph", headers={"authorization": "Bearer good"})
        assert missing.status_code in (403, 404), f"member healthcare_rag/graph should be hidden, got {missing.status_code} {missing.text}"


@pytest.mark.anyio
async def test_store_put_get_search_roundtrip_with_semantic():
    from server.store_routes import routes

    auth = _make_stub_auth()
    storage = _make_semantic_store()
    app = _build_app_with_routes(routes, auth, local_dev=True, storage=storage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # PUT 3 items with distinct text
        for key, text in [("k1", "cats meow"), ("k2", "dogs bark"), ("k3", "cars drive")]:
            resp = await client.put("/store/items", json={"namespace": ["test", "semantic"], "key": key, "value": {"text": text}})
            assert resp.status_code in (200, 201, 204), f"PUT {key} failed: {resp.status_code} {resp.text}"
        # GET single
        got = await client.get("/store/items", params={"namespace": "test.semantic", "key": "k1"})
        assert got.status_code == 200, f"GET failed: {got.text}"
        body = got.json()
        assert body is not None
        # value may be nested under value or direct
        val = body.get("value") if isinstance(body, dict) else None
        assert val is not None and val.get("text") == "cats meow" or body.get("text") == "cats meow", f"GET value mismatch {body}"

        # POST search with semantic query
        # Query that should match cats over dogs/cars
        search = await client.post("/store/items/search", json={"namespace_prefix": ["test", "semantic"], "query": "cats", "limit": 10})
        assert search.status_code == 200, search.text
        items = search.json().get("items")
        assert isinstance(items, list) and len(items) >= 2, f"search items {items}"
        # Determine ranking: first item should be cats
        first = items[0]
        fval = first.get("value", first) if isinstance(first, dict) else {}
        # fval may be dict with text
        text0 = fval.get("text") if isinstance(fval, dict) else ""
        # mocked deterministic: cats first
        assert text0 == "cats meow", f"expected cats first with mocked embeddings, got {first}"
        print(json.dumps({"search": search.json()}, indent=2))


@pytest.mark.anyio
async def test_store_member_denied_via_deny_all():
    from server.store_routes import routes

    auth = _make_stub_auth()
    storage = _make_semantic_store()
    app = _build_app_with_routes(routes, auth, local_dev=False, storage=storage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        hdr = {"authorization": "Bearer good"}
        put = await client.put("/store/items", json={"namespace": ["test"], "key": "k", "value": {"x": 1}}, headers=hdr)
        assert put.status_code == 403, f"member PUT should be 403, got {put.status_code} {put.text}"
        get = await client.get("/store/items", params={"namespace": "test", "key": "k"}, headers=hdr)
        assert get.status_code == 403, f"member GET should be 403, got {get.status_code} {get.text}"
        search = await client.post("/store/items/search", json={"namespace_prefix": ["test"]}, headers=hdr)
        assert search.status_code == 403, f"member search should be 403, got {search.status_code} {search.text}"
        delete = await client.request("DELETE", "/store/items", json={"namespace": ["test"], "key": "k"}, headers=hdr)
        assert delete.status_code == 403, f"member DELETE should be 403, got {delete.status_code} {delete.text}"


@pytest.mark.anyio
async def test_store_malformed_and_missing():
    from server.store_routes import routes

    auth = _make_stub_auth()
    storage = _make_semantic_store()
    app = _build_app_with_routes(routes, auth, local_dev=True, storage=storage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # invalid namespace contains period
        bad = await client.put("/store/items", json={"namespace": ["bad.label"], "key": "k", "value": {"x": 1}})
        assert bad.status_code == 422, f"invalid namespace should be 422, got {bad.status_code} {bad.text}"
        # missing item GET 404
        miss = await client.get("/store/items", params={"namespace": "nope", "key": "missing"})
        assert miss.status_code == 404, f"missing item should be 404, got {miss.status_code} {miss.text}"
        print(bad.text, miss.text)


def test_namespaces_search_stays_501():
    assert "/store/namespaces/search" in UNIMPLEMENTED_PATHS
    # also verify via full app that authenticated request still 501
    async def _run():
        from server.app import create_app
        app = create_app()
        # local_dev true so Studio principal
        import os as _os
        _os.environ["SERVER_LOCAL_DEV"] = "1"
        # need to recreate with local_dev true config
        from server.config import load_config
        cfg = load_config()
        cfg2 = ServerConfig(
            graphs=cfg.graphs,
            auth_path=cfg.auth_path,
            http_app=cfg.http_app,
            http_flags=cfg.http_flags,
            store_index=cfg.store_index,
            api_version=cfg.api_version,
            storage=cfg.storage,
            port=cfg.port,
            local_dev=True,
            raw=cfg.raw,
        )
        from server.app import create_app as ca
        app2 = ca(cfg2)
        async with app2.router.lifespan_context(app2):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://test") as client:
                r = await client.post("/store/namespaces/search", json={})
                assert r.status_code == 501, f"/store/namespaces/search should stay 501, got {r.status_code} {r.text}"
        _os.environ.pop("SERVER_LOCAL_DEV", None)
    import anyio
    anyio.run(_run)
