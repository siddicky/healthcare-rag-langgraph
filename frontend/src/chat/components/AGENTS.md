<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/chat/components

## Purpose
The presentational React for the chat screen: the top-level `ChatShell` layout, the message thread renderer (`MessageList`, which decides what each turn's messages/envelopes/compose-trees render as), the pending-interrupt renderer (`InterruptPanel`), the floating `Composer`, the `ThreadSidebar`, the latest-turn `ActionBar`, and a small inline `Icons` set. All state and network calls live in `useCoachChat` (`../useCoachChat.ts`) — these components are driven entirely by props/callbacks.

## Key Files
| File | Description |
|------|-------------|
| `ChatShell.tsx` | The screen composition: wires `useCoachChat(deps)` to `ThreadSidebar`/`MessageList`/`Composer`/`ActionBar`; owns the opener grid and the hidden opener-attach `<input>`; maps catalog `Button` dispatch ids (`DISPATCH_ACTION_TURNS`) to new natural-language chat turns via `chat.send()` |
| `MessageList.tsx` | Renders each `TurnModel`: human bubble, AI bubbles (`AiBubble`/`aiDisplayText`), AI-message cards (`AiMessageCards` — memory confirmation, reminder delivery), composed catalog trees (`<CatalogTree/>` from `@/catalog/render`), tool-message envelope cards (`ToolEnvelopeCards` — calendar-change outcome, reminders list), the in-flight upload card, and the pending `InterruptPanel`; auto-scrolls to bottom on new content |
| `InterruptPanel.tsx` | Renders the ONE pending interrupt (server guarantees at most one per run) via `classifyInterruptPayload()` — `CalendarChangeCard` or `MemoryExtractionCard`; approval issues `Command(resume={accept, fields?})`. Also exports `ReminderEnvelopeCards` for a `reminders:list` DATA envelope (read-only compact `ReminderCard`s) |
| `Composer.tsx` | Textarea + attach + send; Enter sends (Shift+Enter newlines); disabled while a run streams or an upload is in flight |
| `ThreadSidebar.tsx` | New-conversation button, paginated thread list (select/delete), and the account menu (sign out) |
| `ActionBar.tsx` | The latest-turn-only action row: conditional Regenerate (only when `showRegenerate` is true), Branch, thumbs up/down feedback (disabled once sent or failed) |
| `Icons.tsx` | Minimal inline SVG icon set ported from the coach-chat UI kit (no external icon library) |

## For AI Agents

### Working In This Directory
- `ChatShell`'s `DISPATCH_ACTION_TURNS` map is the ONE place natural-language phrasing for catalog Button actions lives (`log_weight` -> `"Log today's weight"`, etc.) — a new dispatch action that should turn into a chat message needs an entry here, not in `@/catalog/dispatch.tsx` (that file only validates ids, it never carries copy).
- `MessageList`'s `TurnView` renders AI messages, compose-trees, and tool-message cards in that FIXED order per turn — don't reorder without checking `e2e/smoke.spec.ts`, which asserts card visibility/counts by DOM order (`data-testid="compose-tree"`, `"interrupt-card"`, etc.).
- `ActionBar`/`InterruptPanel`/upload cards render only for the LAST turn (`index === turns.length - 1`) in `MessageList` — this mirrors the turn-local eligibility rule in `regenerateEligibility()`; don't make an action bar appear on an older turn.
- Composer/attach controls must stay disabled while `chat.busy` or an upload is `inflight` — the backend allows exactly one active run per thread.

### Testing Requirements
- Covered by `src/chat/__tests__/chat.test.tsx` (full-shell integration), `actionBar.test.tsx` (eligibility/disabled-state assertions), and `genUi.test.tsx` (compose-tree rendering through `MessageList`). E2E coverage in `e2e/smoke.spec.ts` exercises the full DOM (interrupt cards, compose trees, action bar, sidebar) against a real server.
- New components here should be testable via Testing Library + a scripted `CoachChatDeps`, not by mocking `fetch` inside the component.

### Common Patterns
- `data-testid` attributes (`action-bar`, `interrupt-card`, `compose-tree`, `document-ingest`, `reminder-list`, `memory-confirmation`, `reminder-card`, `upload-error`, `opener-attach-input`) are the stable hooks both Vitest component tests and Playwright E2E rely on — preserve them when refactoring markup.
- Card JSON never renders as raw bubble text — `aiDisplayText()`/`parseComponentCard()` gate what `AiBubble` shows before falling back to plain text.

## Dependencies

### Internal
- `@/catalog/dispatch` (`DispatchActionId`, `DispatchHandlers`), `@/catalog/render` (`CatalogTree`), `@/catalog/envelopes` (`parseDataEnvelope`)
- `@/chat/model`, `@/chat/coachProtocol`, `@/chat/useCoachChat`, `@/chat/stream` (`chatTelemetry`), `@/chat/uploadFlow`
- `@/components/generative-ui/{CalendarChangeCard,DocumentIngestCard,MemoryExtractionCard,ReminderCard}`

### External
- `zod` 4 (interrupt/envelope payload parsing in `InterruptPanel.tsx`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
