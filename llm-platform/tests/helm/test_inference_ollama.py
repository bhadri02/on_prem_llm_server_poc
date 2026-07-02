"""
Unit tests for the inference-ollama Helm chart.

Validates:
  - Requirements 9.1, 9.4, 9.5, 9.6, 11.3
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Repo / chart root resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
CHART_DIR = REPO_ROOT / "llm-platform" / "charts" / "inference-ollama"


# ---------------------------------------------------------------------------
# Local helm helper (uses conftest helper when available, else inline)
# ---------------------------------------------------------------------------

try:
    from conftest import helm_template  # type: ignore[import]
except ImportError:  # pragma: no cover
    import tempfile

    import yaml

    def helm_template(
        chart_dir: str | Path,
        release_name: str = "test-release",
        values: dict[str, Any] | None = None,
        set_args: list[str] | None = None,
        namespace: str = "llm-poc",
        extra_args: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Minimal fallback if conftest is not on sys.path."""
        chart_dir = Path(chart_dir)
        cmd = [
            "helm", "template", release_name, str(chart_dir),
            "--namespace", namespace,
        ]
        tmp_values_file = None
        if values:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, prefix="helm-test-values-"
            )
            yaml.dump(values, tmp)
            tmp.flush()
            tmp.close()
            tmp_values_file = tmp.name
            cmd += ["--values", tmp_values_file]
        if set_args:
            for arg in set_args:
                cmd += ["--set", arg]
        if extra_args:
            cmd.extend(extra_args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        finally:
            if tmp_values_file:
                Path(tmp_values_file).unlink(missing_ok=True)
        docs = list(__import__("yaml").safe_load_all(result.stdout))
        return [d for d in docs if d is not None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_ollama_deployment(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the Deployment whose name ends with 'inference-ollama' (not '-adapter')."""
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        name: str = doc.get("metadata", {}).get("name", "")
        if name.endswith("-adapter"):
            continue
        return doc
    raise AssertionError(
        "No ollama Deployment (non-adapter) found in rendered output. "
        f"Deployments present: {[d['metadata']['name'] for d in docs if d.get('kind') == 'Deployment']}"
    )


def _get_job(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first Job resource from a list of parsed YAML docs."""
    for doc in docs:
        if doc.get("kind") == "Job":
            return doc
    raise AssertionError("No Job found in rendered output")


def _get_container(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the named container from a Deployment spec."""
    containers: list[dict] = (
        deployment["spec"]["template"]["spec"]["containers"]
    )
    for c in containers:
        if c.get("name") == name:
            return c
    available = [c.get("name") for c in containers]
    raise AssertionError(
        f"Container '{name}' not found. Available containers: {available}"
    )


# ---------------------------------------------------------------------------
# Test 1 — helm lint exits 0
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_inference_ollama_helm_lint() -> None:
    """helm lint must exit zero for the inference-ollama chart.

    Requirements: 9.1
    """
    result = subprocess.run(
        ["helm", "lint", str(CHART_DIR)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"helm lint exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Ollama container liveness probe path, port and delays
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ollama_container_liveness_probe() -> None:
    """The ollama container liveness probe must target /api/tags on port 11434
    with initialDelaySeconds=30 and timeoutSeconds=30.

    Requirements: 9.4, 9.5
    """
    docs = helm_template(CHART_DIR, release_name="test-release")
    deployment = _get_ollama_deployment(docs)
    container = _get_container(deployment, "ollama")

    probe: dict = container.get("livenessProbe", {})
    assert probe, "livenessProbe is missing from the ollama container"

    http_get: dict = probe.get("httpGet", {})
    assert http_get.get("path") == "/api/tags", (
        f"Expected livenessProbe.httpGet.path == '/api/tags', got: {http_get.get('path')!r}"
    )
    assert http_get.get("port") == 11434, (
        f"Expected livenessProbe.httpGet.port == 11434, got: {http_get.get('port')!r}"
    )
    assert probe.get("initialDelaySeconds") == 30, (
        f"Expected livenessProbe.initialDelaySeconds == 30, got: {probe.get('initialDelaySeconds')!r}"
    )
    assert probe.get("timeoutSeconds") == 30, (
        f"Expected livenessProbe.timeoutSeconds == 30, got: {probe.get('timeoutSeconds')!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Init Job has activeDeadlineSeconds and backoffLimit
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_init_job_deadline_and_backoff() -> None:
    """The init Job must have activeDeadlineSeconds=6000 and backoffLimit=2.

    Requirements: 9.6, 11.3
    """
    docs = helm_template(CHART_DIR, release_name="test-release")
    job = _get_job(docs)

    job_spec: dict = job.get("spec", {})
    assert job_spec.get("activeDeadlineSeconds") == 6000, (
        f"Expected spec.activeDeadlineSeconds == 6000, got: {job_spec.get('activeDeadlineSeconds')!r}"
    )
    assert job_spec.get("backoffLimit") == 2, (
        f"Expected spec.backoffLimit == 2, got: {job_spec.get('backoffLimit')!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Empty models.preload produces a Job with an exit-guard script
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_empty_preload_job_contains_skip_guard() -> None:
    """When models.preload is empty the Job container script must contain the
    empty-list guard phrase that causes an early exit-0.

    Requirements: 9.6
    """
    docs = helm_template(
        CHART_DIR,
        release_name="test-release",
        values={"models": {"preload": []}},
    )
    job = _get_job(docs)

    containers: list[dict] = (
        job["spec"]["template"]["spec"]["containers"]
    )
    assert containers, "No containers found in Job spec"
    container = containers[0]

    # The command field is a list; join it for substring search
    command_parts: list = container.get("command", [])
    command_str = " ".join(str(part) for part in command_parts)

    has_guard = (
        "if [ -z" in command_str
        or "model_preload_skipped" in command_str
    )
    assert has_guard, (
        "Expected the Job script to contain the empty-preload guard "
        "('if [ -z' or 'model_preload_skipped') when models.preload is empty. "
        f"Command was:\n{command_str}"
    )
