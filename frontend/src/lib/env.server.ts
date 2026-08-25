/**
 * Server-only configuration. NEVER import this from a client component —
 * these names are deliberately NOT NEXT_PUBLIC_* and must never reach the
 * browser bundle (see src/lib/env.ts for the client-side convention).
 */

/**
 * The LangGraph deployment the `/api/copilotkit` runtime route proxies to.
 * Lazy-throw at call time (same pattern as `getSupabase()`): a misconfigured
 * deploy fails on first request instead of crashing the build.
 */
export function langgraphDeploymentUrl(): string {
  const url = process.env.LANGGRAPH_DEPLOYMENT_URL;
  if (!url) {
    throw new Error(
      "LANGGRAPH_DEPLOYMENT_URL is not set: the /api/copilotkit route cannot reach the coach graph",
    );
  }
  return url.replace(/\/+$/, "");
}
