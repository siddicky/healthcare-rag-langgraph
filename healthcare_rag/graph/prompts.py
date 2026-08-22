from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

import yaml
from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, TypeAdapter
from typing_extensions import TypedDict

from healthcare_rag.models.answers import CitedAnswerResult, RelevantHistoryContext
from healthcare_rag.models.misc import FollowUpQuestions
from healthcare_rag.models.queries import (
    ClarifiedQuery,
    DecomposedQuery,
    RetrievalEvaluation,
)
from healthcare_rag.models.retrieval import PageIndexSelection
from healthcare_rag.models.safety import SafetyAssessment


class PromptMessageData(TypedDict):
    role: Literal["system", "user"]
    content: str


STAGE_FILES: Final = {
    "safety_gate": "safety_gate",
    "clarify_query": "clarify_query",
    "decompose_query": "decompose_query",
    "extract_conversation_context": "context_extraction",
    "evaluate_retrieval": "retrieval_evaluation",
    "generate_answer": "answer_generation",
    "validate_answer": "answer_structuring",
    "generate_follow_ups": "follow_up_questions",
    "pageindex_select": "pageindex_select",
    "query_or_respond": "query_or_respond",
}

RESPONSE_MODELS: Final[dict[str, type[BaseModel]]] = {
    "safety_gate": SafetyAssessment,
    "clarify_query": ClarifiedQuery,
    "decompose_query": DecomposedQuery,
    "extract_conversation_context": RelevantHistoryContext,
    "evaluate_retrieval": RetrievalEvaluation,
    "validate_answer": CitedAnswerResult,
    "generate_follow_ups": FollowUpQuestions,
    "pageindex_select": PageIndexSelection,
}

_PROMPT_MESSAGES_ADAPTER: Final = TypeAdapter(list[PromptMessageData])
_MESSAGE_TYPES: Final = {
    "system": SystemMessage,
    "user": HumanMessage,
}


class PromptRegistry:
    def __init__(self) -> None:
        self._environment: Environment = Environment(
            loader=FileSystemLoader(Path(__file__).parents[1] / "prompts"),
            autoescape=select_autoescape(enabled_extensions=("j2",)),
        )
        self._templates: dict[str, Template] = {}

    def format_messages(self, stage: str, **vars: str) -> list[BaseMessage]:
        stem = STAGE_FILES[stage]
        if stem not in self._templates:
            self._templates[stem] = self._environment.get_template(f"{stem}.yaml.j2")

        rendered = self._templates[stem].render(**vars)
        prompt_messages = _PROMPT_MESSAGES_ADAPTER.validate_python(
            yaml.safe_load(rendered)
        )
        return [
            _MESSAGE_TYPES[prompt_message["role"]](content=prompt_message["content"])
            for prompt_message in prompt_messages
        ]


_registry: PromptRegistry | None = None


def get_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry
