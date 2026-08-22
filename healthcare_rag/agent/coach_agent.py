from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final, TypedDict, cast
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.messages.tool import ToolCall
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.base import Runnable
from langgraph.store.base import BaseStore
from typing_extensions import override

from healthcare_rag.graph.resources import get as get_resources

from .compose_ui import compose_ui, validate_composition
from .memory import authenticated_user_id, remember_fact
from .reminders import cancel_reminder, create_reminder, edit_reminder
from .safe_message import to_safe_message
from .state import CoachState
from .tools.change_schedule import change_schedule
from .tools.log_injection import log_injection
from .tools.log_metric import log_metric
from .tools.view_schedule import view_schedule

SAFE_FALLBACK: Final = "I couldn't format that reply safely — here's the plain version."
BASE_PROMPT: Final = """You are Nymble's calm, concise behavior coach. Never provide medical advice, dosing decisions, diagnoses, or monograph claims; route medical questions elsewhere. Use tools for member data and never invent facts.

Catalog composition rules: call compose_ui only with catalog components. Every fact-bearing prop must be a {__ref:{turn_scope_id,block_id,pointer}} object resolving into a DATA envelope from this turn. Static labels must use approved fixed copy. Actions must use a registered dispatch id. Unknown components, props, copy, refs, or actions are forbidden. Fixed interrupt cards are not composable."""


@dataclass(frozen=True, slots=True)
class AgentContext:
    user_id: str
    thread_id: str
    human_msg_id: str


class RouteBInput(TypedDict):
    messages: list[AnyMessage]


class RouteBOutput(TypedDict):
    messages: list[AnyMessage]


def _scope(request: ModelRequest[AgentContext | None]) -> str:
    context = request.runtime.context
    if context is None:
        raise ValueError("AgentContext is required")
    return hashlib.sha256(
        f"{context.thread_id}|{context.human_msg_id}".encode()
    ).hexdigest()


def _envelope_contents(request: ModelRequest[AgentContext | None]) -> list[str]:
    return [
        message.content
        for message in request.messages
        if isinstance(message, ToolMessage) and isinstance(message.content, str)
    ]


def _invalid_cycles(request: ModelRequest[AgentContext | None]) -> int:
    return sum(
        isinstance(message, ToolMessage)
        and message.name == "compose_ui"
        and message.status == "error"
        for message in request.messages
    )


async def _safe_model_response(
    request: ModelRequest[AgentContext | None],
    handler: Callable[
        [ModelRequest[AgentContext | None]], Awaitable[ModelResponse[None]]
    ],
) -> ModelResponse[None]:
    """Project model output and reject invalid compositions before state update."""
    response = await handler(request)
    projected: list[BaseMessage] = []
    projected.extend(to_safe_message(message) for message in response.result)
    output = next(
        (message for message in projected if isinstance(message, AIMessage)), None
    )
    if output is None:
        return ModelResponse(
            result=projected, structured_response=response.structured_response
        )
    invalid = [
        call
        for call in output.tool_calls
        if call["name"] == "compose_ui"
        and not validate_composition(
            call["args"], _envelope_contents(request), _scope(request)
        ).valid
    ]
    if not invalid:
        return ModelResponse(
            result=projected, structured_response=response.structured_response
        )
    if _invalid_cycles(request) >= 1:
        terminal = AIMessage(
            id=str(uuid4()),
            name=output.name,
            content=SAFE_FALLBACK,
            tool_calls=[],
        )
        return ModelResponse(
            result=[terminal], structured_response=response.structured_response
        )
    invalid_ids = {call["id"] for call in invalid}
    rewritten: list[ToolCall] = []
    for call in output.tool_calls:
        rewritten.append(
            ToolCall(name=call["name"], id=call["id"], args={"tree": []})
            if call["id"] in invalid_ids
            else call
        )
    safe_output = AIMessage(
        id=output.id,
        name=output.name,
        content=output.content,
        tool_calls=rewritten,
    )
    errors = [
        ToolMessage(
            content="Composition rejected by the catalog validator. Correct the call or reply in plain text.",
            tool_call_id=call["id"],
            name="compose_ui",
            status="error",
        )
        for call in invalid
    ]
    return ModelResponse(
        result=[safe_output, *errors], structured_response=response.structured_response
    )


