"""
Shared fixtures and helpers for Helm chart property-based and unit tests.

Requirements: 14.1, 14.2
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, settings


# ---------------------------------------------------------------------------
# Hypothesis settings profiles
# ---------------------------------------------------------------------------

# "ci" profile: used in CI pipelines and as the default for this test suite.
# Runs enough examples to catch regressions without blowing the time budget.
settings.register_profile(
    "ci",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Load the ci profile as the active default for all tests in this package.
settings.load_profile("ci")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: All ten platform sub-chart names in kebab-case.
CHARTS: list[str] = [
    "api-gateway",
    "security-layer",
    "router",
    "cache",
    "inference-ollama",
    "agent-framework",
    "model-registry",
    "audit-store",
    "admin-portal",
    "observability",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def to_camel(kebab: str) -> str:
    """Convert a kebab-case chart name to camelCase.

    Examples:
        api-gateway      -> apiGateway
        security-layer   -> securityLayer
        inference-ollama -> inferenceOllama
        router           -> router
        cache            -> cache

    Args:
        kebab: A kebab-case string (e.g. ``"api-gateway"``).

    Returns:
        The camelCase equivalent (e.g. ``"apiGateway"``).
    """
    parts = kebab.split("-")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


def helm_template(
    chart_dir: str | Path,
    release_name: str = "test-release",
    values: dict[str, Any] | None = None,
    set_args: list[str] | None = None,
    namespace: str = "llm-poc",
    extra_args: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Invoke ``helm template`` and return parsed YAML documents.

    Runs ``helm template <release_name> <chart_dir>`` via :mod:`subprocess`,
    parses the multi-document YAML output with :func:`yaml.safe_load_all`,
    and returns a list of all non-``None`` documents.

    Args:
        chart_dir: Path to the Helm chart directory to render.
        release_name: Helm release name passed as the first positional argument
            to ``helm template``. Defaults to ``"test-release"``.
        values: Optional dict of values written to a temporary ``values.yaml``
            file and passed via ``--values``.
        set_args: Optional list of ``--set`` strings, e.g.
            ``["persistence.enabled=false", "ingress.enabled=true"]``.
        namespace: Kubernetes namespace passed via ``--namespace``.
            Defaults to ``"llm-poc"``.
        extra_args: Optional list of additional arguments appended verbatim to
            the ``helm template`` command.

    Returns:
        A list of parsed YAML documents (dicts). ``None`` documents produced by
        empty YAML blocks (e.g. ``---`` without content) are excluded.

    Raises:
        subprocess.CalledProcessError: If ``helm template`` exits with a
            non-zero return code.
    """
    chart_dir = Path(chart_dir)

    cmd: list[str] = [
        "helm",
        "template",
        release_name,
        str(chart_dir),
        "--namespace",
        namespace,
    ]

    # Write caller-supplied values dict to a temp file so we don't have to
    # shell-quote complex YAML on the command line.
    tmp_values_file = None
    if values:
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            prefix="helm-test-values-",
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
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    finally:
        if tmp_values_file:
            Path(tmp_values_file).unlink(missing_ok=True)

    # Parse multi-document YAML; filter out None (empty) documents.
    documents = list(yaml.safe_load_all(result.stdout))
    return [doc for doc in documents if doc is not None]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def charts() -> list[str]:
    """Return the list of all 10 sub-chart names."""
    return CHARTS


@pytest.fixture(scope="session")
def umbrella_dir() -> Path:
    """Return the absolute path to the ``llm-platform/`` umbrella chart directory.

    Resolved relative to this conftest's location:
        <repo-root>/llm-platform/tests/helm/conftest.py
        → <repo-root>/llm-platform/
    """
    return Path(__file__).parent.parent.parent.resolve()
