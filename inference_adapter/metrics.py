"""
Prometheus metrics for the Inference Adapter (Layer 5).

All three mandatory metric families are registered at module import time via
`make_layer_metrics("inference")`. This module has no I/O and is safe to
import anywhere.

Mandatory metrics (via shared factory):
  LAYER_METRICS.requests_total  — llm_inference_requests_total{status, department, model}
  LAYER_METRICS.latency_seconds — llm_inference_latency_seconds{department}
  LAYER_METRICS.errors_total    — llm_inference_errors_total{error_code, department}

The infer router (routers/infer.py) records metrics on every request via
`LAYER_METRICS.record_request()` and `LAYER_METRICS.record_error()`.

Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 11.6
"""

from __future__ import annotations

from shared.observability.metrics import make_layer_metrics

# ---------------------------------------------------------------------------
# Create the three mandatory metric families for the inference layer.
# ---------------------------------------------------------------------------
LAYER_METRICS = make_layer_metrics("inference")
