from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces X-API-Key authentication on POST write endpoints.

    Write paths require a valid X-API-Key header. All GET endpoints and any
    path not in WRITE_PATHS bypass authentication unconditionally.
    """

    WRITE_PATHS = {"/audit/events", "/audit/events/batch"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.WRITE_PATHS and request.method == "POST":
            key = request.headers.get("X-API-Key")
            if not key:
                return JSONResponse(
                    status_code=401,
                    content={"error": "missing_api_key"},
                )
            if key != request.app.state.settings.audit_api_key:
                return JSONResponse(
                    status_code=403,
                    content={"error": "invalid_api_key"},
                )
        return await call_next(request)