class SafeModelResponseMiddleware(
    AgentMiddleware[AgentState[None], AgentContext | None, None]
):
    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[AgentContext | None],
        handler: Callable[
            [ModelRequest[AgentContext | None]], Awaitable[ModelResponse[None]]
        ],
    ) -> ModelResponse[None]:
        return await _safe_model_response(request, handler)


safe_model_response = SafeModelResponseMiddleware()


@dynamic_prompt
async def memory_segment(request: ModelRequest[AgentContext | None]) -> str:
    """Append auth-scoped memory to the fixed Route-B system contract."""
    runtime = request.runtime
    context = runtime.context
    if context is None:
        raise ValueError("AgentContext is required")
    if runtime.store is None:
        return BASE_PROMPT
    profile = await runtime.store.asearch(
        ("users", context.user_id, "profile"), limit=100
    )
    episodic = await runtime.store.asearch(
        ("users", context.user_id, "episodic"), limit=100
    )
    facts = [item.value.get("fact") for item in (*profile, *episodic)]
    clean = [fact for fact in facts if isinstance(fact, str)]
    segment = (
        "## Saved user memories\n" + "\n".join(f"- {fact}" for fact in clean)
        if clean
        else ""
    )
    return f"{BASE_PROMPT}\n\n{segment}" if segment else BASE_PROMPT


def build_route_b_agent(
    model: BaseChatModel, store: BaseStore
) -> Runnable[RouteBInput, RouteBOutput]:
    """Build the checkpointer-free Route-B agent with its fixed tool catalog."""
    middleware = cast(
        "Sequence[AgentMiddleware[AgentState[None], AgentContext | None, None]]",
        (
            safe_model_response,
            memory_segment,
            ToolCallLimitMiddleware[None, AgentContext | None](
                tool_name="change_schedule", run_limit=1, exit_behavior="continue"
            ),
        ),
    )
    agent = create_agent(
        model=model,
        tools=[
            remember_fact,
            log_metric,
            log_injection,
            view_schedule,
            change_schedule,
            create_reminder,
            edit_reminder,
            cancel_reminder,
            compose_ui,
        ],
        system_prompt=BASE_PROMPT,
        middleware=middleware,
        store=store,
        context_schema=AgentContext,
        name="route_b_agent",
    )
    return cast("Runnable[RouteBInput, RouteBOutput]", agent)


async def coach_agent(
    state: CoachState,
    config: RunnableConfig,
    *,
    store: BaseStore,
) -> CoachState:
    """Invoke Route B with only projected messages and a server-derived turn id."""
    messages = [to_safe_message(message) for message in state.get("messages", [])]
    human = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, HumanMessage)
        ),
        None,
    )
    if human is None:
        return {"messages": [], "follow_ups": []}
    if human.id is None:
        human.id = str(uuid4())
    configurable = dict(config.get("configurable", {}))
    configurable["coach_human_msg_id"] = human.id
    child_config: RunnableConfig = {**config, "configurable": configurable}
    user_id = authenticated_user_id(config)
    model = get_resources().gateway.chat_model("default", temperature=0.0)
    result = await build_route_b_agent(model, store).ainvoke(
        {"messages": messages},
        child_config,
        context=AgentContext(
            user_id=user_id,
            thread_id=str(configurable.get("thread_id", "")),
            human_msg_id=human.id,
        ),
    )
    return {
        "messages": [to_safe_message(message) for message in result["messages"]],
        "follow_ups": [],
    }


__all__ = [
    "BASE_PROMPT",
    "SAFE_FALLBACK",
    "AgentContext",
    "build_route_b_agent",
    "coach_agent",
    "memory_segment",
    "safe_model_response",
]
