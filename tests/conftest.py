import os

import pytest
from dotenv import load_dotenv

load_dotenv()

# .env sets LANGSMITH_TRACING=true for real runs; tests must not push runs to the
# shared LangSmith project (and must not depend on the network). Opt back in with
# HC_RAG_TEST_TRACING=true if you really want traced test runs.
os.environ["LANGSMITH_TRACING"] = os.getenv("HC_RAG_TEST_TRACING", "false")


def pytest_configure(config):
    config.addinivalue_line("markers", "judge: LLM-as-judge integration tests (need OPENAI_API_KEY, cost money)")
