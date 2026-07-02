"""
tests/smoke/test_router_helm.py — Helm smoke tests for the Intelligent Router chart.

Subtask covered:
  32.3 — helm lint passes on llm-platform/charts/router/
          helm template renders expected Kubernetes resource kinds:
          Deployment, Service, ConfigMap, ServiceMonitor.
          ConfigMap data contains 'model_matrix.yaml' and
          'task_classifier_rules.yaml' keys.

Strategy
--------
Uses subprocess.run to invoke the `helm` CLI (matches the pattern in
tests/smoke/test_security_helm.py). All tests are skipped gracefully when
helm is not installed on PATH.
"""

import subprocess

import pytest
import yaml

# ---------------------------------------------------------------------------
# Chart path (relative to workspace root — subprocess cwd defaults there)
# ---------------------------------------------------------------------------

CHART_PATH = "llm-platform/charts/router/"


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
# 32.3a — helm lint
# ---------------------------------------------------------------------------


def test_helm_lint_router():
    """helm lint must exit 0 on the router chart (subtask 32.3)."""
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
# 32.3b — helm template: expected resource kinds
# ---------------------------------------------------------------------------


def test_helm_template_renders_expected_resources():
    """helm template must render Deployment, Service, ConfigMap, ServiceMonitor
    (subtask 32.3).
    """
    if not _helm_available():
        pytest.skip("helm is not installed or not on PATH")

    result = subprocess.run(
        ["helm", "template", CHART_PATH, "--set", "image.tag=test"],
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
        "kind: ConfigMap",
        "kind: ServiceMonitor",
    ]

    for expected_kind in expected_kinds:
        assert expected_kind in rendered, (
            f"Expected '{expected_kind}' in helm template output but it was not found.\n"
            f"Rendered output (first 3000 chars):\n{rendered[:3000]}"
        )


# ---------------------------------------------------------------------------
# 32.3c — helm template: ConfigMap data keys
# ---------------------------------------------------------------------------


def test_helm_template_configmap_data_keys():
    """The rendered ConfigMap must contain 'model_matrix.yaml' and
    'task_classifier_rules.yaml' as data keys (subtask 32.3).
    """
    if not _helm_available():
        pytest.skip("helm is not installed or not on PATH")

    result = subprocess.run(
        ["helm", "template", CHART_PATH, "--set", "image.tag=test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm template exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    # Split multi-document YAML output into individual documents
    documents = list(yaml.safe_load_all(result.stdout))

    # Find the ConfigMap document
    configmap = None
    for doc in documents:
        if doc and doc.get("kind") == "ConfigMap":
            configmap = doc
            break

    assert configmap is not None, (
        "Expected a ConfigMap document in helm template output, but none was found."
    )

    data = configmap.get("data") or {}

    assert "model_matrix.yaml" in data, (
        f"Expected 'model_matrix.yaml' key in ConfigMap data.\n"
        f"ConfigMap data keys found: {list(data.keys())}"
    )
    assert "task_classifier_rules.yaml" in data, (
        f"Expected 'task_classifier_rules.yaml' key in ConfigMap data.\n"
        f"ConfigMap data keys found: {list(data.keys())}"
    )
