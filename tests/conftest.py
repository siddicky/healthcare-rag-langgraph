import os

import pytest
from dotenv import load_dotenv

load_dotenv()

# .env sets LANGSMITH_TRACING=true for real runs; tests must not push runs to the
# shared LangSmith project (and must not depend on the network). Opt back in with
# HC_RAG_TEST_TRACING=true if you really want traced test runs.
_TRACING = os.getenv("HC_RAG_TEST_TRACING", "false")
os.environ["LANGSMITH_TRACING"] = _TRACING

# Same reason, one level up: a developer's real LANGSMITH_API_KEY (from .env or
# the shell) must not turn an offline test into an authenticated API call — that
# is how the feedback-project startup probe used to pass here and nowhere else.
# Tests that need a platform key set their own via monkeypatch.
if _TRACING.lower() not in {"1", "true", "yes"}:
    os.environ["LANGSMITH_API_KEY"] = ""


def pytest_configure(config):
    config.addinivalue_line("markers", "judge: LLM-as-judge integration tests (need OPENAI_API_KEY, cost money)")


class _OfflineClient:
    """Opaque stand-in for a vector-store client handle."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def __repr__(self) -> str:
        return f"<offline {self.kind} client>"

    # `Resources.aclose()` closes whatever is in the lazy slots. Nothing was
    # ever opened here, so report that honestly and make close a no-op.
    def is_connected(self) -> bool:
        return False

    def close(self) -> None:
        return None


@pytest.fixture
def seal_offline_resources():
    """Seed a `Resources` object's lazy client slots so nothing dials out.

    `retrieve_documents` opens `resources.weaviate()` (or `.pinecone()`) before
    it calls the search callable, and it does so even when a test has injected
    `resources.hybrid_search`: the client is an *argument* to the search, and
    the fakes ignore it. Left alone, `Resources.weaviate()` really connects to
    127.0.0.1:8080. On a dev box running `docker-compose.yml` that connect
    succeeds and the graph tests pass by accident; anywhere without Weaviate
    (CI) it raises `WeaviateBaseError`, the node burns its three retries and
    fails soft, and the injected fake records nothing -- which is why the
    assertions read `assert [] == [('Lipitor', ...)]` on Linux only.

    Seeding the caches makes `Resources.weaviate()` / `.pinecone()` return the
    stub without connecting, so tests that inject a search stay hermetic.
    Tests that exercise the connect path itself (`tests/graph/test_resources.py`)
    must not use this.
    """

    def seal(resources):
        resources._weaviate = _OfflineClient("weaviate")
        resources._pinecone_client = _OfflineClient("pinecone")
        resources._pinecone_index = _OfflineClient("pinecone index")
        return resources

    return seal
