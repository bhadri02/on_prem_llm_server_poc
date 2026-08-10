"""
model_secret_resolver — fetches a cloud-backend model's provider API key
from the Model Registry, with a short in-process TTL cache.

Only called for models whose routing.backend != "ollama" (stamped onto the
IMF by the Router — see intelligent_router/pipeline.py Stage 3). The Ollama
path never touches this module or the Model Registry at all, so the common
case has zero added network I/O.
"""

from __future__ import annotations

import time

import httpx

from inference_adapter.config import Settings


class ModelSecretUnavailable(Exception):
    """Raised when the Model Registry is unreachable or returns something
    the resolver can't interpret. Callers should treat this as a dispatch
    failure (503), not "no key configured" (422) — those are distinct
    problems with distinct remediation."""


# In-process cache: model_name -> (expiry_monotonic, api_key_or_none)
_cache: dict[str, tuple[float, str | None]] = {}


def _cache_get(model_name: str) -> tuple[bool, str | None]:
    entry = _cache.get(model_name)
    if entry is None:
        return False, None
    expiry, value = entry
    if time.monotonic() >= expiry:
        _cache.pop(model_name, None)
        return False, None
    return True, value


def _cache_put(model_name: str, value: str | None, ttl_seconds: float) -> None:
    _cache[model_name] = (time.monotonic() + ttl_seconds, value)


async def resolve_api_key(
    model_name: str,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> str | None:
    """Return the provider API key on file for *model_name*, or ``None`` if
    the Model Registry has no key set for it.

    Raises:
        ModelSecretUnavailable: if the Model Registry cannot be reached or
            returns an unexpected response.
    """
    hit, cached = _cache_get(model_name)
    if hit:
        return cached

    url = f"{settings.model_registry_url}/models/{model_name}/secret"
    try:
        response = await http_client.get(
            url,
            headers={"X-API-Key": settings.registry_api_key},
            timeout=5.0,
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as exc:
        raise ModelSecretUnavailable(str(exc)) from exc

    if response.status_code == 404:
        # Model isn't registered at all — no key to resolve.
        _cache_put(model_name, None, settings.model_backend_cache_ttl_seconds)
        return None

    if response.status_code != 200:
        raise ModelSecretUnavailable(f"unexpected status {response.status_code}")

    try:
        api_key = response.json().get("api_key")
    except Exception as exc:
        raise ModelSecretUnavailable("invalid secret response body") from exc

    _cache_put(model_name, api_key, settings.model_backend_cache_ttl_seconds)
    return api_key
