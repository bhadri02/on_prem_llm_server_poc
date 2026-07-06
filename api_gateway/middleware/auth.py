"""
Authentication middleware for the API Gateway (Layer 1).

Validates the X-Api-Key header against the configured gateway API key.
Exempt paths (/health, /metrics) bypass authentication entirely.

Validates: Requirements 2.2–2.8, 9.2, 9.3
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api_gateway.config import get_settings
from api_gateway.schemas.audit import AuditEvent
from api_gateway.services.audit import build_audit_event, emit_audit_event

_UNAUTHORIZED_RESPONSE = {"error": {"code": "401", "message": "Unauthorized"}}


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces API key authentication on all non-exempt paths.

    Exempt paths:
        - /health  — liveness probe; no auth required
        - /metrics — Prometheus scrape endpoint; no auth required

    On failure, emits an ``auth_fail`` audit event and returns HTTP 401.
    On success, emits an ``auth_pass`` audit event and forwards the request.
    """

    EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/metrics"})

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Skip auth for health/metrics probes (exact match or prefix for mounted sub-apps)
        path = request.url.path
        if path in self.EXEMPT_PATHS or path.startswith("/metrics"):
            return await call_next(request)

        settings = get_settings()

        # Resolve request_id — prefer one already set on request state
        request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())

        key = request.headers.get("X-Api-Key", "")

        if not key:
            emit_audit_event(
                build_audit_event(
                    request_id=request_id,
                    event_type="auth_fail",
                    method=request.method,
                    path=request.url.path,
                    status_code=401,
                    outcome="block",
                    reason="missing_header",
                )
            )
            return JSONResponse(status_code=401, content=_UNAUTHORIZED_RESPONSE)

        if key != settings.gateway_api_key:
            emit_audit_event(
                build_audit_event(
                    request_id=request_id,
                    event_type="auth_fail",
                    method=request.method,
                    path=request.url.path,
                    status_code=401,
                    outcome="block",
                    reason="key_mismatch",
                )
            )
            return JSONResponse(status_code=401, content=_UNAUTHORIZED_RESPONSE)

        # Key matches — record pass and continue
        emit_audit_event(
            build_audit_event(
                request_id=request_id,
                user_id="poc-user",
                event_type="auth_pass",
                method=request.method,
                path=request.url.path,
                outcome="pass",
            )
        )

        # Propagate resolved request_id to downstream handlers
        request.state.request_id = request_id

        return await call_next(request)
