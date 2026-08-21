"""LangGraph state and JSON serialization boundaries.

Domain state is JSON-native. The sole exception is ``messages``, whose
``BaseMessage`` objects are serialized by LangGraph's ``add_messages`` channel.
Message timestamps are ISO-8601 strings in ``additional_kwargs["ts"]``.
"""

import operator
from typing import Annotated, Any, Literal, TypeAlias, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.graph.message import add_messages

from healthcare_rag.models.retrieval import QueryResultList

JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


class RAGState(TypedDict, total=False):
    question: Annotated[str, UntrackedValue(str)]
    scrubbed_question: str
    working_query: str
    user_id: str
    messages: Annotated[list[AnyMessage], add_messages]
    history_context: str
    processed_history: list[dict[str, JSONValue]]
    safety: dict[str, JSONValue] | None
    safety_kind: str
    safety_response: str
    safety_notices: list[str]
    refusal_boundaries: list[dict[str, JSONValue]]
    summary: dict[str, JSONValue] | None
    clarified: str | None
    decomposed: bool
    sub_queries: list[str]
    retrievals: Annotated[list[dict[str, JSONValue]], operator.add]
    merged: list[dict[str, JSONValue]] | None
    evaluation: dict[str, JSONValue] | None
    gap_round: int
    gap_pending: bool
    gap_filled: bool
    generation: dict[str, JSONValue] | None
    structured: dict[str, JSONValue] | None
    validated: str | None
    answer: str | None
    follow_ups: list[str]
    route: Annotated[list[str], operator.add]
    branch_events: Annotated[list[dict[str, JSONValue]], operator.add]
    selected_branch_type: str | None
    selected_branch_query: str | None
    error: str | None


class RetrieveInput(TypedDict):
    query: str
    index: int
    kind: Literal["initial", "clarified", "decomposed", "gap_fill"]
    phase: int
    branch: str


class GraphInput(TypedDict, total=False):
    question: str
    user_id: str


class GraphOutput(TypedDict, total=False):
    answer: str | None
    follow_ups: list[str]
    safety: dict[str, JSONValue] | None
    selected_branch_type: str | None
    selected_branch_query: str | None
    error: str | None


def dump_results(results: QueryResultList) -> dict[str, Any]:
    """Serialize retrieval models at the graph-state boundary."""
    return results.model_dump(mode="json")


def load_results(data: dict[str, Any]) -> QueryResultList:
    """Restore retrieval models after reading graph state."""
    return QueryResultList.model_validate(data)
