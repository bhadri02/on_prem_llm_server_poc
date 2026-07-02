"""
Prometheus metric definitions for the Intelligent Router.

This module is a pure definitions module — no functions, just the five
module-level metric objects. Import this module before calling
prometheus_client.make_asgi_app() to ensure all metrics are registered
in the default registry.

Metrics:
  - requests_total   : Counter  — routing requests by outcome, task type, routing mode
  - latency          : Histogram — end-to-end pipeline latency in seconds
  - cache_hits_total : Counter  — cache hits by task type and model
  - fallbacks_total  : Counter  — fallback events by task type and reason
  - errors_total     : Counter  — errors by error code

Label value constraints (documented; not enforced at definition time):
  outcome    ∈ {"cache_hit", "inference_success", "fallback_success", "error"}
  reason     ∈ {"health_check_failed", "inference_error"}
  error_code ∈ {"governance_check_failed", "all_backends_exhausted",
                "invalid_pinned_model", "internal_error"}
"""

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# 12.1 — Total routing requests by outcome, task type, and routing mode
# ---------------------------------------------------------------------------
requests_total = Counter(
    "llm_router_requests_total",
    "Total routing requests by outcome, task type, and routing mode",
    labelnames=["outcome", "task_type", "routing_mode"],
)

# ---------------------------------------------------------------------------
# 12.2 — End-to-end routing pipeline latency
# ---------------------------------------------------------------------------
latency = Histogram(
    "llm_router_latency_seconds",
    "End-to-end routing pipeline latency in seconds",
    labelnames=["task_type", "routing_mode"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 120.0],
)

# ---------------------------------------------------------------------------
# 12.3 — Total cache hits by task type and model
# ---------------------------------------------------------------------------
cache_hits_total = Counter(
    "llm_router_cache_hits_total",
    "Total cache hits by task type and model",
    labelnames=["task_type", "model"],
)

# ---------------------------------------------------------------------------
# 12.4 — Total fallback events by task type and reason
# ---------------------------------------------------------------------------
fallbacks_total = Counter(
    "llm_router_fallbacks_total",
    "Total fallback events by task type and reason",
    labelnames=["task_type", "reason"],
)

# ---------------------------------------------------------------------------
# 12.5 — Total errors by error code
# ---------------------------------------------------------------------------
errors_total = Counter(
    "llm_router_errors_total",
    "Total errors by error code",
    labelnames=["error_code"],
)
