"""
metrics.py — Prometheus metric definitions for the Security & Governance Layer.

Defines the three mandatory platform metrics via `make_layer_metrics("security")`,
plus two security-specific extra metrics (pii_entities_total, blocks_total).

Mandatory metrics (via shared factory):
  LAYER_METRICS.requests_total  — llm_security_requests_total{status, department, model}
  LAYER_METRICS.latency_seconds — llm_security_latency_seconds{department}
  LAYER_METRICS.errors_total    — llm_security_errors_total{error_code, department}

Extra security metrics (kept as separate prometheus_client objects):
  pii_entities_total — Counter tracking PII entities detected by entity type.
  blocks_total       — Counter tracking blocked requests by block reason.

This module is imported by routers/pre_check.py and routers/post_check.py.

Validates: Requirements 2.4–2.8, 6.1–6.6, 7.1–7.4
"""

from prometheus_client import Counter

from shared.observability.metrics import make_layer_metrics

# ---------------------------------------------------------------------------
# Mandatory platform metrics (contract label schema)
# ---------------------------------------------------------------------------
LAYER_METRICS = make_layer_metrics("security")

# Expose the three mandatory objects at module level for backward-compatible
# use in route handlers and tests that call the old `requests_total` / `latency` names.
requests_total = LAYER_METRICS.requests_total
latency = LAYER_METRICS.latency_seconds
errors_total = LAYER_METRICS.errors_total

# ---------------------------------------------------------------------------
# Extra security-specific metrics (kept alongside LAYER_METRICS)
# ---------------------------------------------------------------------------

# Count of PII entities detected, labelled by:
#   entity_type ∈ {EMAIL_ADDRESS, PHONE_NUMBER, PERSON, OTHER}
pii_entities_total = Counter(
    "llm_security_pii_entities_total",
    "Count of PII entities detected by entity type",
    labelnames=["entity_type"],
)

# Requests blocked at any pipeline stage, labelled by:
#   reason ∈ {injection_detected, content_safety_violation, policy_denied}
blocks_total = Counter(
    "llm_security_blocks_total",
    "Requests blocked at any pipeline stage by reason",
    labelnames=["reason"],
)
