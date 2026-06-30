"""
Prometheus metrics for the Cache Service (Layer 4).

All four metric objects are defined here and imported by the cache router.
A dedicated metrics ASGI app is mounted on port 9090 by main.py (Task 12).

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Metric: total lookup requests
# Labels: status (hit | miss), cache_type (exact | semantic | none), task_type
# ---------------------------------------------------------------------------
llm_cache_requests_total = Counter(
    "llm_cache_requests_total",
    "Total number of cache lookup requests.",
    ["status", "cache_type", "task_type"],
)

# ---------------------------------------------------------------------------
# Metric: end-to-end handler latency
# Labels: operation (lookup | write), task_type
# Buckets: per design doc
# ---------------------------------------------------------------------------
llm_cache_latency_seconds = Histogram(
    "llm_cache_latency_seconds",
    "End-to-end cache handler latency in seconds.",
    ["operation", "task_type"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

# ---------------------------------------------------------------------------
# Metric: Redis / embedding error counter
# Labels: error_code, operation
# ---------------------------------------------------------------------------
llm_cache_errors_total = Counter(
    "llm_cache_errors_total",
    "Total number of cache errors (Redis or embedding failures).",
    ["error_code", "operation"],
)

# ---------------------------------------------------------------------------
# Metric: current semantic cache list length per task_type
# Labels: task_type
# ---------------------------------------------------------------------------
llm_cache_semantic_entries = Gauge(
    "llm_cache_semantic_entries",
    "Current number of entries in the semantic cache list per task_type.",
    ["task_type"],
)
