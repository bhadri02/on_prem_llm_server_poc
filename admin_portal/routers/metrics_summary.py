"""
admin_portal/routers/metrics_summary.py

Metrics summary router for the Admin/Developer Portal (Layer 10).

Endpoints
---------
GET /metrics/summary
    Queries Prometheus for three key operational metrics and returns them in a
    single ``MetricsSummary`` JSON object.

    Metrics returned:
      - ``request_rate``   — requests/second from ``rate(llm_api_gateway_requests_total[60s])``
      - ``error_rate``     — error fraction from errors/requests (null if no requests)
      - ``cache_hit_rate`` — cache hits / total lookups from ``llm_cache_requests_total``
                             (null if no cache lookups recorded)

    On Prometheus unreachability, timeout, or non-2xx response, returns HTTP 502
    with ``ErrorResponse(error="upstream_unavailable", upstream="prometheus")``.

Validates: Requirements 8.1, 8.2, 8.3
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
from fastapi import APIRouter
from fastapi.responses import Response

from admin_portal.config import settings
from admin_portal.metrics import (
    get_status_class,
    llm_portal_errors_total,
    llm_portal_latency_seconds,
    llm_portal_requests_total,
)
from admin_portal.schemas.errors import ErrorResponse
from admin_portal.schemas.metrics import MetricsSummary

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENDPOINT = "/portal/metrics/summary"
_PROMETHEUS_TIMEOUT = 5.0  # seconds (Req 8.3)

# PromQL expressions
_QUERY_REQUEST_RATE = "rate(llm_api_gateway_requests_total[60s])"
_QUERY_ERROR_RATE_NUM = "rate(llm_api_gateway_errors_total[60s])"
_QUERY_CACHE_HITS = 'llm_cache_requests_total{result="hit"}'
_QUERY_CACHE_TOTAL = "llm_cache_requests_total"

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/metrics", tags=["metrics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_scalar(prom_response: dict) -> Optional[float]:
    """Extract a scalar float from a Prometheus instant query response.

    Prometheus instant query format::

        {"data": {"result": [{"value": ["<timestamp>", "<value_string>"]}]}}

    Returns the float value from ``result[0]["value"][1]``, or ``None`` if the
    result array is empty (no time-series data recorded for the window).
    """
    try:
        result = prom_response["data"]["result"]
    except (KeyError, TypeError):
        return None

    if not result:
        return None

    try:
        return float(result[0]["value"][1])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _sum_results(prom_response: dict) -> Optional[float]:
    """Sum scalar values from all result series in a Prometheus instant query.

    Used for aggregating ``llm_cache_requests_total`` across all label
    combinations (e.g. summing hits across all label sets).

    Returns 0.0 if the result array is present but empty, or ``None`` if
    the response structure is unexpected.
    """
    try:
        result = prom_response["data"]["result"]
    except (KeyError, TypeError):
        return None

    total = 0.0
    for series in result:
        try:
            total += float(series["value"][1])
        except (IndexError, KeyError, TypeError, ValueError):
            pass
    return total


# ---------------------------------------------------------------------------
# GET /metrics/summary
# ---------------------------------------------------------------------------

@router.get(
    "/summary",
    summary="Get metrics summary",
    description=(
        "Query Prometheus for request rate, error rate, and cache hit rate.  "
        "Returns HTTP 502 with ``upstream='prometheus'`` if Prometheus is "
        "unreachable or returns a non-2xx response within 5 seconds."
    ),
)
async def get_metrics_summary() -> Response:
    """Query Prometheus for operational metrics and return a MetricsSummary.

    Issues up to four instant queries to Prometheus:
      1. ``rate(llm_api_gateway_requests_total[60s])``         → request_rate
      2. ``rate(llm_api_gateway_errors_total[60s])``           → error numerator
      3. ``llm_cache_requests_total{result="hit"}``             → cache hits
      4. ``llm_cache_requests_total``                           → cache total

    Computes:
      - ``error_rate = errors / requests`` (null if requests == 0)
      - ``cache_hit_rate = hits / total``  (null if total == 0)

    Returns HTTP 502 on any Prometheus connectivity or non-2xx failure.

    Validates: Requirements 8.1, 8.2, 8.3
    """
    t_start = time.monotonic()

    prom_base = settings.PROMETHEUS_URL.rstrip("/")

    _502_body = ErrorResponse(
        error="upstream_unavailable",
        message="prometheus is unreachable or timed out",
        upstream="prometheus",
    ).model_dump_json()

    async with httpx.AsyncClient(timeout=_PROMETHEUS_TIMEOUT) as client:
        try:
            # --- Req 8.1: Issue all four queries ----------------------------
            r_request_rate, r_error_num, r_cache_hits, r_cache_total = (
                await _query(client, prom_base, _QUERY_REQUEST_RATE),
                await _query(client, prom_base, _QUERY_ERROR_RATE_NUM),
                await _query(client, prom_base, _QUERY_CACHE_HITS),
                await _query(client, prom_base, _QUERY_CACHE_TOTAL),
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            # Req 8.3: upstream unreachable / timed out
            _record_502(t_start)
            return Response(content=_502_body, status_code=502, media_type="application/json")

    # --- Req 8.3: non-2xx Prometheus response --------------------------------
    for r in (r_request_rate, r_error_num, r_cache_hits, r_cache_total):
        if r.status_code < 200 or r.status_code >= 300:
            _record_502(t_start)
            return Response(content=_502_body, status_code=502, media_type="application/json")

    # --- Parse raw Prometheus responses -------------------------------------
    request_rate_val = _extract_scalar(r_request_rate.json())
    error_num_val = _extract_scalar(r_error_num.json())
    cache_hits_val = _sum_results(r_cache_hits.json())
    cache_total_val = _sum_results(r_cache_total.json())

    # --- Req 8.1: request_rate (requests/sec) --------------------------------
    request_rate: Optional[float] = request_rate_val  # None if no data

    # --- Req 8.2: error_rate = errors / requests; null when requests == 0 ---
    error_rate: Optional[float]
    if request_rate_val is None or request_rate_val == 0.0:
        error_rate = None
    else:
        error_rate = (error_num_val or 0.0) / request_rate_val

    # --- Req 8.2: cache_hit_rate = hits / total; null when total == 0 -------
    cache_hit_rate: Optional[float]
    if not cache_total_val:  # None or 0.0
        cache_hit_rate = None
    else:
        cache_hit_rate = (cache_hits_val or 0.0) / cache_total_val

    # --- Emit metrics and return --------------------------------------------
    latency = time.monotonic() - t_start
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT).observe(latency)
    llm_portal_requests_total.labels(endpoint=_ENDPOINT, status="2xx").inc()

    summary = MetricsSummary(
        request_rate=request_rate,
        error_rate=error_rate,
        cache_hit_rate=cache_hit_rate,
    )
    return Response(
        content=summary.model_dump_json(),
        status_code=200,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _query(client: httpx.AsyncClient, base_url: str, promql: str) -> httpx.Response:
    """Issue a single Prometheus instant query and return the raw response."""
    url = f"{base_url}/api/v1/query"
    return await client.get(url, params={"query": promql})


def _record_502(t_start: float) -> None:
    """Record Prometheus metrics for a 502 upstream failure."""
    latency = time.monotonic() - t_start
    llm_portal_latency_seconds.labels(endpoint=_ENDPOINT).observe(latency)
    llm_portal_requests_total.labels(endpoint=_ENDPOINT, status="5xx").inc()
    llm_portal_errors_total.labels(
        endpoint=_ENDPOINT, error_code="upstream_unavailable"
    ).inc()
