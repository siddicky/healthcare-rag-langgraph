from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypedDict, cast
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallLimitMiddleware,
    after_agent,
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
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from copilotkit import CopilotKitMiddleware
from typing_extensions import override

from healthcare_rag.graph.resources import get as get_resources

from .compose_ui import compose_ui, validate_composition
from .memory import (
    authenticated_display_name,
    authenticated_user_id,
    remember_fact,
)
from .reminders import cancel_reminder, create_reminder, edit_reminder
from .safe_message import to_safe_message
from .state import CoachState
from .tools.change_schedule import change_schedule
from .tools.copy_to_clipboard import copy_to_clipboard
from .tools.log_injection import log_injection
from .tools.log_metric import log_metric
from .tools.medical_lookup import medical_lookup
from .tools.view_schedule import view_schedule

SAFE_FALLBACK: Final = "I couldn't format that reply safely — here's the plain version."
BASE_PROMPT: Final = """You are Nymble's warm, genuinely helpful member coach. Be conversationally useful for everyday requests: small talk, planning, lists, motivation, habits, and using the calendar/tracking tools. Use tools for member data and never invent facts.

Medical questions — anything about a medication's dose, side effects, interactions, warnings, or what the monograph says — must go through the medical_lookup tool. When a turn is a medical question, call medical_lookup alone in that step and stop; do not call any other tool alongside it. Never answer a medical question from your own knowledge, and never restate, summarize, or soften what medical_lookup returns — its result is shown to the member exactly as returned. Never give diagnoses or personal dosing advice.

Clipboard: copy_to_clipboard is a client-side headless tool that copies text to the member's clipboard — call it only when the member asks to copy something or when offering a short snippet they'd want to keep. It takes {text: string} and runs in the browser (no server log of the text).

Catalog composition rules: call compose_ui only with catalog components. Every fact-bearing prop must be a {__ref:{turn_scope_id,block_id,pointer}} object resolving into a DATA envelope from this turn. Static labels must use approved fixed copy. Actions must use a registered dispatch id. Unknown components, props, copy, refs, or actions are forbidden. Fixed interrupt cards are not composable."""


@dataclass(frozen=True, slots=True)
class AgentContext:
    user_id: str
    thread_id: str
    human_msg_id: str
    display_name: str | None = None


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
    medical_calls = [call for call in output.tool_calls if call["name"] == "medical_lookup"]
    if medical_calls:
        # Never let assistant prose alongside the tool call reach the member: the
        # medical answer contract is tool-only, so any preamble the model wrote in
        # this same step (e.g. "let me check that...") is dropped, not rendered.
        blanked_content: str | list[str | dict[str, object]] = (
            [] if isinstance(output.content, list) else ""
        )
        output = AIMessage(
            id=output.id,
            name=output.name,
            content=blanked_content,
            tool_calls=medical_calls,
        )
        projected = [
            output if isinstance(message, AIMessage) else message
            for message in projected
        ]
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


class RouteBCopilotKitMiddleware(CopilotKitMiddleware):
    """Pass-through CopilotKit emitter that never serializes AgentContext.

    copilotkit 0.1.95's app-context note JSON-serializes ``runtime.context``
    whenever no CopilotKit payload is present, which crashes on our frozen
    dataclass context and would otherwise leak internal ids into the prompt.
    With no payload (the only state this agent is ever served in until the
    runtime proxy lands), it emits nothing — byte-identical to no middleware.
    """

    @override
    def _build_app_context_note(
        self, state: dict[str, Any], runtime_context: Any = None
    ) -> str | None:
        if not self._get_copilotkit_context(state, runtime_context):
            return None
        return super()._build_app_context_note(state, runtime_context)


@dynamic_prompt
async def memory_segment(request: ModelRequest[AgentContext | None]) -> str:
    """Append identity and auth-scoped memory to the fixed Route-B system contract."""
    runtime = request.runtime
    context = runtime.context
    if context is None:
        raise ValueError("AgentContext is required")
    segments: list[str] = []
    if context.display_name:
        segments.append(
            "## Member context\n"
            f"- The member's display name is {context.display_name}. "
            "Address them by name when natural, not every turn."
        )
    if runtime.store is not None:
        profile = await runtime.store.asearch(
            ("users", context.user_id, "profile"), limit=100
        )
        episodic = await runtime.store.asearch(
            ("users", context.user_id, "episodic"), limit=100
        )
        facts = [item.value.get("fact") for item in (*profile, *episodic)]
        clean = [fact for fact in facts if isinstance(fact, str)]
        if clean:
            segments.append(
                "## Saved user memories\n" + "\n".join(f"- {fact}" for fact in clean)
            )
    if not segments:
        return BASE_PROMPT
    return BASE_PROMPT + "\n\n" + "\n\n".join(segments)


@after_agent
async def relay_medical_answer(
    state: AgentState[None], runtime: Runtime[AgentContext | None]
) -> dict[str, list[AnyMessage]] | None:
    """Turn a terminal medical_lookup ToolMessage into an AIMessage the UI renders."""
    del runtime
    messages = state["messages"]
    last = messages[-1] if messages else None
    if not (isinstance(last, ToolMessage) and last.name == "medical_lookup"):
        return None
    return {"messages": [AIMessage(id=str(uuid4()), content=str(last.content))]}


def build_route_b_agent(
    model: BaseChatModel, store: BaseStore
) -> Runnable[RouteBInput, RouteBOutput]:
    """Build the checkpointer-free Route-B agent with its fixed tool catalog."""
    middleware = cast(
        "Sequence[AgentMiddleware[AgentState[None], AgentContext | None, None]]",
        (
            # Order-pinned: the CopilotKit middleware must stay FIRST
            # (outermost) so everything it emits to the CopilotKit runtime
            # observes the final projections — safe_model_response blanking
            # medical preambles and rewriting invalid compositions,
            # memory_segment prompt assembly, and relay_medical_answer's
            # terminal relay. Moving it inward would expose pre-projection
            # messages; it is otherwise a pass-through emitter (default
            # constructor, no state exposure).
            RouteBCopilotKitMiddleware(),
            safe_model_response,
            memory_segment,
            ToolCallLimitMiddleware[None, AgentContext | None](
                tool_name="change_schedule", run_limit=1, exit_behavior="continue"
            ),
            relay_medical_answer,
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
            medical_lookup,
            copy_to_clipboard,
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
    display_name = authenticated_display_name(config)
    model = get_resources().gateway.chat_model("default", temperature=0.0)
    result = await build_route_b_agent(model, store).ainvoke(
        {"messages": messages},
        child_config,
        context=AgentContext(
            user_id=user_id,
            thread_id=str(configurable.get("thread_id", "")),
            human_msg_id=human.id,
            display_name=display_name,
        ),
    )
    return {
        "messages": [to_safe_message(message) for message in result["messages"]],
        "follow_ups": _medical_follow_ups(result["messages"]),
    }


def _medical_follow_ups(messages: list[AnyMessage]) -> list[str]:
    lookup = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, ToolMessage) and message.name == "medical_lookup"
        ),
        None,
    )
    if lookup is None or not isinstance(lookup.artifact, dict):
        return []
    follow_ups = lookup.artifact.get("follow_ups")
    return follow_ups if isinstance(follow_ups, list) else []


__all__ = [
    "BASE_PROMPT",
    "SAFE_FALLBACK",
    "AgentContext",
    "build_route_b_agent",
    "coach_agent",
    "memory_segment",
    "relay_medical_answer",
    "safe_model_response",
]
