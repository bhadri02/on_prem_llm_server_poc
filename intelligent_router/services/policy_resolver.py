"""
intelligent_router/services/policy_resolver.py

TTL-cached fetch of the live (role, task_type) policy matrix from
admin_portal — same pattern as api_gateway/services/key_resolver.py's
per-key cache, just for the whole matrix at once (small: a handful of roles
x 5 task types), since it's checked on every single request through
pipeline.py's Stage 2b.

Makes PATCH /portal/roles/{role}/permissions changes take effect within
settings.policy_cache_ttl_seconds instead of requiring a policy_matrix.yaml
edit + Router restart. Never raises — on any failure (admin_portal
unreachable, bad response), falls back to the last known-good matrix, or
the static YAML-loaded seed if nothing has ever been fetched successfully,
so a soft-dependency hiccup never fails requests closed.
"""

from __future__ import annotations

import time
from typing import Any

from intelligent_router.logging_config import get_logger
from intelligent_router.policy import PolicyMatrix

logger = get_logger(__name__)

# Module-level cache — deliberately not per-request-scoped; the whole point
# is to avoid a network round-trip on every request.
_cache: dict[str, Any] = {"matrix": None, "fetched_at": 0.0}


async def get_policy_matrix(state) -> PolicyMatrix:
    """Return the freshest available PolicyMatrix, refreshing from
    admin_portal if the cache is stale.

    Args:
        state: FastAPI app.state — provides settings, http_client, and
               policy_matrix (the static YAML-loaded fallback from startup).
    """
    # Everything (including the TTL arithmetic) is inside this try block,
    # not just the network call — test fixtures across this codebase often
    # build app.state with a plain MagicMock() for `settings`/`http_client`
    # (no real numeric policy_cache_ttl_seconds), which would raise a
    # TypeError on the comparison below, not just on the HTTP call. Any
    # failure at any stage falls back to the same safe default.
    try:
        now = time.monotonic()
        ttl = state.settings.policy_cache_ttl_seconds

        if (now - _cache["fetched_at"]) < ttl:
            return _cache["matrix"] or state.policy_matrix

        # Mark the attempt now — success or failure — so a persistent
        # admin_portal outage backs off for a full TTL window instead of
        # retrying on every single request until it recovers.
        _cache["fetched_at"] = now

        resp = await state.http_client.get(
            f"{state.settings.admin_portal_url}/portal/policy/matrix",
            headers={"X-Portal-Internal-Key": state.settings.admin_portal_internal_key},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data:
            raise ValueError("empty or malformed policy matrix response")

        _cache["matrix"] = PolicyMatrix(roles=data)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"policy_matrix_refresh_failed: falling back to last known-good matrix: {exc}")
        # _cache["matrix"] is left as-is (last known-good, or None on a
        # first-ever failure) — fall through to the static YAML seed below.

    return _cache["matrix"] or state.policy_matrix


def reset_cache() -> None:
    """Test-only helper — clears the module-level cache between test cases."""
    _cache["matrix"] = None
    _cache["fetched_at"] = 0.0
