<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/app

## Purpose
The Next.js App Router tree. Three routes: `/` (redirects to `/chat`), `/login` (renders `LoginForm`), and `/chat` (a client component that gates on a Supabase session, then mounts `ChatShell` with browser-wired deps). `layout.tsx` sets the page metadata and imports the one global stylesheet; there is no other server-rendered chrome — everything below `/chat` and `/login` is client-rendered.

## Key Files
| File | Description |
|------|-------------|
| `layout.tsx` | Root layout: sets `<html lang="en">`, page `Metadata` ("Nymble coach"), imports `./globals.css` |
| `page.tsx` | `/` — a server component that immediately `redirect("/chat")`s |
| `globals.css` | The one design-system import: `@import "../design/styles.css"` — tokens, base, and every component class |
| `chat/page.tsx` | `/chat` (client component) — checks `getSupabase().auth.getSession()`, redirects to `/login` if unauthenticated, otherwise renders `ChatShell` with `createBrowserDeps()` and the member's email |
| `chat/chat.css` | Chat-page-scoped styles (bubbles, sidebar, composer, banners, opener grid) layered on top of the design system |
| `login/page.tsx` | `/login` (server component) — sets the `Sign in · Nymble coach` title and renders `LoginForm` |

## For AI Agents

### Working In This Directory
- `/chat`'s session check is client-side only (`useEffect` + `getSupabase().auth.getSession()`); there is no middleware-based auth gate, so a signed-out visit briefly renders the "checking" placeholder (`aria-busy="true"` section on `var(--birch)`) before the redirect fires.
- `ChatShell` is always constructed with `createBrowserDeps()` from `@/chat/coachClient` here — never inject a different deps bundle in this file; that's what `chat/__tests__` and component tests are for.
- Route-level CSS (`chat.css`) is imported directly by `chat/page.tsx`, not through `globals.css` — keep page-specific rules there, not in the design system.

### Testing Requirements
- These route files are thin wiring and are exercised primarily by `e2e/smoke.spec.ts` (login flow, `/chat` redirect, composer visibility) rather than Vitest unit tests. No `__tests__/` folder exists under `src/app/`.

### Common Patterns
- Client components in this tree keep almost no logic of their own — `ChatPage` only handles the auth gate and hands everything else to `ChatShell`/`useCoachChat`.

## Dependencies

### Internal
- `@/chat/components/ChatShell`, `@/chat/coachClient` (`createBrowserDeps`)
- `@/components/auth/LoginForm`
- `@/lib/supabase` (`getSupabase`)
- `../design/styles.css`

### External
- `next/navigation` (`redirect`, `useRouter`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
