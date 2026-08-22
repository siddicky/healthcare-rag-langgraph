from __future__ import annotations

# Explicit enumerated list of documented-but-unimplemented endpoints → 501
UNIMPLEMENTED_PATHS: list[str] = [
    "/metrics",
    "/store/namespaces/search",
    "/webhooks",
    "/assistants",
    "/assistants/search",
    "/runs/wait",
    "/runs/stream",
    "/listeners",
]

# Prefixes that should also return 501
UNIMPLEMENTED_PREFIXES: list[str] = [
    "/store/",
    "/assistants/",
    "/runs/",
    "/webhooks/",
    "/listeners/",
]

# MCP/A2A are NOT in 501 — they are unmounted → 404/405
