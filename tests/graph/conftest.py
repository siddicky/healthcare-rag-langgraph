from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import pytest
from langchain_core.messages import ToolCall

from healthcare_rag.graph.llm import LangChainLLMGateway, QueryOrRespondDecision
from healthcare_rag.graph.resources import Resources, override
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.models.retrieval import QueryResultList


@dataclass(slots=True)  # noqa: MUTABLE_OK - counting fake records calls.
class FakeGateway(LangChainLLMGateway):
    structured_results: dict[str, Any] = field(default_factory=dict)
    completion_results: dict[str, str] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    route_error: Exception | None = None
    query_decision: QueryOrRespondDecision | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def astructured(
        self,
        stage: str,
        model_type: type[Any],
        *,
        temperature: float | None = None,
        default: Any = None,
        **variables: Any,
    ) -> Any:
        self.calls.append(
            {
                "method": "structured",
                "stage": stage,
                "model_type": model_type,
                "temperature": temperature,
                **variables,
            }
        )
        return self.structured_results.get(stage, default)

    async def acomplete(
        self,
        stage: str,
        *,
        temperature: float | None = None,
        default: str = "",
        **variables: Any,
    ) -> str:
        self.calls.append(
            {
                "method": "complete",
                "stage": stage,
                "temperature": temperature,
                **variables,
            }
        )
        return self.completion_results.get(stage, default)

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        self.calls.append({"method": "route_tools", "query": query})
        if self.route_error is not None:
            raise self.route_error
        return self.tool_calls

    async def aquery_or_respond(
        self,
        history: list[Any],
        current_query: str,
    ) -> QueryOrRespondDecision:
        self.calls.append(
            {
                "method": "query_or_respond",
                "history": history,
                "query": current_query,
            }
        )
        assert self.query_decision is not None
        return self.query_decision


class FakeLLMGateway(LangChainLLMGateway):
    def __init__(self, **scripts: Iterable[Any]) -> None:
        super().__init__()
        self.scripts = {stage: deque(values) for stage, values in scripts.items()}
        self.calls: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def astructured(
        self,
        stage: str,
        model_type: type[Any],
        *,
        temperature: float | None = None,
        default: Any = None,
        **variables: Any,
    ) -> Any:
        self.calls[stage].append({"temperature": temperature, **variables})
        scripted = self.scripts.get(stage)
        if not scripted:
            return default
        value = scripted.popleft()
        if isinstance(value, Exception):
            raise value
        if isinstance(value, model_type):
            return value
        return default


@dataclass(slots=True)  # noqa: MUTABLE_OK - counting fake records calls.
class FakeRetriever:
    results: dict[str, QueryResultList] = field(default_factory=dict)
    error_factory: Callable[[], Exception] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def __call__(
        self, _client: Any, collection_name: str, query: str
    ) -> QueryResultList:
        self.calls.append((collection_name, query))
        if self.error_factory is not None:
            raise self.error_factory()
        return self.results.get(collection_name, QueryResultList(results=[]))


class ResourceInstaller(Protocol):
    def __call__(
        self,
        gateway: FakeGateway,
        *,
        retriever: FakeRetriever | None = None,
        disabled: tuple[str, ...] = (),
    ) -> Resources: ...


def make_settings(*disabled: str) -> GraphSettings:
    return GraphSettings(
        safety_gate_enabled=True,
        max_subqueries=3,
        decompose_only_complex=True,
        disabled_stages=frozenset(disabled),
        llm_model="fake-default",
        validator_model="fake-validator",
        reasoning_effort="none",
        history_max_tokens=4000,
        structured_strict=False,
        checkpoint_uri="",
        openai_api_key="test",
    )


@pytest.fixture
def install_resources() -> Iterator[ResourceInstaller]:
    def install(
        gateway: FakeGateway,
        *,
        retriever: FakeRetriever | None = None,
        disabled: tuple[str, ...] = (),
    ) -> Resources:
        resources = Resources(make_settings(*disabled))
        resources._gateway = gateway
        if retriever is not None:
            resources.hybrid_search = retriever
        override(resources)
        return resources

    yield install
    override(Resources(make_settings()))
