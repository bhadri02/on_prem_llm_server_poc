"""
Prometheus metrics definitions for the API Gateway (Layer 1).

All metric objects are registered at module import time via the shared
observability module. This module has no I/O and is safe to import anywhere.

PrometheusMiddleware (middleware/prometheus.py) records metrics using the
LayerMetrics interface.

Metric names follow the platform convention: llm_api_gateway_<metric>.

Validates: Requirements 2.1, 2.2, 2.3, 2.19, 2.20
"""

from __future__ import annotations

from shared.observability.metrics import make_layer_metrics

# ---------------------------------------------------------------------------
# Create the three mandatory metric families for the api_gateway layer.
# 
# This replaces the ad-hoc Counter and Histogram definitions with a single
# factory call that enforces the contract label schema:
#   - requests_total: labels [status, department, model]
#   - latency_seconds: label [department], buckets [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
#   - errors_total: labels [error_code, department]
# ---------------------------------------------------------------------------
LAYER_METRICS = make_layer_metrics("api_gateway")
