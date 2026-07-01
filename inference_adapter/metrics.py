"""
Prometheus metrics for the Inference Adapter (Layer 5).

All three metric objects are registered at module import time.
This module has no I/O and is safe to import anywhere.

Infer router (routers/infer.py) imports and updates these metrics on every
request, labelling by model, task_type, department, status, and error_code.

Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 11.6
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Metric: total completed inference requests
# Labels:
#   status      — "success" | "error"
#   model       — routing.selected_model (or "" if absent)
#   task_type   — request.task_type (or "" if None)
#   department  — user.department (or "" if None)
# ---------------------------------------------------------------------------
llm_inference_requests_total = Counter(
    "llm_inference_requests_total",
    "Total completed inference requests",
    ["status", "model", "task_type", "department"],
)

# ---------------------------------------------------------------------------
# Metric: wall-clock latency from Ollama call dispatch to response receipt
# Labels:
#   model       — routing.selected_model (or "" if absent)
#   task_type   — request.task_type (or "" if None)
#   department  — user.department (or "" if None)
# Buckets: aligned to inference latency expectations (seconds)
# ---------------------------------------------------------------------------
llm_inference_latency_seconds = Histogram(
    "llm_inference_latency_seconds",
    "Wall-clock latency from Ollama call dispatch to response receipt",
    ["model", "task_type", "department"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
)

# ---------------------------------------------------------------------------
# Metric: count per failure type
# Labels:
#   error_code  — "ollama_unreachable" | "ollama_error_response" | "ollama_unparseable_body"
#   model       — routing.selected_model (or "" if absent)
#   department  — user.department (or "" if None)
# ---------------------------------------------------------------------------
llm_inference_errors_total = Counter(
    "llm_inference_errors_total",
    "Count per failure type",
    ["error_code", "model", "department"],
)
