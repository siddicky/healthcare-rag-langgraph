from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from langchain_core.messages import ToolCall
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from pydantic import BaseModel

from healthcare_rag.agent.state import CoachState
from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.models.answers import (
    Citation,
    CitedAnswerResult,
    StatementWithCitations,
)
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.models.safety import SafetyAssessment

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class OfflineGateway(LangChainLLMGateway):
    calls: list[str] = field(default_factory=list)

    async def astructured(
        self,
        stage: str,
        model_type: type[ModelT],
        *,
        temperature: float | None = None,
        default: ModelT | None = None,
        **variables: str,
    ) -> ModelT | None:
        del temperature
        self.calls.append(stage)
        if stage == "safety_gate":
            query = variables.get("user_query", "").lower()
            personal_request = any(
                cue in query
                for cue in (
                    "should i double",
                    "can i go up",
                    "tell me what dose i should",
                    "what would you do in my position",
                    "just confirm i can double",
                    "should i be taking more",
                )
            )
            category = (
                "personal_medical_advice"
                if "personally take" in query or personal_request
                else "in_scope_informational"
            )
            drug = (
                "metformin"
                if "metformin" in query
                else "lipitor"
                if "lipitor" in query
                else "none"
            )
            return model_type.model_validate(
                SafetyAssessment(
                    category=category,
                    contains_phi=False,
                    phi_spans=[],
                    drug_mentioned=drug,
                    rationale="offline deterministic fixture",
                ).model_dump()
            )
        if stage == "validate_answer":
            answer = CitedAnswerResult(
                statements=[
                    StatementWithCitations(
                        text="PRODUCT MONOGRAPH information.",
                        citations=[
                            Citation(
                                doc_id="doc_1",
                                source_name="Lipitor",
                                quote="PRODUCT MONOGRAPH",
                            )
                        ],
                        linebreaks="",
                    )
                ]
            )
            return model_type.model_validate(answer.model_dump())
        return default

    async def acomplete(
        self,
        stage: str,
        *,
        temperature: float | None = None,
        default: str = "",
        **variables: str,
    ) -> str:
        del temperature, variables
        self.calls.append(stage)
        return (
            "PRODUCT MONOGRAPH information [doc_1]."
            if stage == "generate_answer"
            else default
        )

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        self.calls.append("route_tools")
        collection = "metformin" if "metformin" in query.lower() else "lipitor"
        return [
            {
                "name": f"query_{collection}",
                "args": {"query": query},
                "id": f"offline-{collection}",
                "type": "tool_call",
            }
        ]


async def offline_search(
    _client: None, collection_name: str, query: str
) -> QueryResultList:
    source = "Metformin" if collection_name == "Metformin" else "Lipitor"
    content = (
        "Pr TEVA-METFORMIN product monograph."
        if source == "Metformin"
        else "PRODUCT MONOGRAPH information."
    )
    return QueryResultList(
        results=[
            QueryResult(
                source=source,
                query=query,
                docs=[
                    QueryDocument(
                        content=content,
                        score=1.0,
                        doc_id="doc_1",
                        metadata={"id_": 1},
                        source_name=source,
                        page_numbers=[1],
                    )
                ],
            )
        ]
    )


async def outer_classifier(**_variables: str) -> SafetyAssessment:
    return SafetyAssessment(
        category="in_scope_informational",
        contains_phi=False,
        phi_spans=[],
        drug_mentioned="lipitor",
        rationale="offline outer route fixture",
    )


async def offline_coach_agent(
    state: CoachState,
    config: RunnableConfig,
    *,
    store: BaseStore,
) -> CoachState:
    from langchain_core.messages import AIMessage

    del state, config, store
    return {"messages": [AIMessage(content="Offline coach reply.")], "follow_ups": []}


__all__ = [
    "OfflineGateway",
    "offline_coach_agent",
    "offline_search",
    "outer_classifier",
]
