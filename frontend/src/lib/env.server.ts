/**
 * Server-only accessors. NEVER import this module from a client component.
 * `NEXT_PUBLIC_LANGGRAPH_URL` is accepted as a deployment alias, but it is
 * read here only to configure the server-side CopilotKit proxy.
 */

/**
 * The LangGraph deployment the `/api/copilotkit` runtime route proxies to.
 * Lazy-throw at call time (same pattern as `getSupabase()`): a misconfigured
 * deploy fails on first request instead of crashing the build.
 */
export function langgraphDeploymentUrl(): string {
  const url =
    process.env.LANGGRAPH_DEPLOYMENT_URL ?? process.env.NEXT_PUBLIC_LANGGRAPH_URL;
  if (!url) {
    throw new Error(
      "LANGGRAPH_DEPLOYMENT_URL or NEXT_PUBLIC_LANGGRAPH_URL is not set: the /api/copilotkit route cannot reach the coach graph",
    );
  }
  return url.replace(/\/+$/, "");
}
