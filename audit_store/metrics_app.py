"""
Separate lightweight ASGI application serving Prometheus metrics on port 9090.

This app is intentionally independent of the main FastAPI app:
- No APIKeyMiddleware — the /metrics endpoint is unauthenticated
- No shared lifespan with main.py
- Started separately via:
    uvicorn audit_store.metrics_app:metrics_app --host 0.0.0.0 --port 9090
"""

from starlette.applications import Starlette
from starlette.routing import Mount
from prometheus_client import make_asgi_app

# Import metrics module to ensure the Counter and Histogram defined there
# are registered in the default Prometheus registry before any scrape occurs.
import audit_store.metrics  # noqa: F401

metrics_app = Starlette(
    routes=[
        Mount("/metrics", make_asgi_app()),
    ]
)
