"""
admin_portal/metrics.py

Prometheus metrics definitions for the Admin/Developer Portal (Layer 10).

Exposes three metric families that track portal API activity:

  llm_portal_requests_total   — request counts by endpoint and status class
  llm_portal_latency_seconds  — end-to-end latency histograms by endpoint
  llm_portal_errors_total     — error counts by endpoint and error_code

Also exposes ``metrics_app``, a Prometheus ASGI app that ``main.py`` mounts
on port 9090 (path ``/metrics``).

Helper ``get_status_class(status_code)`` maps a numeric HTTP status code to
one of the label values accepted by ``llm_portal_requests_total``:
  "2xx", "4xx", or "5xx".

Uses the default global ``prometheus_client.REGISTRY`` so that the standard
``/metrics`` endpoint reflects all registered metrics without additional
configuration.

Validates: Requirements 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

import prometheus_client
from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# llm_portal_requests_total
# Labels:
#   endpoint — URL path, e.g. "/portal/playground/chat"
#   status   — HTTP status class: "2xx" | "4xx" | "5xx"
# ---------------------------------------------------------------------------
llm_portal_requests_total = Counter(
    "llm_portal_requests_total",
    "Total requests handled by the Admin/Developer Portal API, by endpoint and status class",
    ["endpoint", "status"],
)

# ---------------------------------------------------------------------------
# llm_portal_latency_seconds
# Labels:
#   endpoint — URL path, e.g. "/portal/models"
# Buckets: default prometheus_client buckets (.005, .01, .025, .05, .075,
#          .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0)
# ---------------------------------------------------------------------------
llm_portal_latency_seconds = Histogram(
    "llm_portal_latency_seconds",
    "End-to-end request latency for the Admin/Developer Portal API",
    ["endpoint"],
)

# ---------------------------------------------------------------------------
# llm_portal_errors_total
# Labels:
#   endpoint   — URL path
#   error_code — one of:
#       "upstream_unavailable" — a proxied upstream service did not respond
#       "validation_error"     — request body / parameter failed validation
#       "not_found"            — requested resource does not exist (404)
#       "internal_error"       — unhandled exception or unexpected server error
# ---------------------------------------------------------------------------
llm_portal_errors_total = Counter(
    "llm_portal_errors_total",
    "Total error responses from the Admin/Developer Portal API, by endpoint and error_code",
    ["endpoint", "error_code"],
)

# ---------------------------------------------------------------------------
# metrics_app — ASGI app that serves the /metrics endpoint.
# main.py mounts this on a separate port (9090) via:
#     app.mount("/metrics", metrics_app)
# ---------------------------------------------------------------------------
metrics_app = prometheus_client.make_asgi_app()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def get_status_class(status_code: int) -> str:
    """Map a numeric HTTP status code to its status-class label string.

    Returns:
        "2xx"  for 200–299
        "4xx"  for 400–499
        "5xx"  for 500–599 and for any unrecognised / out-of-range value
    """
    if 200 <= status_code < 300:
        return "2xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"
