import os
from collections.abc import Iterator

import pytest
from dotenv import load_dotenv

load_dotenv()

# .env sets LANGSMITH_TRACING=true for real runs; tests must not push runs to the
# shared LangSmith project (and must not depend on the network). Opt back in with
# HC_RAG_TEST_TRACING=true if you really want traced test runs.
os.environ["LANGSMITH_TRACING"] = os.getenv("HC_RAG_TEST_TRACING", "false")


def pytest_configure(config):
    config.addinivalue_line("markers", "judge: LLM-as-judge integration tests (need OPENAI_API_KEY, cost money)")



@pytest.fixture(scope="session", autouse=True)
def privacy_service_server() -> Iterator[str]:
    """Serve services/privacy on a loopback port for the whole session.

    The graph reaches the presidio engine only over HTTP, so the tests run the
    real FastAPI app under uvicorn in a daemon thread and point
    PRIVACY_SERVICE_URL at it. In-process tests and the spawned `langgraph dev`
    subprocess (tests/agent) both go through the same socket; its socket guard
    allows 127.0.0.1.
    """
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    from privacy_service.main import app

    os.environ.setdefault("PRIVACY_SERVICE_TOKEN", "test-token")
    token = os.environ["PRIVACY_SERVICE_TOKEN"]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    url = f"http://127.0.0.1:{port}"
    os.environ["PRIVACY_SERVICE_URL"] = url  # never a real deployment from .env

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, name="privacy-service", daemon=True)
    thread.start()
    deadline = time.monotonic() + 90
    headers = {"Authorization": f"Bearer {token}"}
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("privacy service exited during startup")
        try:
            if httpx.get(f"{url}/health", headers=headers, timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("privacy service did not become ready")
    yield url
    server.should_exit = True
    thread.join(timeout=10)
