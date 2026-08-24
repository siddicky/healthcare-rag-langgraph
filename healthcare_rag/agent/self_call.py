"""In-process loopback self-requests for coach route handlers.

Self-requests must never use ``request.base_url``: it reflects the
client-sent Host and scheme, so behind a proxy the "self" call leaves the
app entirely (in prod the edge answers on a port the app does not serve)
and every dependent check fails with an opaque error. ``self_client``
targets the machine's own bound port from ``scope["server"]`` instead —
the request never leaves the box, regardless of Host header, proxy, or
TLS termination.
"""

from __future__ import annotations

import httpx
from starlette.requests import Request


def self_client(request: Request, timeout: float = 10.0) -> httpx.AsyncClient:
    server = request.scope.get("server") or ("127.0.0.1", 8000)
    return httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{server[1]}",
        timeout=timeout,
    )

