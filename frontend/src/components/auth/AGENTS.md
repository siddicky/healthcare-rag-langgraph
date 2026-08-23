<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/components/auth

## Purpose
The Supabase email+password sign-in screen: a single `LoginForm` component that submits `signInWithPassword`, maps auth errors to member-friendly copy, and redirects to `/chat` (or a caller-supplied `redirectTo`) on success.

## Key Files
| File | Description |
|------|-------------|
| `LoginForm.tsx` | Full-screen centered `Card` with email/password inputs, submit-pending state, and `loginErrorMessage()` (maps "invalid login credentials" to a specific message, everything else to a generic one); accepts an injectable `client?: SupabaseClient` for tests, defaulting to `getSupabase()` |

## For AI Agents

### Working In This Directory
- `LoginForm`'s `client` prop exists specifically so tests can inject a fake Supabase client instead of hitting `getSupabase()` (which throws if `NEXT_PUBLIC_SUPABASE_*` env vars are unset) — keep that injection seam when modifying the component.
- A thrown error whose message starts with `"Sign-in is not configured"` (the exact string `getSupabase()` throws) is shown verbatim to the member; any other thrown error is replaced with a generic connectivity message. Preserve that string-matching contract if you change `getSupabase()`'s error message.
- Inline styles here (not the `.form-*`/`.card`/`.btn-*` classes) use raw `var(--space-*)`/`var(--rust)`/etc. design tokens directly — this is the one component allowed to compose layout with inline styles atop the design system rather than a dedicated CSS class.

### Testing Requirements
- `__tests__/login.test.tsx` (Vitest + Testing Library) — covers successful sign-in/redirect, invalid-credential messaging, and the not-configured error path via the injected `client` prop.
- `e2e/smoke.spec.ts`'s `"wrong password stays on /login with an error"` test exercises this against a real (hermetic-stub) Supabase.

### Common Patterns
- Standard controlled-input form state (`useState` per field) with a single `pending` flag disabling the submit button and swapping its label to "Signing in…".

## Dependencies

### Internal
- `@/components/core/{Button,Card,Label}`
- `@/lib/supabase` (`getSupabase`)

### External
- `@supabase/supabase-js` (`AuthError`, `SupabaseClient`)
- `next/navigation` (`useRouter`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
