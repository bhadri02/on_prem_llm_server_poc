"""
metrics.py — Prometheus metric definitions for the Security & Governance Layer.

Defines four module-level metrics that are registered in the default Prometheus
registry at import time:

- ``requests_total``    — Counter tracking pre-generation pipeline requests by
                          outcome and terminating check stage.
- ``latency``           — Histogram tracking handler latency for pre_check and
                          post_check endpoints.
- ``pii_entities_total`` — Counter tracking PII entities detected by entity type.
- ``blocks_total``      — Counter tracking blocked requests by block reason.

This module is imported by ``metrics_app.py`` (to ensure registration before
``make_asgi_app()`` is called) and by the route handlers in
``routers/pre_check.py`` and ``routers/post_check.py``.
"""

from prometheus_client import Counter, Histogram

# Total pre-generation pipeline requests, labelled by:
#   outcome ∈ {pass, block, error}
#   check   ∈ {injection, content_safety, policy, full_pipeline}
requests_total = Counter(
    "llm_security_requests_total",
    "Total pre-generation pipeline requests by outcome and terminating check",
    labelnames=["outcome", "check"],
)

# Handler latency from entry to response return, labelled by:
#   endpoint ∈ {pre_check, post_check}
latency = Histogram(
    "llm_security_latency_seconds",
    "Handler latency from entry to response return",
    labelnames=["endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

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
