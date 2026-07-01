"""
tests/smoke/test_security_helm.py — Helm smoke tests for the Security & Governance Layer.

Subtasks covered:
  26.1 — helm lint passes on the security-layer chart
  26.2 — helm template renders all expected resource kinds:
          Deployment, Service, NetworkPolicy, ServiceMonitor,
          ConfigMap, HorizontalPodAutoscaler
"""

import subprocess

import pytest

# ---------------------------------------------------------------------------
# Helm chart path (relative to workspace root — subprocess runs there)
# ---------------------------------------------------------------------------
CHART_PATH = "llm-platform/charts/security-layer/"

# ---------------------------------------------------------------------------
# Helper: skip gracefully when helm is not on PATH
# ---------------------------------------------------------------------------


def _helm_available() -> bool:
    """Return True if helm is on PATH and executable."""
    try:
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Subtask 26.1 — helm lint
# ---------------------------------------------------------------------------


def test_helm_lint_security_layer():
    """helm lint must exit 0 on the security-layer chart (subtask 26.1)."""
    if not _helm_available():
        pytest.skip("helm is not installed or not on PATH")

    result = subprocess.run(
        ["helm", "lint", CHART_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm lint exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Subtask 26.2 — helm template renders all expected resource kinds
# ---------------------------------------------------------------------------


def test_helm_template_renders_expected_resources():
    """helm template must render all required Kubernetes resource kinds (subtask 26.2).

    Expected resource kinds:
      - Deployment
      - Service
      - NetworkPolicy
      - ServiceMonitor
      - ConfigMap
      - HorizontalPodAutoscaler
    """
    if not _helm_available():
        pytest.skip("helm is not installed or not on PATH")

    result = subprocess.run(
        ["helm", "template", "test-release", CHART_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm template exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    rendered = result.stdout

    expected_kinds = [
        "kind: Deployment",
        "kind: Service",
        "kind: NetworkPolicy",
        "kind: ServiceMonitor",
        "kind: ConfigMap",
        "kind: HorizontalPodAutoscaler",
    ]

    for expected_kind in expected_kinds:
        assert expected_kind in rendered, (
            f"Expected '{expected_kind}' in helm template output but it was not found.\n"
            f"Rendered output (first 3000 chars):\n{rendered[:3000]}"
        )
