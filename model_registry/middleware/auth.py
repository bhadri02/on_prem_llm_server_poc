"""
Authentication middleware for the Model Registry.

Implements AuthMiddleware(BaseHTTPMiddleware) that enforces X-API-Key header
validation on mutating endpoints (POST /models, PATCH /models/{name}/status).
Uses hmac.compare_digest for constant-time comparison to prevent timing attacks.
GET endpoints and /health are always passed through without an auth check.
If REGISTRY_API_KEY is unset/empty, enforcement is disabled (POC convenience mode).
"""

import hmac
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from model_registry.config import get_settings


def _requires_auth(method: str, path: str) -> bool:
    """Return True if this method + path combination requires API key auth.

    Protected routes:
    - POST /models
    - PATCH /models/{name}/status  (any non-empty {name} segment)
    """
    if method == "POST" and path == "/models":
        return True
    if method == "PATCH" and re.match(r"^/models/[^/]+/status$", path):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces X-API-Key on mutating endpoints.

    Behaviour:
    - Requests to non-protected routes are passed through unconditionally.
    - If REGISTRY_API_KEY is empty/unset, enforcement is skipped (POC
      convenience mode — Req 8.2).
    - When a key is configured, the X-API-Key request header is compared
      against it using hmac.compare_digest to prevent timing attacks.
      A mismatch results in HTTP 401 before FastAPI's own route matching
      runs, so the 401 is returned even for syntactically invalid bodies.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()

        if _requires_auth(request.method, request.url.path):
            if not settings.registry_api_key:
                # Key not configured — POC convenience mode, pass through
                pass
            else:
                client_key = request.headers.get("X-API-Key", "")
                expected = settings.registry_api_key
                if not hmac.compare_digest(
                    client_key.encode(), expected.encode()
                ):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing X-API-Key header."},
                    )

        return await call_next(request)
