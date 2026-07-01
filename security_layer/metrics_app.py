"""
Separate lightweight ASGI application serving Prometheus metrics on port 9090.

This app is intentionally independent of the main FastAPI app:
- No auth middleware — the /metrics endpoint is unauthenticated
- No shared lifespan with main.py
- No app.state access or FastAPI dependency injection
- Started separately via:
    uvicorn security_layer.metrics_app:metrics_app --host 0.0.0.0 --port 9090
"""

from starlette.applications import Starlette
from starlette.routing import Mount
from prometheus_client import make_asgi_app

# Import metrics module to ensure all four Counters/Histograms defined there
# (requests_total, latency, pii_entities_total, blocks_total) are registered
# in the default Prometheus registry before make_asgi_app() is called.
import security_layer.metrics  # noqa: F401

metrics_app = Starlette(
    routes=[
        Mount("/metrics", make_asgi_app()),
    ]
)
