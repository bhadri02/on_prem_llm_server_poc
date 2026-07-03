"""
Prometheus metric definitions for the Intelligent Router (Layer 3).

Provides the three mandatory platform metrics via `make_layer_metrics("router")`,
plus two router-specific extra metrics (cache_hits_total, fallbacks_total).

Mandatory metrics (via shared factory):
  LAYER_METRICS.requests_total  — llm_router_requests_total{status, department, model}
  LAYER_METRICS.latency_seconds — llm_router_latency_seconds{department}
  LAYER_METRICS.errors_total    — llm_router_errors_total{error_code, department}

Extra router metrics (kept as separate prometheus_client objects):
  cache_hits_total — Counter for cache hits by task type and model.
  fallbacks_total  — Counter for fallback events by task type and reason.

Backward-compatible module-level aliases:
  requests_total   — alias for LAYER_METRICS.requests_total
  latency          — alias for LAYER_METRICS.latency_seconds
  errors_total     — alias for LAYER_METRICS.errors_total

Validates: Requirements 2.9–2.12
"""

from prometheus_client import Counter

from shared.observability.metrics import make_layer_metrics

# ---------------------------------------------------------------------------
# Mandatory platform metrics (contract label schema)
# ---------------------------------------------------------------------------
LAYER_METRICS = make_layer_metrics("router")

# Backward-compatible module-level aliases (used by conftest.py fixture)
requests_total = LAYER_METRICS.requests_total
latency = LAYER_METRICS.latency_seconds
errors_total = LAYER_METRICS.errors_total

# ---------------------------------------------------------------------------
# Extra router-specific metrics (kept alongside LAYER_METRICS)
# ---------------------------------------------------------------------------

# 12.3 — Total cache hits by task type and model
cache_hits_total = Counter(
    "llm_router_cache_hits_total",
    "Total cache hits by task type and model",
    labelnames=["task_type", "model"],
)

# 12.4 — Total fallback events by task type and reason
fallbacks_total = Counter(
    "llm_router_fallbacks_total",
    "Total fallback events by task type and reason",
    labelnames=["task_type", "reason"],
)
