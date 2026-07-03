"""
Helm chart tests for the observability chart.

Stubs — implementations added in tasks 7.2, 7.3, 8.2, 8.3, 8.4, 13.1, 13.2.

Tests covered:
  Property 3:  ServiceMonitor Structural Invariant  (task 8.2)  — Req 3.1–3.3
  Property 8:  Dependency Version Pinning           (task 8.3)  — Req 8.1, 8.4
  Property 9:  Dashboard Panel Structural Invariant (task 7.2)  — Req 5.1–5.8
  Property 10: POC Scope Enforcement                (task 8.4)  — Req 11.1–11.7

  Unit tests (task 7.3):
    test_dashboard_json_is_valid
    test_dashboard_title
    test_seven_panels_present

  Integration tests (task 13.1):
    test_grafana_poc_dashboards_configmap_rendered
    test_prometheus_service_clusterip_port_9090
    test_grafana_datasource_sidecar_enabled
    test_jaeger_enabled_renders_deployment
    test_otel_wrong_port_fails

  Smoke tests (task 13.2):
    test_alertmanager_disabled_by_default
    test_tracing_disabled_by_default
    test_prometheus_retention_7d
    test_pvc_10gi
    test_single_replica_prometheus_grafana
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Dashboard unit tests (task 7.3)
# ---------------------------------------------------------------------------

import json
from pathlib import Path

# Resolve the dashboard path relative to the observability chart
OBSERVABILITY_CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "observability"
DASHBOARD_JSON_PATH = OBSERVABILITY_CHART_DIR / "dashboards" / "poc-overview.json"


@pytest.mark.unit
def test_dashboard_json_is_valid() -> None:
    """poc-overview.json parses as valid JSON."""
    assert DASHBOARD_JSON_PATH.exists(), (
        f"Dashboard JSON not found at {DASHBOARD_JSON_PATH}"
    )
    
    with DASHBOARD_JSON_PATH.open("r", encoding="utf-8") as f:
        try:
            dashboard = json.load(f)
        except json.JSONDecodeError as e:
            pytest.fail(f"Dashboard JSON is invalid: {e}")
    
    # Verify it's a dict (root object)
    assert isinstance(dashboard, dict), "Dashboard JSON root must be an object"


@pytest.mark.unit
def test_dashboard_title() -> None:
    """Root title equals 'LLM Platform POC Overview'."""
    assert DASHBOARD_JSON_PATH.exists(), (
        f"Dashboard JSON not found at {DASHBOARD_JSON_PATH}"
    )
    
    with DASHBOARD_JSON_PATH.open("r", encoding="utf-8") as f:
        dashboard = json.load(f)
    
    assert dashboard.get("title") == "LLM Platform POC Overview", (
        f"Dashboard title mismatch: expected 'LLM Platform POC Overview', "
        f"got {dashboard.get('title')!r}"
    )


@pytest.mark.unit
def test_seven_panels_present() -> None:
    """len(dashboard['panels']) == 7."""
    assert DASHBOARD_JSON_PATH.exists(), (
        f"Dashboard JSON not found at {DASHBOARD_JSON_PATH}"
    )
    
    with DASHBOARD_JSON_PATH.open("r", encoding="utf-8") as f:
        dashboard = json.load(f)
    
    panels = dashboard.get("panels", [])
    assert len(panels) == 7, (
        f"Expected 7 panels, found {len(panels)}"
    )


# ---------------------------------------------------------------------------
# Integration tests (task 13.1)
# ---------------------------------------------------------------------------
# TODO: implement in task 13.1


@pytest.mark.integration
def test_grafana_poc_dashboards_configmap_rendered() -> None:
    """helm template output contains ConfigMap grafana-poc-dashboards with poc-overview.json."""
    pytest.skip("Stub — implement in task 13.1")


@pytest.mark.integration
def test_prometheus_service_clusterip_port_9090() -> None:
    """Rendered manifests contain a ClusterIP Service on port 9090 for Prometheus."""
    pytest.skip("Stub — implement in task 13.1")


@pytest.mark.integration
def test_grafana_datasource_sidecar_enabled() -> None:
    """values.yaml has grafana.sidecar.datasources.enabled: true."""
    pytest.skip("Stub — implement in task 13.1")


@pytest.mark.integration
def test_jaeger_enabled_renders_deployment() -> None:
    """Rendering with jaeger.enabled=true produces a Deployment with 'jaeger' in its name."""
    pytest.skip("Stub — implement in task 13.1")


@pytest.mark.integration
def test_otel_wrong_port_fails() -> None:
    """Rendering with otlp.grpc.port=4318 returns non-zero exit with error referencing 4317."""
    pytest.skip("Stub — implement in task 13.1")


# ---------------------------------------------------------------------------
# Smoke tests (task 13.2)
# ---------------------------------------------------------------------------
# TODO: implement in task 13.2


@pytest.mark.smoke
def test_alertmanager_disabled_by_default() -> None:
    """Default values have alertmanager.enabled: false."""
    pytest.skip("Stub — implement in task 13.2")


@pytest.mark.smoke
def test_tracing_disabled_by_default() -> None:
    """Default values have opentelemetry-collector.enabled: false and jaeger.enabled: false."""
    pytest.skip("Stub — implement in task 13.2")


@pytest.mark.smoke
def test_prometheus_retention_7d() -> None:
    """Default values set prometheusSpec.retention: '7d'."""
    pytest.skip("Stub — implement in task 13.2")


@pytest.mark.smoke
def test_pvc_10gi() -> None:
    """Default values request 10Gi storage."""
    pytest.skip("Stub — implement in task 13.2")


@pytest.mark.smoke
def test_single_replica_prometheus_grafana() -> None:
    """replicaCount: 1 for both Prometheus and Grafana."""
    pytest.skip("Stub — implement in task 13.2")
