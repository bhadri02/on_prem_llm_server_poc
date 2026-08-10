from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MetricsSummary(BaseModel):
    request_rate: Optional[float] = None   # requests/sec; None if no data
    error_rate: Optional[float] = None     # fraction 0.0–1.0; None if denominator = 0
    cache_hit_rate: Optional[float] = None # fraction 0.0–1.0; None if denominator = 0
    active_users: Optional[int] = None     # COUNT(users WHERE status='active'); None if the DB is unreachable
