"""
Separate lightweight ASGI application serving Prometheus metrics on port 9090.

This app is intentionally independent of the main FastAPI app:
- No auth middleware — the /metrics endpoint is unauthenticated
- No shared lifespan with main.py
- No access to app.state (http_client, classifier_rules, model_matrix, settings)
- Started separately via:
    uvicorn intelligent_router.metrics_app:metrics_app --host 0.0.0.0 --port 9090
"""

from starlette.applications import Starlette
from starlette.routing import Mount
from prometheus_client import make_asgi_app

# Import the metrics module to ensure all five Counters and Histograms defined
# there are registered in the default Prometheus registry before make_asgi_app()
# is called and before any scrape request arrives.
import intelligent_router.metrics  # noqa: F401

metrics_app = Starlette(
    routes=[
        Mount("/metrics", make_asgi_app()),
    ]
)
