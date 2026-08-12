"""
Rate limiting middleware for the API Gateway (Layer 1).

Implements a per-API-key fixed-window rate limiter backed by Redis (not
in-process memory), so the limit is enforced correctly across multiple
api_gateway replicas rather than reset per-replica.

There is no platform-wide request-count fallback: every key carries its own
concrete rate_limit_rpm (admin_portal, resolved via /portal/keys/resolve —
see api_gateway/services/key_resolver.py::KeyProfile.rate_limit_override,
which is a required int, never None). Exempt paths (/health, /metrics)
bypass rate limiting entirely.

On breach: emits a ``rate_limited`` audit event and returns HTTP 429
with a ``Retry-After`` header.

Validates: Requirements 3.1–3.7, 9.4
"""

from __future__ import annotations

import time
import uuid

import redis.exceptions
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api_gateway.config import get_settings
from api_gateway.services.audit import build_audit_event, emit_audit_event
from api_gateway.services.audit_client import schedule_audit_post
from shared.observability.logging import emit, get_logger

_ERROR_429 = {"error": {"code": "429", "message": "Rate limit exceeded"}}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces per-API-key rate limiting on all non-exempt paths.

    Uses a Redis-backed fixed-window counter:
    - Bucket key: ``ratelimit:{key_id}:{window_id}``, where ``key_id`` is
      ``request.state.user_profile.key_id`` (set by ``AuthMiddleware``, which
      runs first — see ``main.py``'s middleware order) and ``window_id`` is
      the current time divided into ``rate_limit_window_seconds``-wide
      buckets. A new bucket key naturally starts the count over each window;
      Redis TTLs the key so old buckets don't accumulate.
    - ``INCR`` is atomic on its own — no separate read-then-write race, and
      no Lua script needed (kept simple deliberately: this is the standard
      fixed-window counter, not a sliding-window log — it can allow a short
      burst of up to ~2x the limit right at a window boundary, which is an
      accepted, well-known trade-off for the simplicity/atomicity it buys).
    - The limit is always ``user_profile.rate_limit_override`` — there is no
      global fallback value; every key resolves to a concrete int.
    - If Redis itself is unreachable, the request is allowed through (fails
      open, not closed) — rate limiting is a cost/abuse-control mechanism,
      not an auth boundary, so a Redis outage degrading to "unlimited"
      is preferable to it taking down the whole Gateway.

    Exempt paths:
        - /health  — liveness probe; no rate limiting applied
        - /metrics — Prometheus scrape endpoint; no rate limiting applied
    """

    EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/metrics"})

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Skip rate limiting for health/metrics probes
        if request.url.path in self.EXEMPT_PATHS or request.url.path.startswith("/metrics"):
            return await call_next(request)

        settings = get_settings()

        # AuthMiddleware runs before this middleware and stashes the resolved
        # profile (including its own concrete rate_limit_override) on
        # request.state. Every request reaching this middleware has already
        # passed AuthMiddleware, so user_profile is always set in practice —
        # this is genuinely unreachable, not a bypass path an attacker could
        # trigger. Allowed through rather than inventing a fallback limit
        # that isn't backed by any real key.
        user_profile = getattr(request.state, "user_profile", None)
        if user_profile is None:
            return await call_next(request)

        request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())

        window_seconds = settings.rate_limit_window_seconds
        window_id = int(time.time() // window_seconds)
        redis_key = f"ratelimit:{user_profile.key_id}:{window_id}"

        redis_client = request.app.state.redis
        try:
            count = await redis_client.incr(redis_key)
            if count == 1:
                await redis_client.expire(redis_key, window_seconds)
        except redis.exceptions.RedisError as exc:
            emit(
                get_logger(request_id),
                level="ERROR",
                event="rate_limit_redis_unavailable",
                message="Redis unreachable for rate limiting — failing open (request allowed)",
                exception_type=type(exc).__name__,
            )
            return await call_next(request)

        if count > user_profile.rate_limit_override:
            event = build_audit_event(
                request_id=request_id,
                user_id="poc-user",
                event_type="rate_limited",
                method=request.method,
                path=request.url.path,
                status_code=429,
                outcome="block",
            )
            emit_audit_event(event)

            response = JSONResponse(
                status_code=429,
                content=_ERROR_429,
                headers={"Retry-After": str(window_seconds)},
            )
            schedule_audit_post(response, event, request.app.state.http_client)
            return response

        return await call_next(request)
