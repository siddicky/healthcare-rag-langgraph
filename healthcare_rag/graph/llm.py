"""LangChain ChatOpenAI gateway for graph nodes."""

import logging
from importlib import import_module
from threading import Lock
from typing import Any, Literal, Protocol, TypeVar

from langchain_core.messages import BaseMessage, ToolCall
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from healthcare_rag.services.models import sampling_params

from healthcare_rag.graph.settings import GraphSettings

logger = logging.getLogger("MedicalRAG")
ModelT = TypeVar("ModelT", bound=BaseModel)


class PromptRegistry(Protocol):
    def format_messages(self, stage: str, **variables: Any) -> list[BaseMessage]: ...


class LangChainLLMGateway:
    """Cache model clients while keeping callbacks scoped to each graph run."""

    def __init__(
        self,
        settings: GraphSettings | None = None,
        prompts: PromptRegistry | None = None,
    ) -> None:
        self.settings: GraphSettings = settings or GraphSettings.from_env()
        self._prompts: PromptRegistry | None = prompts
        self._models: dict[tuple[str, str, float | None, str], ChatOpenAI] = {}
        self._lock: Lock = Lock()

    def chat_model(
        self,
        tier: Literal["default", "validator"],
        temperature: float | None = None,
    ) -> ChatOpenAI:
        """Return a model cached by its complete sampling configuration."""
        model = (
            self.settings.llm_model
            if tier == "default"
            else self.settings.validator_model
        )
        key = (tier, model, temperature, self.settings.reasoning_effort)
        with self._lock:
            cached = self._models.get(key)
            if cached is None:
                params = sampling_params(
                    model,
                    temperature,
                    self.settings.reasoning_effort,
                )
                cached = ChatOpenAI(
                    model=model,
                    use_responses_api=False,
                    max_retries=3,
                    **params,
                )
                self._models[key] = cached
            return cached

    def _messages(self, stage: str, variables: dict[str, Any]) -> list[BaseMessage]:
        registry = self._prompts
        if registry is None:
            try:
                from healthcare_rag.graph.prompts import PromptRegistry as Registry
            except ImportError as error:
                message = (
                    "PromptRegistry is unavailable; graph prompt support lands in todo 4"
                )
                raise NotImplementedError(message) from error
            registry = Registry()
            self._prompts = registry
        return registry.format_messages(stage, **variables)

    async def astructured(
        self,
        stage: str,
        model_type: type[ModelT],
        *,
        temperature: float | None = None,
        default: ModelT | None = None,
        **variables: Any,
    ) -> ModelT | None:
        """Render and invoke one fail-soft structured-output stage."""
        try:
            messages = self._messages(stage, variables)
            model = self.chat_model(
                "validator" if stage == "validate_answer" else "default",
                temperature,
            )
            runnable = model.with_structured_output(
                model_type,
                method="json_schema",
                strict=self.settings.structured_strict or None,
            )
            result = await runnable.ainvoke(messages)
            if isinstance(result, model_type):
                return result
            return default
        except Exception:  # noqa: BROAD_EXCEPT_OK - required fail-soft LLM boundary.
            logger.exception("Graph structured LLM stage failed: %s", stage)
            return default

    async def acomplete(
        self,
        stage: str,
        *,
        temperature: float | None = None,
        default: str = "",
        **variables: Any,
    ) -> str:
        """Render and invoke one fail-soft plain-text stage."""
        try:
            messages = self._messages(stage, variables)
            response = await self.chat_model("default", temperature).ainvoke(messages)
            return str(response.content)
        except Exception:  # noqa: BROADEXCEPT_OK - required fail-soft LLM boundary.
            logger.exception("Graph completion LLM stage failed: %s", stage)
            return default

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        """Route a query using the retrieval tools introduced in todo 5."""
        retrieval = import_module("healthcare_rag.processors.retrieval")
        build_routing_tools = getattr(retrieval, "build_routing_tools", None)
        if not callable(build_routing_tools):
            message = "Routing tools are unavailable; build_routing_tools lands in todo 5"
            raise NotImplementedError(message)
        tools = build_routing_tools(list(self.settings.collection_names))
        if not isinstance(tools, list):
            message = "build_routing_tools must return a list of LangChain tools"
            raise TypeError(message)
        response = await self.chat_model("default").bind_tools(tools).ainvoke(query)
        return response.tool_calls
