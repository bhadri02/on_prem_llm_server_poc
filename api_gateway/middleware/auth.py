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

from api_gateway.services.audit import build_audit_event, emit_audit_event
from api_gateway.services.audit_client import schedule_audit_post
from api_gateway.services.key_resolver import KeyResolverUnavailable, resolve_key

_UNAUTHORIZED_RESPONSE = {"error": {"code": "401", "message": "Unauthorized"}}
_FORBIDDEN_RESPONSE = {"error": {"code": "403", "message": "Forbidden"}}
_IDENTITY_UNAVAILABLE_RESPONSE = {
    "error": {"code": "503", "message": "Identity service unavailable"}
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces API key authentication on all non-exempt paths.

    Exempt paths:
        - /health  — liveness probe; no auth required
        - /metrics — Prometheus scrape endpoint; no auth required

    Every non-exempt request resolves its API key against the Admin Portal
    (``api_gateway.services.key_resolver.resolve_key``) instead of comparing
    against a single shared secret. This is a server-side identity lookup —
    the caller's roles/entitlements are never trusted from the request
    payload (Phase 2 — RBAC + per-user API keys).

    The key may be sent as either ``X-Api-Key: <key>`` (this platform's
    original header) or the standard ``Authorization: Bearer <key>`` header
    that virtually every OpenAI-compatible client/SDK sends by default
    (added to support external tools like Continue.dev without requiring
    them to special-case a non-standard header). ``X-Api-Key`` is checked
    first for backward compatibility; both resolve identically otherwise.

    On failure, emits an ``auth_fail`` audit event and returns HTTP
    401 (key not found/revoked/expired), 403 (no active roles), or
    503 (Admin Portal unreachable — fails closed).
    On success, emits an ``auth_pass`` audit event, stashes the resolved
    profile on ``request.state.user_profile``, and forwards the request.
    """

    EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/metrics","/docs","/openapi.json"})

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Skip auth for health/metrics probes (exact match or prefix for mounted sub-apps)
        path = request.url.path
        if path in self.EXEMPT_PATHS or path.startswith("/metrics"):
            return await call_next(request)

        # Resolve request_id — prefer one already set on request state
        request_id: str = getattr(request.state, "request_id", None) or str(uuid.uuid4())

        http_client = request.app.state.http_client

        key = request.headers.get("X-Api-Key", "")
        if not key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                key = auth_header[len("Bearer "):].strip()

        if not key:
            event = build_audit_event(
                request_id=request_id,
                event_type="auth_fail",
                method=request.method,
                path=request.url.path,
                status_code=401,
                outcome="block",
                reason="missing_header",
            )
            emit_audit_event(event)
            response = JSONResponse(status_code=401, content=_UNAUTHORIZED_RESPONSE)
            schedule_audit_post(response, event, http_client)
            return response

        try:
            profile = await resolve_key(key, http_client)
        except KeyResolverUnavailable:
            event = build_audit_event(
                request_id=request_id,
                event_type="auth_fail",
                method=request.method,
                path=request.url.path,
                status_code=503,
                outcome="block",
                reason="identity_service_unavailable",
            )
            emit_audit_event(event)
            response = JSONResponse(status_code=503, content=_IDENTITY_UNAVAILABLE_RESPONSE)
            schedule_audit_post(response, event, http_client)
            return response

        if profile is None:
            event = build_audit_event(
                request_id=request_id,
                event_type="auth_fail",
                method=request.method,
                path=request.url.path,
                status_code=401,
                outcome="block",
                reason="key_not_found",
            )
            emit_audit_event(event)
            response = JSONResponse(status_code=401, content=_UNAUTHORIZED_RESPONSE)
            schedule_audit_post(response, event, http_client)
            return response

        if not profile.roles:
            event = build_audit_event(
                request_id=request_id,
                user_id=profile.user_id,
                event_type="auth_fail",
                method=request.method,
                path=request.url.path,
                status_code=403,
                outcome="block",
                reason="no_active_roles",
            )
            emit_audit_event(event)
            response = JSONResponse(status_code=403, content=_FORBIDDEN_RESPONSE)
            schedule_audit_post(response, event, http_client)
            return response

        # Key resolved to an active identity with at least one role — record
        # pass and continue.
        pass_event = build_audit_event(
            request_id=request_id,
            user_id=profile.user_id,
            event_type="auth_pass",
            method=request.method,
            path=request.url.path,
            outcome="pass",
        )
        emit_audit_event(pass_event)

        # Propagate resolved request_id and identity to downstream handlers
        request.state.request_id = request_id
        request.state.user_profile = profile

        response = await call_next(request)
        schedule_audit_post(response, pass_event, http_client)
        return response
