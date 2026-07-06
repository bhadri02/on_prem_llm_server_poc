"""
router_client.py — Downstream Router client for the Security & Governance Layer.

Forwards the enriched IMF to the Intelligent Router via HTTP POST and relays
the response back to the caller.  Three typed exceptions signal the distinct
failure modes callers need to handle separately.
"""

import httpx

from security_layer.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------

#: Per-operation timeout used for all Router calls.
#: connect: 5 s — fail fast if the Router is unreachable.
#: read/write: 30 s — allow time for the inference pipeline behind the Router.
#: pool: 5 s — time to wait for an available connection from the pool.
ROUTER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


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
) -> tuple[int, dict]:
    """Forward an enriched IMF to the downstream Intelligent Router.

    POSTs ``imf`` as JSON to ``{router_url}/router/route`` with the
    ``X-Request-Id`` header set to ``request_id`` for distributed trace
    correlation (Requirement 9.5).

    Args:
        imf:        The governance-enriched IMF dict to forward.
        router_url: Base URL of the Intelligent Router, e.g. ``http://router:8082``.
        request_id: UUID-v4 of the original request; included as a header.

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
        async with httpx.AsyncClient(timeout=ROUTER_TIMEOUT) as client:
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
