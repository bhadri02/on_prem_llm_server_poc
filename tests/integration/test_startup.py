"""
tests/integration/test_startup.py

Integration tests for the Intelligent Router lifespan startup validation.

Covers subtask 29.4:
  - MODEL_MATRIX_PATH unset (settings=None) → sys.exit(1) with ERROR log
  - INFERENCE_TIMEOUT_SECONDS=0 → sys.exit(1)
  - INFERENCE_TIMEOUT_SECONDS=601 → sys.exit(1)
  - HEALTH_CHECK_TIMEOUT_SECONDS=31 → sys.exit(1)
  - Valid config → app.state has classifier_rules, model_matrix, http_client set
  - YAML file not found (bad path) → sys.exit(1)

Strategy
--------
The real lifespan in main.py calls sys.exit(1) on startup failures. We invoke
the lifespan directly via ``app.router.lifespan_context(app)`` and assert
``pytest.raises(SystemExit)``.

The lifespan reads the module-level ``settings`` name from
``intelligent_router.main`` (imported at module top). We patch THAT reference
(``intelligent_router.main.settings``) so the lifespan sees our mock value.

For the valid-config test we copy the real YAML files into a temp directory so
the test is isolated from repo-root file changes.

Requirements: 14.2, 14.3, 14.4, 14.7, 14.8
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import intelligent_router.main as main_mod
from intelligent_router.main import create_app

# ---------------------------------------------------------------------------
# Path to the real YAML config files at repo root
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
REAL_RULES_YAML = _REPO_ROOT / "task_classifier_rules.yaml"
REAL_MATRIX_YAML = _REPO_ROOT / "model_matrix.yaml"
REAL_POLICY_YAML = _REPO_ROOT / "policy_matrix.yaml"


# ---------------------------------------------------------------------------
# Helper: build a valid mock settings object pointing at real YAMLs
# ---------------------------------------------------------------------------


def _valid_settings(
    rules_path: str, matrix_path: str, policy_path: str = str(REAL_POLICY_YAML)
) -> MagicMock:
    """Return a MagicMock that passes all lifespan validation checks."""
    s = MagicMock()
    s.model_matrix_path = matrix_path
    s.task_rules_path = rules_path
    s.policy_matrix_path = policy_path
    s.audit_store_url = "http://audit-store:9200"
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.inference_timeout_seconds = 30      # valid: [1, 600]
    s.health_check_timeout_seconds = 5    # valid: [1, 30]
    s.log_level = "INFO"
    return s


# ---------------------------------------------------------------------------
# 29.4.1 — settings=None (e.g. MODEL_MATRIX_PATH absent) → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_required_env_var_exits():
    """settings=None (required env vars absent) → lifespan calls sys.exit(1).

    When all three required env vars are absent, config.py assigns
    ``settings = None``. The lifespan guard detects this and exits.
    """
    app = create_app()
    with patch.object(main_mod, "settings", None):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 29.4.2 — INFERENCE_TIMEOUT_SECONDS=0 → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inference_timeout_zero_exits():
    """INFERENCE_TIMEOUT_SECONDS=0 is outside [1,600] → sys.exit(1)."""
    mock_settings = _valid_settings(str(REAL_RULES_YAML), str(REAL_MATRIX_YAML))
    mock_settings.inference_timeout_seconds = 0  # out of range

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 29.4.3 — INFERENCE_TIMEOUT_SECONDS=601 → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inference_timeout_601_exits():
    """INFERENCE_TIMEOUT_SECONDS=601 is outside [1,600] → sys.exit(1)."""
    mock_settings = _valid_settings(str(REAL_RULES_YAML), str(REAL_MATRIX_YAML))
    mock_settings.inference_timeout_seconds = 601  # out of range

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 29.4.4 — HEALTH_CHECK_TIMEOUT_SECONDS=31 → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_timeout_31_exits():
    """HEALTH_CHECK_TIMEOUT_SECONDS=31 is outside [1,30] → sys.exit(1)."""
    mock_settings = _valid_settings(str(REAL_RULES_YAML), str(REAL_MATRIX_YAML))
    mock_settings.health_check_timeout_seconds = 31  # out of range

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 29.4.5 — YAML file not found → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_yaml_not_found_exits():
    """Non-existent TASK_RULES_PATH → load_classifier_rules returns None → sys.exit(1)."""
    mock_settings = _valid_settings(
        "/tmp/does_not_exist_rules_xyz.yaml",   # bad rules path
        str(REAL_MATRIX_YAML),
    )

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover
    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_matrix_yaml_not_found_exits():
    """Non-existent MODEL_MATRIX_PATH → load_model_matrix returns None → sys.exit(1)."""
    mock_settings = _valid_settings(
        str(REAL_RULES_YAML),
        "/tmp/does_not_exist_matrix_xyz.yaml",  # bad matrix path
    )

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 29.4.6 — Valid config → app.state has classifier_rules, model_matrix, http_client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_config_sets_app_state(tmp_path):
    """Valid configuration → lifespan completes and app.state is fully populated.

    Copies the real YAML files into tmp_path so the test is isolated from
    changes to the repo-root files.
    """
    rules_file = tmp_path / "task_classifier_rules.yaml"
    matrix_file = tmp_path / "model_matrix.yaml"
    policy_file = tmp_path / "policy_matrix.yaml"
    shutil.copy(REAL_RULES_YAML, rules_file)
    shutil.copy(REAL_MATRIX_YAML, matrix_file)
    shutil.copy(REAL_POLICY_YAML, policy_file)

    mock_settings = _valid_settings(str(rules_file), str(matrix_file), str(policy_file))

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        async with app.router.lifespan_context(app):
            # All three state attributes must be populated
            assert app.state.classifier_rules is not None, (
                "app.state.classifier_rules must be set after successful startup"
            )
            assert app.state.model_matrix is not None, (
                "app.state.model_matrix must be set after successful startup"
            )
            assert app.state.http_client is not None, (
                "app.state.http_client must be set after successful startup"
            )
            # Quick sanity checks on loaded data
            assert app.state.classifier_rules.total_keyword_count > 0, (
                "classifier_rules must have at least one keyword"
            )
            assert len(app.state.model_matrix.models) > 0, (
                "model_matrix must have at least one model entry"
            )
    # After context exit, http_client is closed — no assertion needed here
