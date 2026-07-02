"""
Prometheus metrics middleware for the API Gateway (Layer 1).

Records per-request metrics using the counters and histogram defined in
``api_gateway/metrics.py``.  Registered as the outermost middleware so it
wraps auth, rate-limiting, and routing — every request (including 401/429)
is measured.

Excluded paths: /metrics, /health — these are infrastructure probes and
must not skew application metrics.

Route template resolution: after FastAPI routing, ``request.scope["route"]``
carries the matched ``APIRoute`` whose ``.path`` attribute is the parameterised
template (e.g. ``/v1/chat/completions``).  If the route is absent (e.g. 404)
we fall back to the raw URL path.

Validates: Requirements 10.1, 10.2, 10.3, 10.4
"""

from __future__ import annotations

import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from api_gateway.metrics import ERRORS_TOTAL, LATENCY_SECONDS, REQUESTS_TOTAL


def _get_route_template(request: Request) -> str | None:
    """Return the matched route's path template, or *None* if unavailable.

    FastAPI / Starlette populates ``request.scope["route"]`` after the router
    has matched the incoming URL.  For unmatched requests (404) the key is
    absent.

    Args:
        request: The current incoming request.

    Returns:
        The route template string (e.g. ``"/v1/chat/completions"``) or
        ``None`` when no route was matched.
    """
    route = request.scope.get("route")
    if route is not None:
        return getattr(route, "path", None)
    return None


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that records Prometheus metrics.

    For every request **not** in :attr:`EXCLUDE_PATHS`:

    * ``llm_api_gateway_requests_total`` — incremented once, labelled with
      ``status_code`` and normalised ``path``.
    * ``llm_api_gateway_errors_total`` — incremented for 4xx and 5xx
      responses, labelled with ``error_code``.
    * ``llm_api_gateway_latency_seconds`` — wall-clock time from the start
      of ``dispatch`` to the completion of ``call_next``, labelled with
      normalised ``path``.

    The ``path`` label uses the *route template* (e.g.
    ``/v1/chat/completions``) rather than the raw URL so that parameterised
    routes like ``/v1/items/42`` and ``/v1/items/99`` collapse into a single
    label value, preventing unbounded cardinality.
    """

    EXCLUDE_PATHS: frozenset[str] = frozenset({"/metrics", "/health"})

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        start = time.time()
        response = await call_next(request)
        latency = time.time() - start

        path = request.url.path
        if path not in self.EXCLUDE_PATHS:
            # Prefer the route template; fall back to raw path (e.g. for 404s)
            route_path = _get_route_template(request) or path

            REQUESTS_TOTAL.labels(
                status_code=str(response.status_code),
                path=route_path,
            ).inc()

            if response.status_code >= 400:
                ERRORS_TOTAL.labels(
                    error_code=str(response.status_code),
                ).inc()

            LATENCY_SECONDS.labels(path=route_path).observe(latency)

        return response
