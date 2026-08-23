<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# agent/tools

## Purpose
Route B domain tools: the LangChain `@tool`-decorated functions the coach's
tool-calling sub-agent (`agent/coach_agent.py`) can invoke. One sibling module
per tool, each independently authenticating the user and scoping its
store/envelope writes to the current turn.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Docstring only: "Route B domain tools; one sibling module per tool." No re-exports. |
| `change_schedule.py` | `change_schedule` tool — add/reschedule/cancel a schedule entry (injection/check-in/appointment) with a human-in-the-loop `interrupt()` confirmation card; `_pending_op` dedupes via `OpRecord`/`ApprovalEvent`; `TargetResolutionError`/`ScheduleRuntimeError`. |
| `_change_schedule_contract.py` | Pure request/response contract backing `change_schedule.py`: `AddRequest`/`RescheduleRequest`/`CancelRequest`/`Destination` models, `canonical_request`, `card_payload` (interrupt card JSON), `event_mutation`, `fold_event`. Not itself a tool — no `@tool`. |
| `log_injection.py` | `log_injection` tool — records today's injection dose event only; derives any `upcoming` day / `nextDoseLabel` strictly from approved schedule entries (via `list_schedule`/`next_dose`), never inferred cadence; emits a sparse week-strip DATA envelope for the `InjectionTracker` UI component. |
| `log_metric.py` | `log_metric` tool — persists one metric reading (`MetricName` enum) and emits a trend DATA envelope; model supplies only `metric`/`value`/`unit` — date and owner come from the server/authenticated principal, never the model. |
| `view_schedule.py` | `view_schedule` tool (read-only) — computes a `MiniCalendar` DATA envelope server-side via Python's `calendar` module (no model date math) plus a text listing of `entry_id | date | time | kind | note` so the model has real ids to pass into `change_schedule`. |

## For AI Agents

### Working In This Directory
- Every tool re-derives the authenticated user id itself
  (`_authenticated_user_id(config)` per module) rather than trusting the
  model's arguments — never accept a user id as a tool argument.
- `log_metric` and `log_injection` never accept a date or id from the model:
  `_server_today()` / the server clock is the only source of truth for "when".
- `change_schedule` is the only tool here that uses `interrupt()` for
  human-in-the-loop confirmation; `_change_schedule_contract.py` keeps that
  request/response shape pure and independently testable from the LangGraph
  wiring.
- `view_schedule`'s MiniCalendar DATA envelope carries no entry ids by design
  (the design contract), so entry ids exist only in the text listing —
  don't try to add ids into the envelope's data payload.
- Notes/kinds read out of the events fold are already PHI/PII-scrubbed at
  write time (`store_data.append_event`); do not re-scrub or assume they are
  raw.

### Testing Requirements
- `tests/agent/test_tool_change_schedule.py`, `test_tool_log_injection.py`,
  `test_tool_log_metric.py`, `test_tool_view_schedule.py` — one per tool
  module, mirroring the file layout here.

### Common Patterns
- Each tool defines its own small `_AuthPrincipal` Pydantic model and
  `_authenticated_user_id` / `_turn_scope` helpers rather than importing a
  shared one — intentional duplication to keep each tool module
  self-contained and independently reviewable.
- Envelope construction goes through `agent.store_data.make_envelope`, scoped
  by `(thread_id, coach_human_msg_id)` pulled from `RunnableConfig.configurable`.

## Dependencies

### Internal
- `healthcare_rag/agent/store_data.py` — all persistence (`ScheduleEntry`, `MetricEntry`, `InjectionLogEntry`, `append_event`, `list_schedule`, `next_dose`, `make_envelope`).
- `healthcare_rag/agent/memory.py` — `principal_mapping`, `MemoryIdentityError` (used by `view_schedule.py`).
- `healthcare_rag/agent/state.py` — `CoachState` (tool runtime type parameter).

### External
- `langchain.tools` (`tool`, `ToolRuntime`, `InjectedToolArg`), `langgraph.prebuilt.InjectedStore`, `langgraph.types.interrupt`, `pydantic`.

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
