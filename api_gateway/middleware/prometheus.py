"""
Prometheus metrics middleware for the API Gateway (Layer 1).

Delegates metric recording to the shared observability module's
``PrometheusMiddleware``, configured with the api_gateway ``LayerMetrics``.

The shared middleware:
- Records per-request metrics using ``LAYER_METRICS.record_request()``
- Maps HTTP status codes to metric status labels: ``success | error | blocked``
- Extracts ``department`` and ``model`` from ``X-Department`` and ``X-Model``
  request headers (falling back to ``"unknown"`` if absent)

Excluded paths: /metrics, /health — these are infrastructure probes and
must not skew application metrics. The shared middleware does NOT yet
implement path exclusion; this wrapper applies the exclusion logic.

Validates: Requirements 2.19, 2.20
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from api_gateway.metrics import LAYER_METRICS
from shared.observability.middleware import PrometheusMiddleware as SharedPrometheusMiddleware


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that records Prometheus metrics.

    Wraps the shared ``PrometheusMiddleware`` and adds api_gateway-specific
    path exclusion logic for infrastructure endpoints that should not be
    counted in application metrics.

    For every request **not** in :attr:`EXCLUDE_PATHS`:

    * ``llm_api_gateway_requests_total`` — incremented once, labelled with
      ``status``, ``department``, and ``model``.
    * ``llm_api_gateway_latency_seconds`` — wall-clock time from the start
      of ``dispatch`` to the completion of ``call_next``, labelled with
      ``department``.

    The ``status`` label is mapped from HTTP status code:
      - 2xx / 3xx  → ``"success"``
      - 401 / 403  → ``"blocked"``
      - other 4xx  → ``"error"``
      - 5xx        → ``"error"``
    """

    EXCLUDE_PATHS: frozenset[str] = frozenset({"/metrics", "/health"})

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        # Instantiate the shared PrometheusMiddleware with api_gateway's
        # LayerMetrics. The shared middleware does not know about the app
        # object directly; it operates on layer_metrics only.
        self._shared_middleware = SharedPrometheusMiddleware(app, LAYER_METRICS)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Apply path exclusion before delegating to the shared middleware.
        path = request.url.path
        if path in self.EXCLUDE_PATHS:
            # Skip metric recording for infrastructure endpoints.
            return await call_next(request)

        # Delegate to shared PrometheusMiddleware for all other paths.
        return await self._shared_middleware.dispatch(request, call_next)

