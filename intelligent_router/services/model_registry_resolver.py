"""
intelligent_router/services/model_registry_resolver.py

TTL-cached fetch of the live model catalog from model_registry — same
pattern as services/policy_resolver.py, but merging into ModelMatrix.models
instead of the policy matrix. Closes the long-standing gap where
registering a model via POST /portal/models never made it routable (see
CLAUDE.md): any model_registry record with status="active" now becomes
selectable — by pinning (request.model = <name>) or via a user's model
entitlements — within model_registry_cache_ttl_seconds, no
model_matrix.yaml edit or Router restart required.

task_defaults (which model auto-routing picks for a given task_type) is
deliberately NOT derived from the registry — "the default model for task X"
is a routing policy decision, not a fact about a model, and the registry has
no field for it. task_defaults always comes from the static
model_matrix.yaml seed.

Calls model_registry's public, unauthenticated GET /models/ directly
(no REGISTRY_API_KEY needed — see model_registry/middleware/auth.py's
_requires_auth, which only guards the mutating/secret endpoints).

Never raises — on any failure (model_registry unreachable, bad response),
falls back to the last known-good merged catalog, or the static
model_matrix.yaml models if nothing has ever been fetched successfully, so
a soft-dependency hiccup never fails requests closed.
"""

from __future__ import annotations

import time
from typing import Any

from intelligent_router.logging_config import get_logger
from intelligent_router.model_selector import ModelEntry, ModelMatrix

logger = get_logger(__name__)

# Module-level cache — deliberately not per-request-scoped; the whole point
# is to avoid a network round-trip on every request.
_cache: dict[str, Any] = {"models": None, "fetched_at": 0.0}


def _health_url(backend: str, endpoint: str) -> str:
    """Best-effort health probe URL — model_registry doesn't store one.

    Mirrors model_matrix.yaml's own convention for Ollama models. Unused for
    non-Ollama backends (pipeline.py's Stage 3 skips the live probe for
    those and assumes healthy), so the exact value doesn't matter there.
    """
    if backend == "ollama":
        return f"{endpoint.rstrip('/')}/api/tags"
    return endpoint


def _to_model_entry(record: dict) -> ModelEntry | None:
    """Convert one model_registry list-response record into a ModelEntry.

    Returns None (and logs) for non-active records or records missing a
    required field — a malformed/incomplete registry entry should never
    take down the whole merge.
    """
    if record.get("status") != "active":
        return None
    try:
        return ModelEntry(
            name=record["name"],
            backend=record["backend"],
            endpoint=record["endpoint"],
            tasks=list(record.get("tasks") or []),
            health_url=_health_url(record["backend"], record["endpoint"]),
            fallback=record.get("fallback_model"),
        )
    except KeyError as exc:
        logger.warning(f"model_registry_record_skipped: missing field {exc}")
        return None


async def get_model_matrix(state) -> ModelMatrix:
    """Return the freshest available ModelMatrix.

    ``models`` is the static model_matrix.yaml seed overlaid with active
    entries fetched live from model_registry (registry entries win on a name
    collision, so re-registering an existing model with new metadata takes
    effect). ``task_defaults`` always comes from the static YAML seed.

    Args:
        state: FastAPI app.state — provides settings, http_client, and
               model_matrix (the static YAML-loaded fallback from startup).
    """
    # Everything (including the TTL arithmetic) is inside this try block,
    # not just the network call — test fixtures across this codebase often
    # build app.state with a plain MagicMock() for `settings`/`http_client`
    # (no real numeric model_registry_cache_ttl_seconds), which would raise
    # a TypeError on the comparison below, not just on the HTTP call. Any
    # failure at any stage falls back to the same safe default.
    try:
        now = time.monotonic()
        ttl = state.settings.model_registry_cache_ttl_seconds

        if (now - _cache["fetched_at"]) < ttl and _cache["models"] is not None:
            return _merged(state, _cache["models"])

        # Mark the attempt now — success or failure — so a persistent
        # model_registry outage backs off for a full TTL window instead of
        # retrying on every single request until it recovers.
        _cache["fetched_at"] = now

        resp = await state.http_client.get(
            f"{state.settings.model_registry_url}/models/",
            timeout=5.0,
        )
        resp.raise_for_status()
        records = resp.json()
        if not isinstance(records, list):
            raise ValueError("model_registry list response was not a list")

        entries: dict[str, ModelEntry] = {}
        for record in records:
            entry = _to_model_entry(record)
            if entry is not None:
                entries[entry.name] = entry

        _cache["models"] = entries
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"model_registry_refresh_failed: falling back to last known-good catalog: {exc}"
        )
        # _cache["models"] is left as-is (last known-good, or None on a
        # first-ever failure) — _merged() below falls back to the static
        # YAML seed alone when _cache["models"] is still None.

    return _merged(state, _cache["models"])


def _merged(state, registry_models: dict[str, ModelEntry] | None) -> ModelMatrix:
    models = {**state.model_matrix.models, **(registry_models or {})}
    return ModelMatrix(models=models, task_defaults=state.model_matrix.task_defaults)


def reset_cache() -> None:
    """Test-only helper — clears the module-level cache between test cases."""
    _cache["models"] = None
    _cache["fetched_at"] = 0.0
