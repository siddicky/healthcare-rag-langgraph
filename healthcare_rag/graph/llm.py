"""LangChain ChatOpenAI gateway for graph nodes."""

import logging
import re
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, Any, Final, Literal, Protocol, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.processors.privacy import MAX_INPUT_BYTES, PrivacyScanError
from healthcare_rag.processors.retrieval import build_routing_tools
from healthcare_rag.processors.safety import NUMERIC_DOSE, scrub_phi
from healthcare_rag.services.models import sampling_params

logger = logging.getLogger("MedicalRAG")
ModelT = TypeVar("ModelT", bound=BaseModel)
RouterAction = Literal["direct", "retrieve"]
QUERY_OR_RESPOND_TOOL: Final[dict[str, Any]] = {
    "type": "function",
    "function": {
        "name": "retrieve_monographs",
        "description": "Retrieve Lipitor and metformin product-monograph information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The monograph question to retrieve evidence for.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
_HISTORY_MESSAGE_TYPES: Final = {"human": HumanMessage, "ai": AIMessage}
_DIRECT_ADVICE_PREFIX: Final = re.compile(
    r"(?i)^\s*(?:please\s+|you\s+(?:should|must|need to)\s+|"
    r"i\s+(?:recommend|advise)\s+|(?:take|stop|start|double|increase|decrease|skip|hold|use|swallow)\b)"
)
_PERCENT_QUANTITY = re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s*(?:percent\b|%)")


class RetrieveMonographsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@dataclass(frozen=True, slots=True)
class QueryOrRespondDecision:
    action: RouterAction | None
    direct_content: str
    tool_query: str | None
    fallback_reason: str | None
    tool_call_count: int


def _scrub_router_text(text: str) -> str:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise PrivacyScanError("PRIVACY_INPUT_TOO_LARGE")
    return scrub_phi(text)[0]


def _project_history(history: list[BaseMessage]) -> list[BaseMessage]:
    projected: list[BaseMessage] = []
    for message in history:
        message_type = _HISTORY_MESSAGE_TYPES.get(message.type)
        if message_type is None:
            continue
        content = message.content
        if not isinstance(content, str):
            raise PrivacyScanError("PRIVACY_UNSUPPORTED_MESSAGE_CONTENT")
        projected.append(message_type(content=_scrub_router_text(content)))
    return projected


def _clinical_direct_content(text: str) -> bool:
    return bool(
        NUMERIC_DOSE.search(text)
        or _PERCENT_QUANTITY.search(text)
        or _DIRECT_ADVICE_PREFIX.match(text)
    )


def _query_or_respond_decision(response: AIMessage) -> QueryOrRespondDecision:
    tool_call_count = len(response.tool_calls) + len(response.invalid_tool_calls)
    if response.invalid_tool_calls:
        return QueryOrRespondDecision(
            "retrieve", "", None, "malformed_tool", tool_call_count
        )
    if len(response.tool_calls) > 1:
        return QueryOrRespondDecision(
            "retrieve", "", None, "multiple_tools", tool_call_count
        )
    if len(response.tool_calls) == 1:
        tool_call = response.tool_calls[0]
        if tool_call["name"] != "retrieve_monographs":
            return QueryOrRespondDecision(
                "retrieve", "", None, "unknown_tool", tool_call_count
            )
        try:
            arguments = RetrieveMonographsArguments.model_validate(tool_call["args"])
            query = _scrub_router_text(arguments.query)
        except ValidationError:
            return QueryOrRespondDecision(
                "retrieve", "", None, "malformed_tool", tool_call_count
            )
        except PrivacyScanError:
            return QueryOrRespondDecision(
                "retrieve", "", None, "privacy_error", tool_call_count
            )
        return QueryOrRespondDecision("retrieve", "", query, None, tool_call_count)
    if not isinstance(response.content, str):
        return QueryOrRespondDecision(None, "", None, "malformed_content", 0)
    raw_content = response.content.strip()
    if not raw_content:
        return QueryOrRespondDecision(None, "", None, "empty_response", 0)
    if len(raw_content.encode("utf-8")) > MAX_INPUT_BYTES:
        return QueryOrRespondDecision("direct", "", None, "privacy_error", 0)
    if _clinical_direct_content(raw_content):
        return QueryOrRespondDecision("direct", "", None, "clinical_direct_content", 0)
    try:
        direct_content = _scrub_router_text(raw_content).strip()
    except PrivacyScanError:
        return QueryOrRespondDecision("direct", "", None, "privacy_error", 0)
    return QueryOrRespondDecision("direct", direct_content, None, None, 0)


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
                _project_history(history),
                strategy="last",
                token_counter=count_tokens_approximately,
                max_tokens=self.settings.history_max_tokens,
                start_on="human",
                include_system=False,
            )
            messages = [
                *self._messages("query_or_respond", {}),
                *capped_history,
                HumanMessage(content=_scrub_router_text(current_query)),
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
        return _query_or_respond_decision(response)
