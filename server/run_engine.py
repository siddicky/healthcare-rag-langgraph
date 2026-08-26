from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from typing import ClassVar, Final, Literal, Protocol, TypeAlias, runtime_checkable
from uuid import uuid4

import anyio
from anyio.abc import TaskGroup
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

logger = logging.getLogger("MedicalRAG")

from server.registries import PostgresRunRegistry
from server.storage import Storage

JSONValue: TypeAlias = JsonValue

QUEUE_LIMIT: Final = 100
EVENT_BUFFER_LIMIT: Final = 100
PERSISTED_PAYLOAD_REDACTION: Final = "[redacted]"
NONTERMINAL_RUN_STATUSES: Final[frozenset[str]] = frozenset({"pending", "running"})

# The authenticated principal is server-controlled state. It has exactly one
# writer (`RunEngine._graph_config`, from the request's authenticated user) and
# a client-supplied value is never trusted anywhere.
AUTH_USER_KEY: Final = "langgraph_auth_user"


async def reconcile_interrupted_runs(storage: Storage) -> int:
    """Interrupt Postgres runs that cannot survive a process restart."""
    if not isinstance(storage.runs, PostgresRunRegistry):
        return 0
    reconciled = 0
    for record in await storage.runs.all():
        run_id = record.get("run_id")
        if isinstance(run_id, str) and record.get("status") in NONTERMINAL_RUN_STATUSES:
            await storage.runs.set_status(run_id, "interrupted")
            reconciled += 1
    return reconciled


