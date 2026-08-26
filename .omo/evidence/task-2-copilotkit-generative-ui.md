# Task 2 — Capture CopilotKit runtime route contract

Date: 2026-08-25 · Branch: `release/v1.5.0` · Status: DONE, hermetic probe passes

## Probe construction

The contract was measured with `@copilotkit/runtime@1.69.1` through its v2
entrypoint. The runtime under test is constructed with:

- `CopilotRuntime`
- `LangGraphAgent({ deploymentUrl: <local recording proxy>, graphId: "coach" })`
- `InMemoryAgentRunner`

`scripts/probe_copilotkit_runtime_contract.mjs` starts a hermetic LangGraph
fixture, a recording proxy, and the CopilotKit runtime. It exercises real coach
execution through interrupt, resume, and reconnect, then replays the captured
upstream requests through the member perimeter. No product server or external
service is required.

## Public v2 route inventory

| Method | Runtime path | Observed status | Contract note |
|---|---|---:|---|
| `GET` | `/info` | 200 | Runtime and agent discovery |
| `POST` | `/agent/coach/run` | 200 | Starts an AG-UI run stream |
| `POST` | `/agent/coach/connect` | 200 | Reconnect is **POST**, not GET |
| `GET` | `/threads` | 200 | Thread listing |
| `GET` | `/threads/:threadId/messages` | 200 | Thread message history |
| `GET` | `/threads/:threadId/events` | 200 | Thread event history |
| `GET` | `/threads/:threadId/state` | 200 | Runtime-maintained agent state |
| `POST` | `/agent/coach/stop/:threadId` | 200 | Stops the active thread run |
| `POST` | `/transcribe` | 503 | Route exists; no transcription service configured |

## LangGraph upstream traffic

All observed upstream calls carried the configured bearer authorization header.
Dynamic UUIDs are represented as `:threadId` and `:assistantId` below.

| Method | Upstream path | Body shape / purpose |
|---|---|---|
| `POST` | `/assistants/search` | `{ graph_id, limit, offset }` |
| `GET` | `/threads/:threadId` | Probe for an existing thread |
| `POST` | `/threads` | `{ metadata: {}, thread_id }` |
| `GET` | `/threads/:threadId/state` | Initial state and interrupt lookup |
| `GET` | `/assistants/:assistantId/schemas` | Graph schemas |
| `GET` | `/assistants/:assistantId/graph` | Graph topology |
| `POST` | `/threads/:threadId/runs/stream` | `{ assistant_id, input, stream_mode, stream_subgraphs }` |

The stream input contained `question`, one user `messages` item, empty `tools`,
and `copilotkit: { actions: [], context: [] }`. `stream_mode` is an array and
`stream_subgraphs` is boolean.

## Lifecycle behavior

- Initial scheduling run emitted 62 AG-UI events and reached the coach
  scheduling interrupt.
- The LangGraph custom event name observed for that transition is
  `on_interrupt`.
- The authoritative interrupt id was available from LangGraph thread state at
  `tasks[].interrupts[].id`; the probe does not infer it from display content.
- Resume emitted 3 events.
- `POST /agent/coach/connect` emitted 44 replay/reconnect events for the same
  thread.

## Member-perimeter replay

Captured requests were first accepted by the hermetic upstream, then replayed
unchanged against the existing member perimeter:

- Assistant discovery, assistant schemas, assistant graph, and direct
  `/runs/stream` calls were rejected with 403.
- Unknown direct thread and thread-state reads returned 404.
- The result confirms that CopilotKit's direct LangGraph transport is not, by
  itself, a member-authorized route contract; integration must remain behind an
  explicitly allowed server surface rather than widening the perimeter.

## Dead-upstream behavior

After the LangGraph fixture was stopped, `/agent/coach/run` retried
`POST /assistants/search` three times. The underlying adapter logged
`Failed to retrieve assistant`, but the HTTP request did not settle with a
runtime response or `RUN_ERROR` event. The probe's caller-side 10,000 ms deadline
aborted it at 10,003 ms and classified the result as `request-error` with
`callerTimedOut: true`.

This is the measured 1.69.1 contract: callers need their own bounded timeout for
an unavailable LangGraph deployment; they must not assume the v2 runtime route
will promptly return an HTTP error.

## Process cleanup and command result

```text
$ bun scripts/probe_copilotkit_runtime_contract.mjs
probe: ports 55118,55119,55120
probe: pids 85619,85621,85622
spawnedPids: [85619,85621,85622]
killedPids:  [85619,85621,85622]
deadUpstream.elapsedMs: 10003
deadUpstream.timeoutMs: 10000
exit: 0
```

Post-run `kill -0` checks confirmed PIDs 85619, 85621, and 85622 were stopped.

## Files touched

- `scripts/probe_copilotkit_runtime_contract.mjs`
- this evidence file
