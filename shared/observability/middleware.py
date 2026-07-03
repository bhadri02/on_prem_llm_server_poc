"""
shared.observability.middleware — reusable Starlette/FastAPI middleware.

Provides:
- `LoggingMiddleware`     — emits a structured log entry per request using
                            shared logging module; never touches IMF content
- `PrometheusMiddleware`  — records request metrics via `LayerMetrics` after
                            each request completes
- `configure_tracing()`  — optional OTel/Jaeger wiring (task 14; disabled by
                            default, wrapped in try/except ImportError)

Implementation: task 5.1 (LoggingMiddleware + PrometheusMiddleware),
                task 14.1 (configure_tracing)
Requirements: 2.19, 2.20, 6.1, 6.3, 7.1–7.4, 9.1, 9.3, 9.5
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shared.observability.logging import emit, get_logger
from shared.observability.metrics import LayerMetrics

__all__ = [
    "LoggingMiddleware",
    "PrometheusMiddleware",
    "configure_tracing",
]


class LoggingMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that emits one structured log entry per request.

    Extracts ``request_id`` from the ``X-Request-ID`` header (falls back to
    ``"none"`` when absent), times the full request/response cycle using
    ``time.monotonic()``, and calls :func:`emit` with only safe fields.

    **Never** reads ``request.body()``, passes any header values other than
    ``X-Request-ID``, or passes any IMF ``messages[].content`` fields to
    :func:`emit`.  Logging failures are silently swallowed so that a broken
    logger can never crash the service.

    Requirements: 6.1, 6.3, 7.1–7.4
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract request_id from the dedicated header only — no other header
        # values are read for logging purposes (Req 7.3).
        request_id: str = request.headers.get("X-Request-ID", "none")

        logger = get_logger(request_id)

        start = time.monotonic()
        response: Response = await call_next(request)
        end = time.monotonic()

        latency_ms = int((end - start) * 1000)

        # Determine log level from HTTP status code.
        status_code = response.status_code
        if status_code >= 500:
            level = "ERROR"
        elif status_code >= 400:
            level = "WARN"
        else:
            level = "INFO"

        # Only safe, non-sensitive fields are passed to emit (Req 7.1–7.4).
        # request.body() is deliberately never called here.
        try:
            emit(
                logger,
                level=level,
                event="request_processed",
                message=f"{request.method} {request.url.path} → {status_code}",
                latency_ms=latency_ms,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
            )
        except Exception:
            # Logging must never crash the service (Req 6.6).
            pass

        return response


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that records per-request Prometheus metrics.

    Accepts a :class:`~shared.observability.metrics.LayerMetrics` instance at
    construction time (passed in from the layer's ``main.py``), times the full
    request/response cycle, and calls ``record_request()`` after the response
    is returned.

    Status mapping:
    - ``2xx`` / ``3xx``  → ``"success"``
    - ``401`` / ``403``  → ``"blocked"``
    - all other ``4xx``  → ``"error"``
    - ``5xx``            → ``"error"``

    ``department`` and ``model`` are extracted from ``X-Department`` and
    ``X-Model`` request headers (falling back to ``"unknown"`` if absent).
    Metric failures are silently swallowed so that a broken metrics backend
    can never crash the service.

    Requirements: 2.19, 2.20
    """

    def __init__(self, app, layer_metrics: LayerMetrics) -> None:
        super().__init__(app)
        self.layer_metrics = layer_metrics

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response: Response = await call_next(request)
        end = time.monotonic()

        latency_s = end - start

        # Map HTTP status to metric status label.
        status_code = response.status_code
        if 200 <= status_code < 400:
            status = "success"
        elif status_code in (401, 403):
            status = "blocked"
        else:
            status = "error"

        # Extract routing context headers; never log their values (Req 7.3).
        department: str = request.headers.get("X-Department", "unknown")
        model: str = request.headers.get("X-Model", "unknown")

        try:
            self.layer_metrics.record_request(
                status=status,
                department=department,
                model=model,
                latency_s=latency_s,
            )
        except Exception:
            # Metric failures must never crash the service (Req 2.19 / 2.20).
            pass

        return response


