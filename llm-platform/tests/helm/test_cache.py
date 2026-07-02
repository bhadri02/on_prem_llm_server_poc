"""
Unit tests for the cache Helm chart.

Validates:
  - Requirements 2.7, 4.2, 6.6
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
CHART_DIR = REPO_ROOT / "llm-platform" / "charts" / "cache"


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
# Test 1 — helm lint exits 0 with no errors or warnings
# ---------------------------------------------------------------------------

def test_cache_helm_lint() -> None:
    """helm lint must exit zero with no Error lines.

    The cache chart has a Redis sub-chart dependency. If the dependency charts
    directory is absent we attempt ``helm dependency update`` first, then
    re-run lint. The missing-dependency WARNING is tolerated; only hard errors
    cause a test failure.

    Requirements: 2.7
    """
    def _run_lint() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["helm", "lint", str(CHART_DIR), "--set", "redis.enabled=false"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    result = _run_lint()

    # If lint failed because of missing dependency charts, try to fetch them.
    if result.returncode != 0:
        dep_result = subprocess.run(
            ["helm", "dependency", "update", str(CHART_DIR)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if dep_result.returncode == 0:
            # Re-run lint after fetching dependencies.
            result = _run_lint()

    output = (result.stdout + result.stderr).lower()
    assert result.returncode == 0, (
        f"helm lint exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    assert "error" not in output, (
        f"helm lint reported errors:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 2 — redis.master.persistence.storageClass is empty string
# ---------------------------------------------------------------------------

def test_cache_redis_storage_class_empty() -> None:
    """values.yaml must set redis.master.persistence.storageClass to '' so
    that Redis uses the cluster's default StorageClass (no custom class
    pinned at deploy time).

    Requirements: 6.6
    """
    values_path = CHART_DIR / "values.yaml"
    assert values_path.exists(), f"values.yaml not found at {values_path}"

    with values_path.open() as fh:
        values = yaml.safe_load(fh)

    storage_class = (
        values
        .get("redis", {})
        .get("master", {})
        .get("persistence", {})
        .get("storageClass")
    )

    # The key must exist and be set to the empty string (cluster default).
    assert storage_class is not None, (
        "redis.master.persistence.storageClass key is missing from values.yaml"
    )
    assert storage_class == "", (
        f"Expected redis.master.persistence.storageClass == '', got: {storage_class!r}"
    )


# ---------------------------------------------------------------------------
# Helpers — dependency management
# ---------------------------------------------------------------------------

def _ensure_chart_dependencies(chart_dir: Path) -> None:
    """Ensure Helm chart dependencies are available in charts/.

    If the ``charts/`` directory is missing or empty (sub-chart archives not
    yet downloaded), attempt a ``helm dependency build`` so that
    ``helm template`` can resolve them.  Skips silently if the charts
    directory already contains archives.
    """
    charts_subdir = chart_dir / "charts"
    has_archives = charts_subdir.is_dir() and any(
        f.suffix == ".tgz" for f in charts_subdir.iterdir()
    ) if charts_subdir.is_dir() else False

    if not has_archives:
        subprocess.run(
            ["helm", "dependency", "build", str(chart_dir)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,  # best-effort; lint/template will surface errors
        )


# ---------------------------------------------------------------------------
# Test 3 — Deployment container uses envFrom.secretRef: llm-poc-secrets
# ---------------------------------------------------------------------------

def test_cache_deployment_uses_envfrom_secretref() -> None:
    """Rendered cache Deployment must have envFrom with secretRef.name
    equal to 'llm-poc-secrets'.

    Ensures sub-chart dependencies are available (downloads them if needed)
    then renders with ``redis.enabled=false`` to disable the Redis StatefulSet
    from appearing in output, while still satisfying Helm's dependency check.

    Requirements: 4.2
    """
    _ensure_chart_dependencies(CHART_DIR)

    docs = helm_template(
        CHART_DIR,
        release_name="test-release",
        set_args=["redis.enabled=false"],
    )

    deployment = _get_deployment(docs)

    containers: list[dict] = (
        deployment["spec"]["template"]["spec"]["containers"]
    )
    assert containers, "No containers found in Deployment"
    container = containers[0]

    env_from: list[dict] = container.get("envFrom", [])
    assert env_from, "container.envFrom is missing from cache Deployment"

    secret_ref_names = [
        entry["secretRef"]["name"]
        for entry in env_from
        if "secretRef" in entry
    ]
    assert "llm-poc-secrets" in secret_ref_names, (
        f"Expected secretRef.name 'llm-poc-secrets' in envFrom, got: {secret_ref_names}"
    )
