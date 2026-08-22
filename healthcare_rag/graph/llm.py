"""LangChain ChatOpenAI gateway for graph nodes."""

import logging
from threading import Lock
from typing import Any, Literal, Protocol, TypeVar

from anyio import to_thread
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_openai import ChatOpenAI
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel

from healthcare_rag.graph.query_response import (
    QUERY_OR_RESPOND_TOOL,
    QueryOrRespondDecision,
    RouterAction,
    project_history,
    query_or_respond_decision,
    scrub_router_text,
)
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.processors.direct_output_policy import evaluate_generated_output
from healthcare_rag.processors.privacy import PrivacySanitizer, PrivacyScanError
from healthcare_rag.processors.retrieval import build_routing_tools
from healthcare_rag.services.models import sampling_params

logger = logging.getLogger("MedicalRAG")
ModelT = TypeVar("ModelT", bound=BaseModel)
__all__ = [
    "QUERY_OR_RESPOND_TOOL",
    "LangChainLLMGateway",
    "PromptRegistry",
    "QueryOrRespondDecision",
    "RouterAction",
    "evaluate_generated_output",
]


class PromptRegistry(Protocol):
    def format_messages(self, stage: str, **variables: Any) -> list[BaseMessage]: ...


class LangChainLLMGateway:
    """Cache model clients while keeping callbacks scoped to each graph run."""

    def __init__(
        self,
        privacy: PrivacySanitizer,
        settings: GraphSettings | None = None,
        prompts: PromptRegistry | None = None,
    ) -> None:
        self._privacy = privacy
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
                    http_client=DefaultHttpxClient(),
                    http_async_client=DefaultAsyncHttpxClient(),
                    **params,
                )
                self._models[key] = cached
            return cached

    async def aclose(self) -> None:
        """Close HTTP clients retained by cached chat models."""
        with self._lock:
            models = tuple(self._models.values())
            self._models.clear()
        for model in models:
            if model.root_async_client is not None:
                await model.root_async_client.close()
            if model.root_client is not None:
                model.root_client.close()

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
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
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
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            logger.warning("LLM_COMPLETION_STAGE_FAILED")
            return default

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        """Route a query to the configured collections' retrieval tools."""
        tools: list[dict[str, Any]] = [
            dict(tool)
            for tool in build_routing_tools(list(self.settings.collection_names))
        ]
        response = await self.chat_model("default").bind_tools(tools).ainvoke(query)
        return response.tool_calls

    async def aquery_or_respond(
        self,
        history: list[BaseMessage],
        current_query: str,
    ) -> QueryOrRespondDecision:
        """Return one query-or-respond decision from the centralized default model."""
        try:
            capped_history = trim_messages(
                await to_thread.run_sync(project_history, history, self._privacy),
                strategy="last",
                token_counter=count_tokens_approximately,
                max_tokens=self.settings.history_max_tokens,
                start_on="human",
                include_system=False,
            )
            messages = [
                *self._messages("query_or_respond", {}),
                *capped_history,
                HumanMessage(
                    content=await to_thread.run_sync(
                        scrub_router_text, current_query, self._privacy
                    )
                ),
            ]
            model = self.chat_model("default").bind_tools(
                [QUERY_OR_RESPOND_TOOL],
                tool_choice="auto",
                parallel_tool_calls=False,
            )
            response = await model.ainvoke(messages)
        except PrivacyScanError:
            logger.warning("QUERY_OR_RESPOND_PRIVACY_FAILED")
            return QueryOrRespondDecision(None, "", None, "privacy_error", 0)
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            logger.warning("QUERY_OR_RESPOND_MODEL_FAILED")
            return QueryOrRespondDecision(None, "", None, "model_error", 0)
        if not isinstance(response, AIMessage):
            return QueryOrRespondDecision(None, "", None, "malformed_response", 0)
        return await to_thread.run_sync(
            query_or_respond_decision, response, self._privacy, evaluate_generated_output
        )
