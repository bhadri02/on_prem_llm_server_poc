from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MetricsSummary(BaseModel):
    request_rate: Optional[float] = None   # requests/sec; None if no data
    error_rate: Optional[float] = None     # fraction 0.0–1.0; None if denominator = 0
    cache_hit_rate: Optional[float] = None # fraction 0.0–1.0; None if denominator = 0
    active_users: Optional[int] = None     # COUNT(users WHERE status='active'); None if the DB is unreachable

    # --- AI governance / security metrics (Phase 7) ---
    # Requests blocked by security_layer for ANY reason (injection_detected,
    # content_safety_violation, policy_denied) — rate(llm_security_blocks_total[60s]), summed across reasons.
    blocked_requests_rate: Optional[float] = None
    # Requests denied by intelligent_router's fine-grained (role, task_type)
    # policy matrix specifically — rate(llm_router_errors_total{error_code="policy_denied"}[60s]).
    policy_denied_rate: Optional[float] = None
    # Requests denied for pinning/using a model outside the caller's
    # entitlements — rate(llm_router_errors_total{error_code="model_not_entitled"}[60s]).
    model_not_entitled_rate: Optional[float] = None
    # PII entities detected (and masked) in requests/responses per second —
    # rate(llm_security_pii_entities_total[60s]), summed across entity types.
    pii_detections_rate: Optional[float] = None
    # Total LLM tokens (prompt + completion) consumed per second across all
    # models/tasks — rate(llm_router_tokens_total[60s]), summed.
    tokens_per_second: Optional[float] = None
