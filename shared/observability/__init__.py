"""
shared.observability — cross-cutting instrumentation package.

Exposes the public API for metrics, logging, and middleware that every
platform layer imports instead of defining ad-hoc instrumentation.

Populated progressively by tasks 2–5; only symbols that have been implemented
are imported here — stubs for logging and middleware are left as forward
declarations so downstream imports don't break before those tasks are done.
"""

from __future__ import annotations

# ── Task 2: metrics ──────────────────────────────────────────────────────────
from shared.observability.metrics import (  # noqa: F401
    VALID_LAYERS,
    LayerMetrics,
    make_layer_metrics,
    validate_scrape_interval,
)

# ── Task 3: logging ───────────────────────────────────────────────────────────
from shared.observability.logging import (  # noqa: F401
    configure_structlog,
    get_logger,
    emit,
)

# ── Task 5 / 14: middleware ───────────────────────────────────────────────────
from shared.observability.middleware import (  # noqa: F401
    LoggingMiddleware,
    PrometheusMiddleware,
    configure_tracing,
)

__all__ = [
    # metrics.py (task 2)
    "LayerMetrics",
    "make_layer_metrics",
    "validate_scrape_interval",
    "VALID_LAYERS",
    # logging.py (task 3) — implemented in task 3.1
    "configure_structlog",
    "get_logger",
    "emit",
    # middleware.py (task 5 / 14) — implemented in task 5.1 / 14.1
    "LoggingMiddleware",
    "PrometheusMiddleware",
    "configure_tracing",
]
