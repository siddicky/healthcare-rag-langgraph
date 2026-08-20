"""LangChain ChatOpenAI gateway for graph nodes."""

import logging
from threading import Lock
from typing import Any, Literal, Protocol, TypeVar

from langchain_core.messages import BaseMessage, ToolCall
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from healthcare_rag.services.models import sampling_params

from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.processors.retrieval import build_routing_tools

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
            from healthcare_rag.graph.prompts import get_registry

            registry = get_registry()
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
            logger.warning("LLM_STRUCTURED_STAGE_FAILED")
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
            logger.warning("LLM_COMPLETION_STAGE_FAILED")
            return default

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        """Route a query to the configured collections' retrieval tools."""
        tools = build_routing_tools(list(self.settings.collection_names))
        response = await self.chat_model("default").bind_tools(tools).ainvoke(query)
        return response.tool_calls