def _sanitized_config(config: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Strip any client-supplied principal from a run config."""
    configurable = config.get("configurable")
    if not isinstance(configurable, dict) or AUTH_USER_KEY not in configurable:
        return config
    scrubbed = {k: v for k, v in configurable.items() if k != AUTH_USER_KEY}
    return {**config, "configurable": scrubbed}


def _to_jsonable(obj: object) -> JSONValue:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}  # type: ignore[arg-type]
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]  # type: ignore[arg-type]
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_jsonable(model_dump(mode="json"))
        except Exception:
            pass
    to_dict = getattr(obj, "dict", None)
    if callable(to_dict):
        try:
            return _to_jsonable(to_dict())
        except Exception:
            pass
    return str(obj)


class ResumeCommand(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    resume: JSONValue


class RunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    assistant_id: str
    input: dict[str, JSONValue] | None = None
    command: ResumeCommand | None = None
    config: dict[str, JSONValue] = Field(default_factory=dict)
    stream_mode: list[
        Literal[
            "updates", "custom", "values", "messages", "messages-tuple", "events"
        ]
    ] = Field(default_factory=lambda: ["updates", "custom"])  # type: ignore[assignment]
    # The locked CopilotKit adapter (@ag-ui/langgraph 0.0.42) always streams
    # with stream_subgraphs=True. Accepted for wire parity; not forwarded
    # into graph.astream, whose subgraph tuples ((ns, (mode, data))) would
    # change the engine's (mode, data) unpacking — member frames stay
    # root-scoped. langgraph itself ignores unknown extra modes like
    # "events" (verified against the pinned runtime), so the adapter's
    # default mode list passes through harmlessly.
    stream_subgraphs: bool = False
    stream_resumable: bool = False
    durability: Literal["exit"] = "exit"
    if_not_exists: Literal["reject"] = "reject"
    multitask_strategy: Literal["reject", "enqueue", "interrupt"] = "reject"

    @model_validator(mode="before")
    @classmethod
    def coerce_stream_mode(cls, data: object) -> object:
        if isinstance(data, dict) and "forkFrom" in data:
            data = dict(data)
            fork_from = data.pop("forkFrom")
            config = data.get("config", {})
            if not isinstance(config, dict):
                raise ValueError("config must be an object")
            configurable = config.get("configurable", {})
            if not isinstance(configurable, dict):
                raise ValueError("config.configurable must be an object")
            checkpoint_id = configurable.get("checkpoint_id")
            if checkpoint_id is not None and checkpoint_id != fork_from:
                raise ValueError(
                    "forkFrom conflicts with config.configurable.checkpoint_id"
                )
            configurable = {**configurable, "checkpoint_id": fork_from}
            data["config"] = {**config, "configurable": configurable}
        if isinstance(data, dict) and "stream_mode" in data:
            sm = data["stream_mode"]
            if isinstance(sm, str):
                data = dict(data)
                data["stream_mode"] = [sm]
                sm = data["stream_mode"]
            if isinstance(sm, list):
                coerced: list[str] = []
                changed = False
                for item in sm:
                    if item == "messages-tuple":
                        coerced.append("messages")
                        changed = True
                    else:
                        coerced.append(item)  # type: ignore[arg-type]
                if changed:
                    data = dict(data)
                    data["stream_mode"] = coerced
        return data

    @model_validator(mode="after")
    def exactly_one_payload(self) -> RunRequest:
        if (self.input is None) == (self.command is None):
            raise ValueError("exactly one of input or command is required")
        configurable = self.config.get("configurable")
        if isinstance(configurable, dict) and "checkpoint_id" in configurable:
            checkpoint_id = configurable["checkpoint_id"]
            if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
                raise ValueError("checkpoint_id must be a non-empty string")
        return self


class CancelRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    action: Literal["rollback", "interrupt"]
    wait: bool = False


class StateSnapshot(Protocol):
    values: Mapping[str, JSONValue]
    next: tuple[str, ...]


@runtime_checkable
class GraphRunner(Protocol):
    def astream(
        self,
        input: dict[str, JSONValue] | Command[str],
        config: dict[str, JSONValue],
        *,
        stream_mode: Sequence[str],
        durability: Literal["exit"],
    ) -> AsyncIterator[tuple[str, JSONValue]]: ...

    async def aget_state(self, config: dict[str, JSONValue]) -> StateSnapshot: ...

    async def aupdate_state(
        self, config: dict[str, JSONValue], values: Mapping[str, JSONValue]
    ) -> None: ...


@dataclass(slots=True)
class RunRuntime:
    """Mutable execution signals and output for one run."""

    request: RunRequest
    thread_id: str
    auth_user: dict[str, JSONValue] | None = None
    done: anyio.Event = field(default_factory=anyio.Event)
    changed: anyio.Event = field(default_factory=anyio.Event)
    events: list[tuple[str, JSONValue]] = field(default_factory=list)
    event_count: int = 0
    output: dict[str, JSONValue] = field(default_factory=dict)
    pre_values: dict[str, JSONValue] = field(default_factory=dict)
    scope: anyio.CancelScope | None = None
    rollback: bool = False

    def append_event(self, mode: str, data: JSONValue) -> None:
        self.events.append((mode, data))
        self.event_count += 1
        overflow = len(self.events) - EVENT_BUFFER_LIMIT
        if overflow > 0:
            del self.events[:overflow]


class RunConflict(Exception):
    pass


class QueueFull(Exception):
    pass


class RunMissing(Exception):
    pass


class CheckpointMissing(Exception):
    pass


class RunEngine:
    """Mutable queue coordinator for checkpointed graph runs."""

    def __init__(
        self, storage: Storage, graphs: Mapping[str, object], tasks: TaskGroup
    ) -> None:
        self.storage: Storage = storage
        self.graphs: Mapping[str, object] = graphs
        self.tasks: TaskGroup = tasks
        self.runtime: dict[str, RunRuntime] = {}
        self.queues: dict[str, deque[str]] = {}
        self.active: dict[str, str] = {}
        self.command_replays: dict[str, str] = {}
        self.stopping: bool = False

    async def shutdown(self) -> None:
        self.stopping = True
        for queue in self.queues.values():
            for run_id in queue:
                await self.storage.runs.set_status(run_id, "interrupted")
                self.runtime[run_id].done.set()
                self.runtime[run_id].changed.set()
            queue.clear()
        for run_id in self.active.values():
            scope = self.runtime[run_id].scope
            if scope is not None:
                scope.cancel()

    async def submit(
        self,
        thread_id: str,
        request: RunRequest,
        *,
        auth_user: dict[str, JSONValue] | None = None,
    ) -> dict[str, object]:
        configurable = request.config.get("configurable")
        if isinstance(configurable, dict) and "checkpoint_id" in configurable:
            checkpoint_config: RunnableConfig = {
                "configurable": {**configurable, "thread_id": thread_id},
            }
            if await self.storage.saver.aget_tuple(checkpoint_config) is None:
                raise CheckpointMissing
        replay_key = self._replay_key(thread_id, request)
        if replay_key is not None and replay_key in self.command_replays:
            replay = await self.storage.runs.get(self.command_replays[replay_key])
            if replay is None:
                raise RunMissing
            return replay
        active_id = self.active.get(thread_id)
        if active_id is not None and request.multitask_strategy == "reject":
            raise RunConflict
        if active_id is not None and request.multitask_strategy == "interrupt":
            await self.cancel(
                thread_id, active_id, CancelRequest(action="interrupt", wait=True)
            )
        # Queue bound is server-wide: it must hold regardless of whether THIS
        # thread already has an active run (an idle thread must not bypass it).
        if self.pending_count >= QUEUE_LIMIT:
            raise QueueFull
        run_id = str(uuid4())
        record: dict[str, object] = {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": request.assistant_id,
            "input": request.input,
            # The persisted record must describe the run that actually happened.
            # `_graph_config` drops any client-supplied `langgraph_auth_user`
            # before execution, so storing the raw client config would echo a
            # forged principal back on GET /runs/{id} and hand it to anything
            # that reads the record instead of the runtime.
            "config": _sanitized_config(request.config),
            "stream_resumable": request.stream_resumable,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
        }
        if request.command is not None:
            record["command"] = request.command.model_dump(mode="json")
        persisted_record = record
        if isinstance(self.storage.runs, PostgresRunRegistry):
            persisted_record = {**record, "input": PERSISTED_PAYLOAD_REDACTION}
            if "command" in persisted_record:
                persisted_record["command"] = PERSISTED_PAYLOAD_REDACTION
        await self.storage.runs.save(run_id, persisted_record)
        self.runtime[run_id] = RunRuntime(
            request=request, thread_id=thread_id, auth_user=auth_user
        )
        if replay_key is not None:
            self.command_replays[replay_key] = run_id
        if thread_id in self.active:
            self.queues.setdefault(thread_id, deque()).append(run_id)
        else:
            self._start(run_id)
        return record

    @property
    def pending_count(self) -> int:
        return sum(len(queue) for queue in self.queues.values())

    def _start(self, run_id: str) -> None:
        runtime = self.runtime[run_id]
        self.active[runtime.thread_id] = run_id
        _ = self.tasks.start_soon(self._execute, run_id)

    async def _execute(self, run_id: str) -> None:
        runtime = self.runtime[run_id]
        graph = self.graphs[runtime.request.assistant_id]
        assert isinstance(graph, GraphRunner)
        graph_config = self._graph_config(runtime)
        snapshot = await graph.aget_state(graph_config)
        runtime.pre_values = dict(snapshot.values)
        await self.storage.runs.set_status(run_id, "running")
        cancelled = False
        try:
            with anyio.CancelScope() as scope:
                runtime.scope = scope
                graph_input: dict[str, JSONValue] | Command[str]
                if runtime.request.command is None:
                    graph_input = runtime.request.input or {}
                else:
                    graph_input = Command(resume=runtime.request.command.resume)
                async for mode, data in graph.astream(
                    graph_input,
                    graph_config,
                    stream_mode=runtime.request.stream_mode,
                    durability="exit",
                ):
                    runtime.append_event(mode, _to_jsonable(data))  # type: ignore[arg-type]
                    runtime.changed.set()
                    runtime.changed = anyio.Event()
            cancelled = scope.cancel_called
            final = await graph.aget_state(graph_config)
            output = _to_jsonable(dict(final.values))  # type: ignore[arg-type]
            assert isinstance(output, dict)
            runtime.output = output
            await self.storage.runs.set_status(
                run_id, "interrupted" if final.next else "success"
            )
        except anyio.get_cancelled_exc_class():
            cancelled = True
        except Exception:  # noqa: BLE001 - executor boundary records arbitrary graph failures.
            logger.error("run %s failed on graph execution", run_id, exc_info=True)
            await self.storage.runs.set_status(run_id, "error")
        if cancelled:
            await self.storage.runs.set_status(run_id, "interrupted")
        if runtime.rollback:
            await self._restore(graph, graph_config, runtime.pre_values)
        runtime.done.set()
        runtime.changed.set()
        _ = self.active.pop(runtime.thread_id, None)
        queue = self.queues.get(runtime.thread_id)
        if queue and not self.stopping:
            self._start(queue.popleft())

    async def _restore(
        self,
        graph: GraphRunner,
        config: dict[str, JSONValue],
        values: dict[str, JSONValue],
    ) -> None:
        configurable = config["configurable"]
        assert isinstance(configurable, dict)
        await self.storage.saver.adelete_thread(str(configurable["thread_id"]))
        if values:
            await graph.aupdate_state(config, values)

    def _graph_config(self, runtime: RunRuntime) -> dict[str, JSONValue]:
        configurable = runtime.request.config.get("configurable", {})
        merged = dict(configurable) if isinstance(configurable, dict) else {}
        # The authenticated principal is server-controlled state, mirroring the
        # real Agent Server: a client-supplied `langgraph_auth_user` is never
        # trusted, and graphs (coach memory/gate) read the member identity
        # from it.
        merged.pop(AUTH_USER_KEY, None)
        if runtime.auth_user is not None:
            merged[AUTH_USER_KEY] = dict(runtime.auth_user)
        merged["thread_id"] = runtime.thread_id
        return {**runtime.request.config, "configurable": merged}

    def _replay_key(self, thread_id: str, request: RunRequest) -> str | None:
        if request.command is None:
            return None
        return f"{thread_id}:{request.assistant_id}:{request.command.model_dump_json()}"

    async def cancel(self, thread_id: str, run_id: str, request: CancelRequest) -> None:
        runtime = self.runtime[run_id]
        if runtime.thread_id != thread_id:
            raise RunMissing
        record = await self.storage.runs.get(run_id)
        if record is None:
            raise RunMissing
        if record["status"] == "pending":
            self.queues.get(thread_id, deque()).remove(run_id)
            await self.storage.runs.set_status(run_id, "interrupted")
            runtime.done.set()
            runtime.changed.set()
            return
        if record["status"] == "running" and runtime.scope is not None:
            runtime.rollback = request.action == "rollback"
            runtime.scope.cancel()
        if request.wait:
            await runtime.done.wait()
