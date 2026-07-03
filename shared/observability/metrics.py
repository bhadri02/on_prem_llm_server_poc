"""
shared.observability.metrics — Prometheus metric factory for platform layers.

Provides `make_layer_metrics()` which registers the three mandatory Prometheus
metric families for a given platform layer, and `validate_scrape_interval()`
which validates scrape interval strings.

Implementation: task 2.1
Property tests:  task 2.2 (Property 1), task 2.3 (Property 2), task 2.4 (Property 4)
Requirements: 2.1–2.21, 3.4
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from prometheus_client import CollectorRegistry, Counter, Histogram

__all__ = [
    "VALID_LAYERS",
    "LayerMetrics",
    "make_layer_metrics",
    "validate_scrape_interval",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_LAYERS = ("api_gateway", "security", "router", "cache", "inference", "agent")

_LATENCY_BUCKETS = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class LayerMetrics:
    """Holds the three mandatory Prometheus metric objects for a platform layer.

    For the ``cache`` layer, ``requests_total`` is registered with an extra
    ``outcome`` label (``hit`` | ``miss``).  Callers should use
    ``record_request()`` which handles the difference transparently.

    Attributes:
        requests_total: Counter — ``llm_{layer}_requests_total``
        latency_seconds: Histogram — ``llm_{layer}_latency_seconds``
        errors_total: Counter — ``llm_{layer}_errors_total``
        _is_cache: Internal flag set to ``True`` when layer == "cache".
    """

    requests_total: Counter
    latency_seconds: Histogram
    errors_total: Counter
    _is_cache: bool = False

    def record_request(
        self,
        status: Literal["success", "error", "blocked"],
        department: str,
        model: str,
        latency_s: float,
        outcome: Optional[Literal["hit", "miss"]] = None,
    ) -> None:
        """Increment ``requests_total`` and observe latency in one call.

        Args:
            status: One of ``"success"``, ``"error"``, ``"blocked"``.
            department: Value of the ``department`` label (maps to
                ``imf.user.department``).
            model: Value of the ``model`` label (maps to
                ``imf.routing.selected_model``).
            latency_s: Request duration in seconds (≥ 0).
            outcome: For the cache layer only — ``"hit"`` or ``"miss"``.
                Ignored for all other layers.
        """
        if self._is_cache:
            outcome_val = outcome if outcome is not None else "miss"
            self.requests_total.labels(
                status=status,
                department=department,
                model=model,
                outcome=outcome_val,
            ).inc()
        else:
            self.requests_total.labels(
                status=status,
                department=department,
                model=model,
            ).inc()

        self.latency_seconds.labels(department=department).observe(latency_s)

    def record_error(self, error_code: str, department: str) -> None:
        """Increment ``errors_total`` with the given error_code and department.

        Args:
            error_code: Platform-level error code from the audit record schema.
            department: Value of the ``department`` label.
        """
        self.errors_total.labels(
            error_code=error_code,
            department=department,
        ).inc()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_layer_metrics(
    layer: str,
    registry: Optional[CollectorRegistry] = None,
) -> LayerMetrics:
    """Create and register the three mandatory Prometheus metric families for a
    platform layer.

    Args:
        layer: One of ``"api_gateway"``, ``"security"``, ``"router"``,
               ``"cache"``, ``"inference"``, ``"agent"``.
        registry: Optional ``CollectorRegistry`` to register metrics in.
            Defaults to the global ``prometheus_client`` default registry.
            Pass a fresh ``CollectorRegistry()`` in tests to avoid
            ``Duplicated timeseries`` errors across test runs.

    Returns:
        A :class:`LayerMetrics` instance holding the three metric objects.

    Raises:
        ValueError: If ``layer`` is not one of the six valid values.
    """
    if layer not in VALID_LAYERS:
        raise ValueError(
            f"Invalid layer {layer!r}. Must be one of: {VALID_LAYERS}"
        )

    # Registry kwargs — only pass when explicitly supplied so we don't break
    # callers that rely on the global registry default.
    reg_kwargs: dict = {} if registry is None else {"registry": registry}

    is_cache = layer == "cache"

    # llm_{layer}_requests_total
    request_labels = ["status", "department", "model"]
    if is_cache:
        request_labels = ["status", "department", "model", "outcome"]

    requests_total = Counter(
        f"llm_{layer}_requests_total",
        f"Total requests handled by the {layer} layer",
        request_labels,
        **reg_kwargs,
    )

    # llm_{layer}_latency_seconds
    latency_seconds = Histogram(
        f"llm_{layer}_latency_seconds",
        f"Request latency for the {layer} layer",
        ["department"],
        buckets=_LATENCY_BUCKETS,
        **reg_kwargs,
    )

    # llm_{layer}_errors_total
    errors_total = Counter(
        f"llm_{layer}_errors_total",
        f"Total errors recorded by the {layer} layer",
        ["error_code", "department"],
        **reg_kwargs,
    )

    return LayerMetrics(
        requests_total=requests_total,
        latency_seconds=latency_seconds,
        errors_total=errors_total,
        _is_cache=is_cache,
    )


# ---------------------------------------------------------------------------
# Scrape interval validation
# ---------------------------------------------------------------------------

# Matches strings like "15s", "5s", "300s", "1m30s", "2m", "1h" etc.
# We only enforce the [5, 300] constraint on the total seconds value.
_INTERVAL_RE = re.compile(
    r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$"
)


def validate_scrape_interval(s: str) -> None:
    """Parse a scrape interval string and validate it is within [5, 300] seconds.

    Accepts strings of the form ``"<n>s"``, ``"<n>m"``, ``"<n>h"``, or any
    combination thereof (e.g. ``"1m30s"``).  Pure numeric strings (without a
    unit) are **not** accepted.

    Args:
        s: The scrape interval string to validate (e.g. ``"15s"``).

    Raises:
        ValueError: If the string cannot be parsed, represents zero seconds,
            or its total duration in seconds is outside the closed interval
            ``[5, 300]``.
    """
    if not s or not isinstance(s, str):
        raise ValueError(f"Scrape interval must be a non-empty string, got {s!r}")

    m = _INTERVAL_RE.match(s.strip())
    if not m or not any(m.group(g) for g in ("hours", "minutes", "seconds")):
        raise ValueError(
            f"Cannot parse scrape interval {s!r}. "
            "Expected a Go duration string such as '15s', '1m', '2m30s'."
        )

    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)

    total_seconds = hours * 3600 + minutes * 60 + seconds

    if total_seconds < 5 or total_seconds > 300:
        raise ValueError(
            f"Scrape interval {s!r} resolves to {total_seconds}s, "
            f"which is outside the allowed range [5, 300] seconds."
        )
