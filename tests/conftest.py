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
