from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from healthcare_rag.processors.direct_output_policy import GeneratedOutputPolicyDecision
from healthcare_rag.processors.privacy import PrivacySanitizer, PrivacyScanError

RouterAction = Literal["direct", "retrieve"]
GeneratedOutputEvaluator = Callable[
    [str, PrivacySanitizer], GeneratedOutputPolicyDecision
]
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


def scrub_router_text(text: str, privacy: PrivacySanitizer) -> str:
    return privacy.scan(text).text


def project_history(
    history: list[BaseMessage], privacy: PrivacySanitizer
) -> list[BaseMessage]:
    projected: list[BaseMessage] = []
    for message in history:
        message_type = _HISTORY_MESSAGE_TYPES.get(message.type)
        if message_type is None:
            continue
        content = message.content
        if not isinstance(content, str):
            raise PrivacyScanError("PRIVACY_UNSUPPORTED_MESSAGE_CONTENT")
        projected.append(message_type(content=scrub_router_text(content, privacy)))
    return projected


def query_or_respond_decision(
    response: AIMessage,
    privacy: PrivacySanitizer,
    evaluate_generated_output: GeneratedOutputEvaluator,
) -> QueryOrRespondDecision:
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
            query = scrub_router_text(arguments.query, privacy)
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
    try:
        policy = evaluate_generated_output(raw_content, privacy)
    except PrivacyScanError:
        return QueryOrRespondDecision("direct", "", None, "privacy_error", 0)
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        return QueryOrRespondDecision("direct", "", None, "direct_policy_error", 0)
    return QueryOrRespondDecision(
        "direct", policy.content, None, policy.denial_reason, 0
    )
