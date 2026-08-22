"""
router_client.py — Downstream Router client for the Security & Governance Layer.

Forwards the enriched IMF to the Intelligent Router via HTTP POST and relays
the response back to the caller.  Three typed exceptions signal the distinct
failure modes callers need to handle separately.
"""

import json
from typing import AsyncIterator

import httpx

from security_layer.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------

#: Default read/write timeout (seconds) when the caller doesn't specify one —
#: kept for backward compatibility with callers/tests that don't pass
#: timeout_seconds. Real traffic should pass settings.router_timeout_seconds
#: instead (see routers/pre_check.py) — CPU-only Ollama inference can
#: legitimately take well over 30s, especially with multiple models loaded.
_DEFAULT_READ_TIMEOUT_SECONDS = 30.0


def _build_timeout(read_seconds: float) -> httpx.Timeout:
    """connect/pool stay short (5s) — those are about *reachability*, not
    processing time. read/write scale with the caller-supplied budget."""
    return httpx.Timeout(connect=5.0, read=read_seconds, write=read_seconds, pool=5.0)


# ---------------------------------------------------------------------------
# Typed exception hierarchy
# ---------------------------------------------------------------------------


class RouterTimeoutError(Exception):
    """Raised when the Router does not respond within the configured timeout.

    Corresponds to Requirement 9.3: connect or read timeout → caller returns
    HTTP 504 with ``error: "router_timeout"``.
    """


class RouterUnavailableError(Exception):
    """Raised when the TCP connection to the Router is refused or unreachable.

    Corresponds to Requirement 9.4: connection refused/unreachable → caller
    returns HTTP 502 with ``error: "router_unavailable"``.
    """


class RouterInvalidResponseError(Exception):
    """Raised when the Router returns 2xx but the body is empty or not valid JSON.

    Corresponds to Requirement 9.6: empty or non-JSON 2xx body → caller
    returns HTTP 502 with ``error: "router_invalid_response"``.
    """


# ---------------------------------------------------------------------------
# Forwarding function
# ---------------------------------------------------------------------------


async def forward_to_router(
    imf: dict,
    router_url: str,
    request_id: str,
    timeout_seconds: float | None = None,
) -> tuple[int, dict]:
    """Forward an enriched IMF to the downstream Intelligent Router.

    POSTs ``imf`` as JSON to ``{router_url}/router/route`` with the
    ``X-Request-Id`` header set to ``request_id`` for distributed trace
    correlation (Requirement 9.5).

    Args:
        imf:        The governance-enriched IMF dict to forward.
        router_url: Base URL of the Intelligent Router, e.g. ``http://router:8082``.
        request_id: UUID-v4 of the original request; included as a header.
        timeout_seconds: Read/write timeout budget for the whole downstream
                          pipeline (Router + cache + inference). Defaults to
                          30s if omitted — real callers should pass
                          settings.router_timeout_seconds instead, since
                          CPU-only inference can exceed 30s.

    Returns:
        A ``(status_code, body_dict)`` tuple.  For 2xx responses the body is
        the parsed JSON response from the Router.  For non-2xx responses the
        status code and parsed body are relayed unchanged (Requirement 9.2).

    Raises:
        RouterTimeoutError:         On ``httpx.TimeoutException`` (Requirement 9.3).
        RouterUnavailableError:     On ``httpx.ConnectError`` (Requirement 9.4).
        RouterInvalidResponseError: When the Router returns 2xx but the body
                                    is empty or cannot be parsed as JSON
                                    (Requirement 9.6).
    """
    try:
        timeout = _build_timeout(timeout_seconds if timeout_seconds is not None else _DEFAULT_READ_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{router_url}/route",
                json=imf,
                headers={"X-Request-Id": request_id},
            )

        if resp.status_code < 300:
            # 2xx — body must be non-empty, valid JSON (Requirement 9.6).
            try:
                body = resp.json()
            except Exception:
                raise RouterInvalidResponseError(request_id)

            if not body:
                raise RouterInvalidResponseError(request_id)

            return resp.status_code, body

        # Non-2xx — relay status code and body unchanged (Requirement 9.2).
        logger.warning(
            "router_non_2xx",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "status_code": resp.status_code,
                }
            },
        )
        return resp.status_code, resp.json()

    except RouterInvalidResponseError:
        raise
    except httpx.TimeoutException:
        raise RouterTimeoutError(request_id)
    except httpx.ConnectError:
        raise RouterUnavailableError(request_id)


async def forward_to_router_stream(
    imf: dict,
    router_url: str,
    request_id: str,
    http_client: httpx.AsyncClient,
    timeout_seconds: float | None = None,
) -> AsyncIterator[dict]:
    """Streaming counterpart to forward_to_router — POSTs to
    ``{router_url}/route/stream`` and yields each parsed newline-delimited-
    JSON line unchanged (see intelligent_router's streaming wire protocol —
    pipeline.py's run_streaming_routing_pipeline docstring):

        {"type": "delta", "content": "<text>"}
        {"type": "done", "imf": {...}}
        {"type": "error", "event": "<code>", "status_code": <int>, ...}

    The caller (pre_check.py's streaming endpoint) is responsible for
    chunk-level PII re-masking of "delta" content before relaying it
    further — this function only relays the Router's stream unchanged.

    Args:
        imf:             The governance-enriched IMF dict to forward.
        router_url:      Base URL of the Intelligent Router.
        request_id:      UUID-v4 of the original request (X-Request-Id header).
        http_client:     Shared httpx.AsyncClient; caller manages its lifecycle
                         (unlike forward_to_router, which opens its own).
        timeout_seconds: Read/write timeout budget. Defaults to
                         _DEFAULT_READ_TIMEOUT_SECONDS if omitted.

    Raises:
        RouterTimeoutError:     On httpx.TimeoutException.
        RouterUnavailableError: On httpx.ConnectError.
        RouterInvalidResponseError: If the Router returns non-200, or a
                                    stream line isn't valid JSON.
    """
    timeout = _build_timeout(timeout_seconds if timeout_seconds is not None else _DEFAULT_READ_TIMEOUT_SECONDS)
    try:
        async with http_client.stream(
            "POST",
            f"{router_url}/route/stream",
            json=imf,
            headers={"X-Request-Id": request_id},
            timeout=timeout,
        ) as resp:
            if resp.status_code >= 300:
                logger.warning(
                    "router_non_2xx",
                    extra={"extra_fields": {"request_id": request_id, "status_code": resp.status_code}},
                )
                raise RouterInvalidResponseError(request_id)

            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except RouterInvalidResponseError:
                    raise
                except Exception:
                    raise RouterInvalidResponseError(request_id)

    except RouterInvalidResponseError:
        raise
    except httpx.TimeoutException:
        raise RouterTimeoutError(request_id)
    except httpx.ConnectError:
        raise RouterUnavailableError(request_id)
