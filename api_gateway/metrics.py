"""
Prometheus metrics definitions for the API Gateway (Layer 1).

All metric objects are registered at module import time.
This module has no I/O and is safe to import anywhere.

PrometheusMiddleware (middleware/prometheus.py) increments these on every
completed request, labelled by status_code, path, and error_code.

Metric names follow the platform convention: llm_api_gateway_<metric>.

Validates: Requirements 10.1, 10.2, 10.3, 10.4
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Metric: total completed requests (excluding /metrics and /health)
# Labels:
#   status_code — HTTP status code string, e.g. "200", "401", "429", "502"
#   path        — route template, e.g. "/v1/chat/completions" (not raw URL)
# ---------------------------------------------------------------------------
REQUESTS_TOTAL = Counter(
    "llm_api_gateway_requests_total",
    "Total completed requests handled by the API Gateway",
    ["status_code", "path"],
)

# ---------------------------------------------------------------------------
# Metric: total error responses (4xx and 5xx, excluding /metrics and /health)
# Labels:
#   error_code  — HTTP status code string, e.g. "401", "429", "502", "500"
# ---------------------------------------------------------------------------
ERRORS_TOTAL = Counter(
    "llm_api_gateway_errors_total",
    "Total error responses (4xx and 5xx) returned by the API Gateway",
    ["error_code"],
)

# ---------------------------------------------------------------------------
# Metric: end-to-end request latency
# Labels:
#   path        — route template, e.g. "/v1/chat/completions" (not raw URL)
# Buckets: default prometheus_client buckets for POC
# ---------------------------------------------------------------------------
LATENCY_SECONDS = Histogram(
    "llm_api_gateway_latency_seconds",
    "End-to-end request latency from first byte received to last byte sent",
    ["path"],
)