def configure_tracing(service: str, otel_endpoint: str) -> None:
    """Wire OpenTelemetry tracing into the FastAPI service (opt-in, disabled by default).

    Initialises the OTel SDK with an OTLP/gRPC exporter pointed at
    ``otel_endpoint``, instruments FastAPI and httpx, and configures W3C
    ``traceparent`` header propagation on all outbound ``httpx`` calls so
    that traces span across service boundaries.

    **Mandatory span attributes** (set via a request hook, not here):
        ``llm.request_id``, ``llm.user_id``, ``llm.department``,
        ``llm.layer``, ``llm.model``, ``llm.task_type``,
        ``http.status_code``, ``llm.latency_ms``

    **Never** sets span attributes from ``imf.request.messages[].content``
    or any PII value.  Span attribute population is the responsibility of
    the server-request hook below (``_set_llm_span_attributes``), which only
    reads safe IMF header fields.

    Gracefully no-ops when any of the ``opentelemetry-*`` packages are not
    installed — the service starts normally without distributed tracing.

    Args:
        service:       The service / layer name used as the OTel
                       ``service.name`` resource attribute
                       (e.g. ``"api_gateway"``).
        otel_endpoint: The OTLP gRPC endpoint of the OTel Collector
                       (e.g. ``"http://otel-collector:4317"``).

    Requirements: 9.1, 9.3, 9.5
    """
    try:
        # ── OTel core SDK ────────────────────────────────────────────────
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # ── OTLP gRPC exporter ───────────────────────────────────────────
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError:
            # Fall back to HTTP exporter if grpc variant is unavailable
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[no-redef]
                OTLPSpanExporter,
            )

        # ── FastAPI auto-instrumentation ──────────────────────────────────
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            _fastapi_instrumentor_available = True
        except ImportError:
            _fastapi_instrumentor_available = False

        # ── httpx propagation ─────────────────────────────────────────────
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            _httpx_instrumentor_available = True
        except ImportError:
            _httpx_instrumentor_available = False

        # ── W3C TraceContext propagator ───────────────────────────────────
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.propagators.b3 import B3MultiFormat  # noqa: F401 — optional
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

    except ImportError:
        # opentelemetry-sdk (or a required sub-package) is not installed.
        # Tracing is opt-in for the POC; the service starts normally without it.
        return

    # ── Resource: service.name ────────────────────────────────────────────
    resource = Resource.create({"service.name": service})

    # ── Tracer provider + OTLP exporter ──────────────────────────────────
    exporter = OTLPSpanExporter(endpoint=otel_endpoint)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Register as the global provider so all code in this process uses it.
    trace.set_tracer_provider(provider)

    # ── W3C traceparent propagation ───────────────────────────────────────
    # Ensures inbound `traceparent` headers are read and outbound calls
    # propagate the current trace context (Req 9.5).
    set_global_textmap(TraceContextTextMapPropagator())

    # ── httpx auto-instrumentation ────────────────────────────────────────
    # Automatically injects `traceparent` into all outbound httpx requests
    # (both sync and async clients) so inter-service traces are linked.
    if _httpx_instrumentor_available:
        HTTPXClientInstrumentor().instrument()

    # ── FastAPI auto-instrumentation ──────────────────────────────────────
    # Instruments all FastAPI routes and adds a server-request hook that
    # populates the mandatory LLM span attributes from safe IMF header
    # fields only.  The hook never reads message content or PII values.
    if _fastapi_instrumentor_available:
        FastAPIInstrumentor().instrument(
            server_request_hook=_set_llm_span_attributes,
        )


def _set_llm_span_attributes(span, scope: dict) -> None:  # type: ignore[no-untyped-def]
    """Server-request hook: populate mandatory LLM span attributes safely.

    Called by ``opentelemetry-instrumentation-fastapi`` once per request,
    after the span has been created but before the handler runs.

    Reads **only** safe IMF-derived request headers; never touches
    ``request.body()``, ``imf.request.messages[].content``, or any PII field.

    Mandatory span attributes (master contract §Observability):
        llm.request_id     — X-Request-ID header
        llm.user_id        — X-User-ID header (opaque identifier only)
        llm.department     — X-Department header
        llm.layer          — X-Layer header (set by each service)
        llm.model          — X-Model header
        llm.task_type      — X-Task-Type header
        http.status_code   — populated post-response by the instrumentation
        llm.latency_ms     — populated post-response by the instrumentation

    Args:
        span:  The active OpenTelemetry :class:`~opentelemetry.trace.Span`.
        scope: The ASGI scope dict for the current request.
    """
    try:
        # Guard: span may be a NonRecordingSpan when sampling is off
        if not span or not span.is_recording():
            return

        # Extract headers from the ASGI scope — headers are list of
        # (bytes, bytes) tuples in ASGI.
        headers_raw: list[tuple[bytes, bytes]] = scope.get("headers", [])
        headers: dict[str, str] = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in headers_raw
        }

        def _get(header_name: str) -> str:
            """Return header value or empty string — never raises."""
            return headers.get(header_name, "")

        # Mandatory span attributes — safe header fields only.
        # NEVER add messages[].content, response content, or PII here.
        span.set_attribute("llm.request_id",  _get("x-request-id"))
        span.set_attribute("llm.user_id",     _get("x-user-id"))     # opaque ID only
        span.set_attribute("llm.department",  _get("x-department"))
        span.set_attribute("llm.layer",       _get("x-layer"))
        span.set_attribute("llm.model",       _get("x-model"))
        span.set_attribute("llm.task_type",   _get("x-task-type"))
        # http.status_code and llm.latency_ms are set post-response by the
        # FastAPIInstrumentor response hook — do not duplicate them here.

    except Exception:
        # Span attribute failures must never crash the service.
        pass
