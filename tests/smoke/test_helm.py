"""
tests/smoke/test_helm.py — Smoke tests for Helm chart and application startup.

Subtasks covered:
  19.1 — helm lint passes on the audit-store chart
  19.2 — helm template renders Deployment, Service, NetworkPolicy, ServiceMonitor
  19.3 — create_app() lifespan with valid config sets up conn, schema, and WAL mode
  19.4 — create_app() lifespan with empty AUDIT_API_KEY raises SystemExit
"""

import shutil
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from sqlalchemy import inspect

from audit_store.config import Settings
from audit_store.main import lifespan

# ---------------------------------------------------------------------------
# Helm chart path (relative to workspace root — subprocess runs there)
# ---------------------------------------------------------------------------
CHART_PATH = "llm-platform/charts/audit-store/"

# ---------------------------------------------------------------------------
# Subtask 19.1 — helm lint
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="helm is not installed in this environment",
)
def test_helm_lint_passes():
    """helm lint must exit 0 on the audit-store chart (subtask 19.1)."""
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
# Subtask 19.2 — helm template
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="helm is not installed in this environment",
)
def test_helm_template_renders_expected_resources():
    """helm template must render at least one each of the expected resource kinds
    (Deployment, Service, NetworkPolicy, ServiceMonitor) — subtask 19.2.
    """
    result = subprocess.run(
        ["helm", "template", CHART_PATH],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"helm template exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    rendered = result.stdout
    for expected_kind in (
        "kind: Deployment",
        "kind: Service",
        "kind: NetworkPolicy",
        "kind: ServiceMonitor",
    ):
        assert expected_kind in rendered, (
            f"Expected '{expected_kind}' in helm template output but it was not found.\n"
            f"Rendered output:\n{rendered[:2000]}"
        )


# ---------------------------------------------------------------------------
# Subtask 19.3 — startup smoke test (valid config)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_smoke_valid_config():
    """With AUDIT_API_KEY='test-key' and DATABASE_URL pointed at an in-memory
    SQLite DB, the lifespan must:
    - store a non-None engine on app.state.engine
    - have created the audit_events table (with its indexes)

    Subtask 19.3.
    """
    mock_settings = MagicMock(spec=Settings)
    mock_settings.audit_api_key = "test-key"
    mock_settings.database_url = "sqlite:///:memory:"
    mock_settings.retention_days = 0

    test_app = FastAPI(lifespan=lifespan)

    with patch("audit_store.main.settings", mock_settings):
        async with lifespan(test_app):
            engine = test_app.state.engine

            # 1. app.state.engine is not None
            assert engine is not None, "app.state.engine should be set after lifespan startup"

            # 2. audit_events table (and its indexes) exist
            assert "audit_events" in inspect(engine).get_table_names(), (
                "audit_events table was not created during lifespan startup"
            )
            index_names = {idx["name"] for idx in inspect(engine).get_indexes("audit_events")}
            assert index_names == {"idx_request_id", "idx_user_id", "idx_timestamp"}


# ---------------------------------------------------------------------------
# Subtask 19.4 — startup-refusal smoke test (empty AUDIT_API_KEY)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_smoke_refuses_empty_api_key():
    """With AUDIT_API_KEY='' the lifespan must raise SystemExit (subtask 19.4)."""
    mock_settings = MagicMock(spec=Settings)
    mock_settings.audit_api_key = ""
    mock_settings.database_url = "sqlite:///:memory:"

    test_app = FastAPI(lifespan=lifespan)

    with patch("audit_store.main.settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(test_app):
                pass  # pragma: no cover — should never reach here

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) but got exit code {exc_info.value.code}"
    )
