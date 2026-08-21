from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, cast
from typing import override as typing_override

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from healthcare_rag.agent import build as coach_build
from healthcare_rag.agent import gate
from healthcare_rag.agent.ns_sweep import (
    CheckpointRecord,
    checkpoint_records,
    diff_records,
    lineage_leaves,
)
from healthcare_rag.agent.state import CoachInput, CronWakePayload
from healthcare_rag.graph.resources import Resources, override
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.graph.state import load_results

from .agent_chunks import CheckedChunk, ChunkCatalog
from .coach_fixtures import seed_document_fixture, seed_reminder_fixture
from .offline_agent_fakes import (
    OfflineGateway,
    offline_coach_agent,
    offline_search,
    outer_classifier,
)

DEFAULT_USER: Final = "offline-member"
DocumentDecision: TypeAlias = dict[str, bool | list[dict[str, str]]]


@dataclass(frozen=True, slots=True)
class CoachTurnResult:
    route: str
    answer: str
    contexts: tuple[CheckedChunk, ...]
    route_a_leaf: CheckpointRecord | None
    boundary_hit: bool


class RouteALineageError(RuntimeError):
    def __init__(self, thread_id: str, leaf_count: int) -> None:
        self.thread_id: str = thread_id
        self.leaf_count: int = leaf_count
        super().__init__(str(self))

    @typing_override
    def __str__(self) -> str:
        return (
            f"Route A turn {self.thread_id!r} produced {self.leaf_count} child leaves"
        )


class CoachEngine:
    def __init__(
        self,
        saver: InMemorySaver,
        store: InMemoryStore,
        catalog: ChunkCatalog,
    ) -> None:
        self._saver: InMemorySaver = saver
        self.store: InMemoryStore = store
        self._catalog: ChunkCatalog = catalog
        original = coach_build.coach_agent
        coach_build.coach_agent = offline_coach_agent
        try:
            self._graph = coach_build.build_coach_graph().compile(
                checkpointer=saver,
                store=store,
                name="offline_coach_eval",
            )
        finally:
            coach_build.coach_agent = original

    async def seed_document(self, attachment_id: str, *, thread_id: str) -> None:
        await seed_document_fixture(self.store, DEFAULT_USER, attachment_id, thread_id)

    async def seed_reminder(self, *, thread_id: str) -> CronWakePayload:
        return await seed_reminder_fixture(self.store, DEFAULT_USER, thread_id)

    async def run_wake(self, wake: CronWakePayload) -> CoachTurnResult:
        return await self._invoke(
            {"question": None, "cron_wake": wake},
            thread_id=wake["thread_id"],
            authenticated=False,
        )

    async def resume_document(
        self, *, thread_id: str, decision: DocumentDecision
    ) -> str:
        config = self._member_config(thread_id)
        output = await self._graph.ainvoke(Command(resume=decision), config)
        return next(
            (
                str(message.content)
                for message in reversed(output.get("messages", []))
                if isinstance(message, AIMessage)
            ),
            "",
        )

    async def profile_facts(self) -> tuple[str, ...]:
        items = await self.store.asearch(("users", DEFAULT_USER, "profile"), limit=100)
        return tuple(
            str(item.value["fact"])
            for item in items
            if isinstance(item.value.get("fact"), str)
        )

    async def run_turn(
        self,
        question: str,
        *,
        thread_id: str,
        attachment_id: str | None = None,
    ) -> CoachTurnResult:
        request: CoachInput = {"question": question, "attachment_id": attachment_id}
        return await self._invoke(request, thread_id=thread_id, authenticated=True)

    async def _invoke(
        self,
        request: CoachInput,
        *,
        thread_id: str,
        authenticated: bool,
    ) -> CoachTurnResult:
        config: RunnableConfig = (
            self._member_config(thread_id)
            if authenticated
            else cast(
                RunnableConfig,
                cast(object, {"configurable": {"thread_id": thread_id}}),
            )
        )
        before = await checkpoint_records(self._saver, thread_id)
        output = await self._graph.ainvoke(request, config)
        snapshot = await self._graph.aget_state(config)
        route = str(snapshot.values.get("route", ""))
        added = diff_records(before, await checkpoint_records(self._saver, thread_id))
        route_a_leaf = await self._route_a_leaf(route, added, thread_id)
        leaf_state = await self._leaf_state(route_a_leaf)
        contexts = self._contexts(leaf_state)
        safety_outcome = leaf_state.get("safety", {})
        boundary_hit = (
            isinstance(safety_outcome, dict)
            and safety_outcome.get("boundary_hit") is True
        )
        messages = output.get("messages") or snapshot.values.get("messages", [])
        answer = next(
            (
                str(message.content)
                for message in reversed(messages)
                if isinstance(message, AIMessage)
            ),
            "",
        )
        return CoachTurnResult(route, answer, contexts, route_a_leaf, boundary_hit)

    @staticmethod
    def _member_config(thread_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": thread_id,
                "langgraph_auth_user": {
                    "identity": DEFAULT_USER,
                    "role": "member",
                },
            }
        }

    async def _route_a_leaf(
        self,
        route: str,
        records: tuple[CheckpointRecord, ...],
        thread_id: str,
    ) -> CheckpointRecord | None:
        if route != "rag_relay":
            return None
        leaves = tuple(
            record
            for group in lineage_leaves(records).values()
            for record in group
            if record.checkpoint_ns.startswith("rag_relay")
        )
        if len(leaves) != 1:
            raise RouteALineageError(thread_id=thread_id, leaf_count=len(leaves))
        return leaves[0]

    async def _leaf_state(self, leaf: CheckpointRecord | None) -> dict[str, object]:
        if leaf is None:
            return {}
        config: RunnableConfig = {
            "configurable": {
                "thread_id": leaf.thread_id,
                "checkpoint_ns": leaf.checkpoint_ns,
                "checkpoint_id": leaf.checkpoint_id,
            }
        }
        item = await self._saver.aget_tuple(config)
        if item is None:
            raise RouteALineageError(thread_id=leaf.thread_id, leaf_count=0)
        return dict(item.checkpoint["channel_values"])

    def _contexts(self, leaf_state: dict[str, object]) -> tuple[CheckedChunk, ...]:
        merged = leaf_state.get("merged")
        if not isinstance(merged, dict):
            return ()
        mapped: list[CheckedChunk] = []
        for result in load_results(merged).results:
            for document in result.docs:
                mapped.append(
                    self._catalog.resolve(
                        document.source_name,
                        cast(dict[str, int | str], document.metadata),
                    )
                )
        return tuple(mapped)


def build_offline_coach_engine() -> CoachEngine:
    settings = GraphSettings(
        safety_gate_enabled=True,
        max_subqueries=3,
        decompose_only_complex=True,
        disabled_stages=frozenset(),
        llm_model="offline",
        validator_model="offline",
        reasoning_effort="none",
        history_max_tokens=4000,
        structured_strict=False,
        checkpoint_uri="",
        openai_api_key="offline",
        retriever="pageindex",
    )
    resources = Resources(settings)
    resources._gateway = OfflineGateway()  # pyright: ignore[reportPrivateUsage]
    resources.hybrid_search = offline_search
    override(resources)
    gate.GATEWAY = outer_classifier
    return CoachEngine(
        saver=InMemorySaver(),
        store=InMemoryStore(),
        catalog=ChunkCatalog.load(Path("data")),
    )


__all__ = ["CoachEngine", "CoachTurnResult", "build_offline_coach_engine"]
