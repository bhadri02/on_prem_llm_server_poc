"""
Unit tests for the audit-store Helm chart.

Validates:
  - Requirements 2.7, 4.2, 6.6, 16.1
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Repo / chart root resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
CHART_DIR = REPO_ROOT / "llm-platform" / "charts" / "audit-store"


# ---------------------------------------------------------------------------
# Local helm helper (uses conftest helper when available, else inline)
# ---------------------------------------------------------------------------

try:
    from conftest import helm_template  # type: ignore[import]
except ImportError:  # pragma: no cover
    def helm_template(
        chart_dir: str | Path,
        release_name: str = "test-release",
        set_args: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Minimal fallback if conftest is not on sys.path."""
        cmd = ["helm", "template", release_name, str(chart_dir)]
        if set_args:
            for arg in set_args:
                cmd += ["--set", arg]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            cwd=str(REPO_ROOT),
        )
        docs = list(yaml.safe_load_all(result.stdout))
        return [d for d in docs if d is not None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_deployment(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first Deployment resource from a list of parsed YAML docs."""
    for doc in docs:
        if doc.get("kind") == "Deployment":
            return doc
    raise AssertionError("No Deployment found in rendered output")


# ---------------------------------------------------------------------------
# Test 1 — helm lint exits 0 with no warnings or errors
# ---------------------------------------------------------------------------

def test_audit_store_helm_lint() -> None:
    """helm lint must exit zero with no Warning or Error lines.

    Requirements: 2.7
    """
    result = subprocess.run(
        ["helm", "lint", str(CHART_DIR)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    output = (result.stdout + result.stderr).lower()
    assert result.returncode == 0, (
        f"helm lint exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    assert "error" not in output, (
        f"helm lint reported errors:\n{result.stdout}\n{result.stderr}"
    )
    assert "warning" not in output, (
        f"helm lint reported warnings:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Deployment uses envFrom.secretRef, not bare secretKeyRef
# ---------------------------------------------------------------------------

def test_audit_store_uses_envfrom_not_secretkeyref() -> None:
    """Rendered Deployment must use envFrom secretRef and must NOT contain
    any env entry that uses valueFrom.secretKeyRef.

    Requirements: 4.2
    """
    docs = helm_template(CHART_DIR, release_name="test-release")
    deployment = _get_deployment(docs)

    containers: list[dict] = (
        deployment["spec"]["template"]["spec"]["containers"]
    )
    assert containers, "No containers found in Deployment"
    container = containers[0]

    # Must have envFrom with a secretRef pointing at llm-poc-secrets
    env_from: list[dict] = container.get("envFrom", [])
    assert env_from, "container.envFrom is missing"
    secret_ref_names = [
        entry["secretRef"]["name"]
        for entry in env_from
        if "secretRef" in entry
    ]
    assert "llm-poc-secrets" in secret_ref_names, (
        f"Expected secretRef.name 'llm-poc-secrets' in envFrom, got: {secret_ref_names}"
    )

    # Must NOT have any env entry with valueFrom.secretKeyRef
    env_entries: list[dict] = container.get("env", [])
    bad_entries = [
        e for e in env_entries
        if e.get("valueFrom", {}).get("secretKeyRef") is not None
    ]
    assert not bad_entries, (
        f"Found bare secretKeyRef in env — should use envFrom instead: {bad_entries}"
    )


# ---------------------------------------------------------------------------
# Test 3 — persistence.enabled=false uses emptyDir, no PVC
# ---------------------------------------------------------------------------

def test_audit_store_persistence_disabled_uses_emptydir() -> None:
    """When persistence.enabled=false the Deployment uses an emptyDir volume
    and no PersistentVolumeClaim appears in the rendered output.

    Requirements: 6.6
    """
    docs = helm_template(
        CHART_DIR,
        release_name="test-release",
        set_args=["persistence.enabled=false"],
    )

    deployment = _get_deployment(docs)
    volumes: list[dict] = (
        deployment["spec"]["template"]["spec"].get("volumes", [])
    )

    # Find the 'data' volume
    data_vols = [v for v in volumes if v.get("name") == "data"]
    assert data_vols, "Expected a volume named 'data' in the Deployment"
    data_vol = data_vols[0]
    assert "emptyDir" in data_vol, (
        f"Expected emptyDir when persistence.enabled=false, got: {data_vol}"
    )

    # No PVC resource should appear in any document
    pvc_docs = [d for d in docs if d.get("kind") == "PersistentVolumeClaim"]
    assert not pvc_docs, (
        f"Expected no PVC when persistence.enabled=false, but found: {pvc_docs}"
    )


# ---------------------------------------------------------------------------
# Test 4 — image tag defaults to :latest when image.tag is empty
# ---------------------------------------------------------------------------

def test_audit_store_image_tag_defaults_to_latest() -> None:
    """When image.tag is not set the container image must end with ':latest'.

    Requirements: 16.1
    """
    docs = helm_template(
        CHART_DIR,
        release_name="test-release",
        set_args=["image.tag="],
    )
    deployment = _get_deployment(docs)
    containers: list[dict] = (
        deployment["spec"]["template"]["spec"]["containers"]
    )
    assert containers, "No containers found in Deployment"
    image: str = containers[0]["image"]
    assert image.endswith(":latest"), (
        f"Expected container image to end with ':latest', got: {image!r}"
    )
