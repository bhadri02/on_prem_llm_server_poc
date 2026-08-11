from __future__ import annotations

from typing import Dict

from pydantic import BaseModel


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class GovernanceSummary(BaseModel):
    total_events: int
    by_outcome: Dict[str, int]
    by_layer: Dict[str, int]
    requests_blocked_total: int
    blocked_by_reason: Dict[str, int]
    injection_flagged_total: int
    pii_detections_total: int
    token_usage: TokenUsage
    model_usage: Dict[str, int]
