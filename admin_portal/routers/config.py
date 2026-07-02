"""
admin_portal/routers/config.py

Portal configuration router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
GET /config
    Returns a ``PortalConfig`` JSON object so the Portal_UI can resolve the
    Grafana embed URL at runtime without hardcoding it in the frontend bundle.

    ``grafana_url`` is populated from the ``GRAFANA_URL`` environment variable
    (default ``http://grafana:3000`` if the env var is absent — the default is
    declared in ``admin_portal/config.py``).

No authentication is required for this endpoint.

Validates: Requirements 9.3, 9.4
"""

from __future__ import annotations

from fastapi import APIRouter

from admin_portal.config import settings
from admin_portal.schemas.config import PortalConfig

router = APIRouter(tags=["config"])


@router.get(
    "/config",
    response_model=PortalConfig,
    summary="Portal runtime configuration",
    description=(
        "Returns portal configuration values needed by the UI, "
        "including the Grafana base URL for the dashboard embed."
    ),
)
async def get_config() -> PortalConfig:
    """Return portal runtime configuration.

    The ``grafana_url`` field defaults to ``http://grafana:3000`` when the
    ``GRAFANA_URL`` environment variable is not set (Req 9.3).
    """
    return PortalConfig(grafana_url=settings.GRAFANA_URL)
