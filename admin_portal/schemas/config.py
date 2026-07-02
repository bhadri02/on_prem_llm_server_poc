from __future__ import annotations

from pydantic import BaseModel


class PortalConfig(BaseModel):
    grafana_url: str  # value of GRAFANA_URL env var (default: http://grafana:3000)
