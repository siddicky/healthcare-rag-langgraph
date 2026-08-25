<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# tests/agent

## Purpose
Tests for the coach agent platform (`healthcare_rag/agent/`) — the deterministic
pre-agent gate, the single top-level `create_agent` coach and its tools
(including `medical_lookup`, its `return_direct` handoff into the RAG graph),
uploads, reminders, erasure, and the auth/perimeter boundary between member and
internal/operator callers. This is the part of the system with the most
security-sensitive surface (PHI, cross-tenant isolation, credential-scoped
routes), so most files here read as an exhaustive decision matrix rather than a
handful of happy-path cases.

## Key Files
| File | Description |
|------|-------------|
| `conftest.py` | Shared fixtures for the coach-agent test suite (server/app construction, principal stubs, envelope builders) |
| `test_auth.py` | Auth wiring for the agent server |
| `test_coach_gate.py` | The deterministic pre-agent gate ordering: cron_wake → attachment → red-flag/injection/identifier short-circuit → erasure → everything else falls through to the coach agent |
| `test_deploy_config.py` | Deploy config pins runtime/privacy dependencies; `.env.example` declares deployment/feedback projects; feedback startup validation names the missing/invalid var and probes its own project; composed-version route requires both internal credentials; smoke settings reject local-dev + missing environment |
| `test_documents.py` | Upload parser avoids framework multipart spooling; no persistent raw-byte write path exists anywhere in the `agent` package |
| `test_features.py` | `is_erase_request()`: all erasure-request phrasings recognized, non-erasure text rejected |
| `test_finalized_stream.py` | `finalize` streams only the safe public answer — no card payloads, identifiers scrubbed first |
| `test_member_streaming.py` | Member stream allows updates + safe custom chunks but never raw model tokens |
| `test_memory.py` | `sanitize_memory_field()` scrub/reject-on-residual/reject-on-scanner-exception; principal mapping for the platform proxy shape; `authenticated_user_id` reads the proxy principal |
| `test_perimeter_composed.py` | LangGraph config mounts a fail-closed HTTP app; member native route allow-list (accept-listed vs everything-else-rejected); run-envelope key/value strictness; attachment sentinel; unified resume shape; recursive state-projection filtering of private sentinels and pending-doc-op ids |
| `test_perimeter_studio.py` | Studio auth stays enabled; Studio principal bypasses the member perimeter; member principal is still held to contract routes; anonymous requests stay unauthorized |
| `test_rag_relay.py` | `relay_question()` — the bridge the `medical_lookup` tool calls into the underlying RAG pipeline |
| `test_reminders.py` | Reminder creation/update/cancellation lifecycle and its store interactions |
| `test_route_b.py` | The top-level coach agent: tool-call message safety (`to_safe_message` drops provider metadata, preserves tool-error status), same-turn `__ref` composition requirement, whole-channel finalize projection without id changes, `medical_lookup` round trip (`return_direct` → `after_agent` relay into an `AIMessage`), the mixed-call guard that drops non-medical tool calls issued alongside `medical_lookup`, per-thread RAG child history nested under the coach agent's checkpoint namespace |
| `test_server_perimeter.py` | The largest test file: public `/health` vs authenticated native routes; member thread ownership (create/read/search/copy/delete cleanup ordering); internal reservation dual-secret + metadata exactness; reservation credentials can't touch ordinary threads; cron ops are owner-scoped; CORS preflight; upload streaming/extraction idempotency and cross-owner rejection; attachment thread-binding and single-use; feedback owner+key scoping |
| `test_store_data.py` | Writable collection allow-list is exact; envelope has a stable turn scope; no hardcoded schedule-collection string literal outside the allow-list module |
| `test_tool_change_schedule.py` | `change_schedule` tool schema rejects cross-action fields and off-contract variants |
| `test_tool_log_injection.py` | Tool schema exposes only model-authored args (no injected internal fields) |
| `test_tool_log_metric.py` | Same schema-exposure check for the metric-logging tool, plus metrics-envelope block-id/metric matching |
| `test_tool_view_schedule.py` | Same schema-exposure and contract checks for the view-schedule tool |

## For AI Agents

### Working In This Directory
- This is the security-critical suite for the member-facing surface — treat any perimeter/route-allow-list/reservation test as a spec, not an obstacle. If a change makes one of these fail, the fix is almost always in `healthcare_rag/agent/`, not in relaxing the test.
- `test_coach_gate.py` covers a short deterministic decision list, not an LLM classifier — the gate no longer makes a model call; routing between medical and non-medical questions is now the top-level coach agent's job (prompt-driven, via `medical_lookup`), not the gate's.
- Catalog facts must never be literals in a response — every fact prop is a `__ref` into a same-turn DATA envelope (`test_route_b.py`, `test_store_data.py`); a new tool or response path that emits a literal fact value is a regression, not a style choice.
- Route B's `ToolCallLimitMiddleware` limit is the single source of truth for `compose_ui`'s tool-call cap — do not add a second ad-hoc interrupt counter (see the root `AGENTS.md` gotcha list).
- `SERVER_LOCAL_DEV` (credential-less Studio principal) must stay dev-only; `test_deploy_config.py` and `test_perimeter_studio.py` are what enforce that a prod/Fly config can't accidentally ship it enabled.

### Testing Requirements
```
uv run pytest tests/agent/ -q
uv run pytest tests/agent/test_server_perimeter.py -q   # perimeter/ownership regressions
uv run pytest tests/agent/test_coach_gate.py -q         # feature-gate ordering
```
These run offline against in-memory stores; no `ORACLE=1`/Fly/Weaviate dependency.

### Common Patterns
- Perimeter tests build a real (in-memory) server/app via `conftest.py` fixtures and drive it through HTTP-shaped calls rather than importing route handlers directly — prefer that over calling internals when adding a new perimeter test.
- Tests that assert "no such literal/import exists anywhere in the package" (e.g. `test_agent_package_has_no_persistent_raw_byte_write_path`, `test_agent_store_code_has_no_schedule_collection_literal`) do a source scan rather than a runtime check — follow that pattern for new invariants that are about absence, not behavior.

## Dependencies

### Internal
- `healthcare_rag/agent/` (the system under test: gate, tools, uploads, reminders, erasure, auth/perimeter)
- `server/` shares some perimeter/topology concepts with `tests/server/` — check both when changing shared auth code

### External
- `langgraph` runtime types (`Runtime`, `Command`) used directly in several tests
- `langchain_core.messages` (`AIMessage`, `AnyMessage`) for streaming/message tests
- `pydantic` (`JsonValue`) for envelope/schema tests

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
