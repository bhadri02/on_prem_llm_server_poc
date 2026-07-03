"""
Prometheus metrics for the Cache Service (Layer 4).

Provides the three mandatory platform metrics via `make_layer_metrics("cache")`,
plus one cache-specific extra metric (llm_cache_semantic_entries).

The `cache` layer's `requests_total` metric has an additional `outcome` label
(`hit`|`miss`) on top of the contract schema — `make_layer_metrics("cache")`
handles this special case internally and registers the metric as:
  llm_cache_requests_total{status, department, model, outcome}

Mandatory metrics (via shared factory):
  LAYER_METRICS.requests_total  — llm_cache_requests_total{status, department, model, outcome}
  LAYER_METRICS.latency_seconds — llm_cache_latency_seconds{department}
  LAYER_METRICS.errors_total    — llm_cache_errors_total{error_code, department}

Extra cache metric (kept as separate prometheus_client object):
  llm_cache_semantic_entries — Gauge tracking current semantic cache list length per task_type.

When calling LAYER_METRICS.record_request(), pass the `outcome` kwarg:
  LAYER_METRICS.record_request(status=..., department=..., model=..., latency_s=..., outcome="hit"|"miss")

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

from prometheus_client import Gauge

from shared.observability.metrics import make_layer_metrics

# ---------------------------------------------------------------------------
# Mandatory platform metrics (contract label schema + cache-specific `outcome`)
# ---------------------------------------------------------------------------
LAYER_METRICS = make_layer_metrics("cache")

# ---------------------------------------------------------------------------
# Extra cache-specific metric (kept alongside LAYER_METRICS)
# ---------------------------------------------------------------------------

# 9.5 — Current semantic cache list length per task_type (Gauge)
llm_cache_semantic_entries = Gauge(
    "llm_cache_semantic_entries",
    "Current number of entries in the semantic cache list per task_type.",
    ["task_type"],
)
