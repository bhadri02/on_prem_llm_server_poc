"""
Rate limiting middleware for the API Gateway (Layer 1).

Implements a per-API-key fixed-window in-memory rate limiter.
Exempt paths (/health, /metrics) bypass rate limiting entirely.

On breach: emits a ``rate_limited`` audit event and returns HTTP 429
with a ``Retry-After: 60`` header.

Validates: Requirements 3.1–3.7, 9.4
"""

from __future__ import annotations

import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api_gateway.config import get_settings
from api_gateway.services.audit import build_audit_event, emit_audit_event

_ERROR_429 = {"error": {"code": "429", "message": "Rate limit exceeded"}}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces per-API-key rate limiting on all non-exempt paths.

    Uses an in-memory fixed-window algorithm:
    - Tracks request timestamps per API key in ``_store``.
    - On each request, evicts timestamps outside the current window.
    - If the number of remaining timestamps meets or exceeds the configured
      limit, the request is rejected with HTTP 429.

    ``_store`` is a class variable so it is shared across all instances
    (safe without a lock in a single-instance asyncio deployment).

    Exempt paths:
        - /health  — liveness probe; no rate limiting applied
        - /metrics — Prometheus scrape endpoint; no rate limiting applied
    """

    # Shared across all instances — intentional for single-process asyncio
    _store: dict[str, list[float]] = {}

    EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/metrics"})

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Skip rate limiting for health/metrics probes
        if request.url.path in self.EXEMPT_PATHS or request.url.path.startswith("/metrics"):
            return await call_next(request)

        settings = get_settings()

        key = request.headers.get("X-Api-Key", "unknown")
        now = time.time()
        window_start = now - settings.rate_limit_window_seconds

        # Evict timestamps that have fallen outside the current window
        timestamps = [t for t in self._store.get(key, []) if t > window_start]

        if len(timestamps) >= settings.rate_limit_requests:
            # Resolve request_id — prefer one already set on request state
            request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())

            emit_audit_event(
                build_audit_event(
                    request_id=request_id,
                    user_id="poc-user",
                    event_type="rate_limited",
                    method=request.method,
                    path=request.url.path,
                    status_code=429,
                    outcome="block",
                )
            )

            return JSONResponse(
                status_code=429,
                content=_ERROR_429,
                headers={"Retry-After": "60"},
            )

        # Request is within limits — record timestamp and continue
        timestamps.append(now)
        self._store[key] = timestamps

        return await call_next(request)
