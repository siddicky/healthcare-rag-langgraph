<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/catalog

## Purpose
The fail-closed rendering pipeline for `compose_ui` trees the model emits: a fixed catalog of 11 components (`InjectionTracker`, `MiniCalendar`, `TrendCard`, `ActionCard`, `StatRow`, `ScoreRing`, `Timeline`, `Card`, `Tag`, `Label`, `Button`), each with a WIRE zod schema (fact props must be `{__ref: {turn_scope_id, block_id, pointer}}` objects, never literals) and a CONCRETE zod schema (post-hydration shape re-validated as defense-in-depth). `render.tsx` walks a raw tree node by node: wire validation -> dispatch-id check against the fixed map -> hydration against same-turn DATA envelopes (RFC 6901 pointer resolution) -> concrete re-validation -> a `json-render` flat spec fed to `<Renderer/>`. Any node that fails at any stage renders nothing (subtree included); siblings survive. The four fixed-contract cards (`CalendarChangeCard`, `MemoryExtractionCard`, `DocumentIngestCard`, `ReminderCard`) are explicitly NOT part of this catalog — they live in `@/components/generative-ui` and render directly from interrupts/upload status/envelopes.

## Key Files
| File | Description |
|------|-------------|
| `catalog.ts` | `defineCatalog(schema, {components, actions: {}})` — the 11-component list with descriptions (copied verbatim from `catalog.js`) and `ConcreteSchemas` props |
| `dataRef.ts` | `DataRefSchema` — the one `{__ref: {turn_scope_id, block_id, pointer}}` grammar shared with the backend; `isDataRef()` guard |
| `dispatch.tsx` | `DISPATCH_ACTIONS` (9 fixed ids: `log_weight`, `log_injection`, `view_schedule`, `change_schedule`, `set_reminder`, `cancel_reminder`, `upload_document`, `confirm`, `decline`), `DispatchProvider`/`useDispatchAction` — unknown ids fail closed + telemetry, known-but-unregistered ids no-op + telemetry |
| `envelopes.ts` | `DataEnvelope` type + `parseDataEnvelope(s)` — parses the `{turn_scope_id, block_id, data, text}` JSON riding a ToolMessage's `content` |
| `hydrate.ts` | `createHydrator(turnScopeId, envelopes)` — resolves a `DataRef` only if its scope matches the CURRENT turn (cross-turn refs to the same `block_id` are explicitly rejected); `resolvePointer()` implements RFC 6901 |
| `registry.tsx` | `defineRegistry(...)` — maps each catalog component's serializable props back to React children/handlers; all interactive handlers resolve through `useDispatchAction()`, never a model-emitted function |
| `render.tsx` | `resolveCatalogTree()` (pure resolution -> `{roots, elements, events}`) and `<CatalogTree/>` (the component: resolves, emits telemetry, renders via `JSONUIProvider`/`Renderer`) |
| `schemas.ts` | `CATALOG_COMPONENT_NAMES`, `WireSchemas`, `ConcreteSchemas`, `ComposedNodeSchema` (the recursive `{component, props, children?}` wire shape) |
| `telemetry.ts` | `TelemetryEvent` union (unknown_component, wire_rejection, unknown/unregistered_dispatch, unresolved/cross_turn_ref, hydrate_rejection) + swappable `telemetrySink` (silenced under `NODE_ENV=test`) |
| `weekstrip.ts` | `sparseToWeekStrip()` — sparse date-keyed injection days -> exactly 7 Monday-first slots, `muted` filler for empty slots, later array entry wins on date collision |

## For AI Agents

### Working In This Directory
- The wire/concrete schema split is load-bearing: a new catalog component needs BOTH a `WireSchemas` entry (fact props as `DataRefSchema`, static props as literals) and a `ConcreteSchemas` entry (post-hydration real types) in `schemas.ts`, plus an entry in `CATALOG_COMPONENT_NAMES` and `catalog.ts`'s `components` map, plus a registry adapter in `registry.tsx`.
- Never add a literal fact prop to a `WireSchemas` entry — the whole point of the wire grammar is that facts can only enter via a `__ref` resolved against verified tool data.
- Dispatch-id-carrying props are enumerated explicitly in `render.tsx`'s `dispatchIdCarriers()` (`Button.action`, `MiniCalendar.onDateSelectAction`, `ActionCard.primaryAction.action`/`secondaryAction.action`) — a new action-bearing prop must be added there or its dispatch id will never be checked against `DISPATCH_ACTIONS`.
- `transformHydrated()` in `render.tsx` is the place for post-hydration adapters (currently only `InjectionTracker.days` via `sparseToWeekStrip`); keep new adapters there rather than in the registry or the components.
- A `turnScopeId` of `""` (as passed from `MessageList`'s `turn.scopeId ?? ""`) means no envelope will ever match — refs simply stay unresolved, which is the correct fail-closed behavior for a turn with no tool envelopes yet.

### Testing Requirements
- `bun --cwd frontend run test` covers this directory via `src/catalog/__tests__/`: `catalog.test.tsx` (end-to-end tree resolution + rendering), `dataRef.fixture.test.ts` (wire/concrete schema fixtures), `weekstrip.test.ts` (sparse-to-seven adapter edge cases: anchor selection, same-weekday-different-week exclusion, date-collision fold order).
- Any change to `WireSchemas`/`ConcreteSchemas` or the hydration scope rule should add a case to `catalog.test.tsx` proving the fail-closed path (bad node renders nothing, siblings survive, telemetry fires).

### Common Patterns
- Everything here is designed to never throw on untrusted model output: every `safeParse`, every lookup returns `undefined`/`null`/a rejection event rather than throwing, and the top-level `CatalogTree` component only ever returns `null` or a `<Renderer/>` tree.

## Dependencies

### Internal
- `@json-render/core` (`defineCatalog`, `UIElement`), `@json-render/react` (`defineRegistry`, `JSONUIProvider`, `Renderer`, `schema`)
- `@/components/core/*`, `@/components/data-display/*`, `@/components/generative-ui/{ActionCard,InjectionTracker,MiniCalendar,TrendCard}` (registry targets)
- Consumed by `@/chat/model.ts` (`composeTreesForTurn`) and `@/chat/components/MessageList.tsx` (`<CatalogTree/>`)

### External
- `zod` 4

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
