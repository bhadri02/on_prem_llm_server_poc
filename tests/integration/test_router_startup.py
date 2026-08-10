"""
tests/integration/test_router_startup.py

Integration tests for the Intelligent Router lifespan startup validation
(create_app() / lifespan handler in intelligent_router/main.py).

Covers subtask 29.4:
  - MODEL_MATRIX_PATH unset (settings=None) → sys.exit(1) with ERROR log
  - INFERENCE_TIMEOUT_SECONDS=0            → sys.exit(1)
  - INFERENCE_TIMEOUT_SECONDS=601          → sys.exit(1)
  - HEALTH_CHECK_TIMEOUT_SECONDS=31        → sys.exit(1)
  - YAML file not found                    → sys.exit(1)
  - Valid config                           → app.state has classifier_rules,
                                             model_matrix, http_client set

Strategy
--------
The lifespan is an async context manager obtained from
`app.router.lifespan_context`. We run it directly via:

    async with app.router.lifespan_context(app):
        ...

`patch.object(intelligent_router.config, "settings", <value>)` lets us inject
controlled settings without touching env vars.

Requirements: 14.2, 14.3, 14.4, 14.7, 14.8
"""

import shutil

import pytest
from unittest.mock import MagicMock, patch

import intelligent_router.config as cfg_mod
import intelligent_router.main as main_mod
from intelligent_router.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_mock_settings(
    matrix_path: str, rules_path: str, policy_path: str = "policy_matrix.yaml"
) -> MagicMock:
    """Return a fully-valid mock Settings for startup."""
    s = MagicMock()
    s.model_matrix_path = matrix_path
    s.task_rules_path = rules_path
    s.policy_matrix_path = policy_path
    s.audit_store_url = "http://audit-store:9200"
    s.cache_url = "http://cache:8086"
    s.inference_adapter_url = "http://inference-adapter:8087"
    s.inference_timeout_seconds = 30
    s.health_check_timeout_seconds = 5
    s.log_level = "INFO"
    return s


def _range_violation_settings(matrix_path: str, rules_path: str) -> MagicMock:
    """Base settings with valid YAML paths for numeric range tests."""
    s = _valid_mock_settings(matrix_path, rules_path)
    return s


# ---------------------------------------------------------------------------
# 29.4.1 — settings is None (required env vars missing) → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_none_exits():
    """settings=None (required env vars absent) → lifespan must call sys.exit(1).

    Validates: Requirements 14.2 (startup validation on missing required vars)
    """
    app = create_app()
    # Patch the module-level `settings` in main.py (where the lifespan reads it)
    with patch.object(main_mod, "settings", None):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1), got code={exc_info.value.code}"
    )


# ---------------------------------------------------------------------------
# 29.4.2 — INFERENCE_TIMEOUT_SECONDS=0 → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inference_timeout_zero_exits(tmp_path):
    """INFERENCE_TIMEOUT_SECONDS=0 is out of range [1,600] → sys.exit(1).

    Validates: Requirements 14.3 (numeric range validation)
    """
    # Provide real YAML files so the failure is purely the timeout range
    rules_file = tmp_path / "rules.yaml"
    matrix_file = tmp_path / "matrix.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)

    mock_settings = _range_violation_settings(str(matrix_file), str(rules_file))
    mock_settings.inference_timeout_seconds = 0  # OUT OF RANGE

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) for inference_timeout=0, got code={exc_info.value.code}"
    )


# ---------------------------------------------------------------------------
# 29.4.3 — INFERENCE_TIMEOUT_SECONDS=601 → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inference_timeout_601_exits(tmp_path):
    """INFERENCE_TIMEOUT_SECONDS=601 exceeds max 600 → sys.exit(1).

    Validates: Requirements 14.3 (numeric range validation)
    """
    rules_file = tmp_path / "rules.yaml"
    matrix_file = tmp_path / "matrix.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)

    mock_settings = _range_violation_settings(str(matrix_file), str(rules_file))
    mock_settings.inference_timeout_seconds = 601  # OUT OF RANGE

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) for inference_timeout=601, got code={exc_info.value.code}"
    )


# ---------------------------------------------------------------------------
# 29.4.4 — HEALTH_CHECK_TIMEOUT_SECONDS=31 → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_check_timeout_31_exits(tmp_path):
    """HEALTH_CHECK_TIMEOUT_SECONDS=31 exceeds max 30 → sys.exit(1).

    Validates: Requirements 14.4 (numeric range validation)
    """
    rules_file = tmp_path / "rules.yaml"
    matrix_file = tmp_path / "matrix.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)

    mock_settings = _range_violation_settings(str(matrix_file), str(rules_file))
    mock_settings.health_check_timeout_seconds = 31  # OUT OF RANGE

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) for health_check_timeout=31, got code={exc_info.value.code}"
    )


# ---------------------------------------------------------------------------
# 29.4.5 — YAML file not found → sys.exit(1)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_yaml_file_not_found_exits():
    """Non-existent YAML paths → load_classifier_rules/load_model_matrix return None → sys.exit(1).

    Validates: Requirements 14.7 (YAML loading failure)
    """
    mock_settings = MagicMock()
    mock_settings.model_matrix_path = "/tmp/does_not_exist_matrix_xyz.yaml"
    mock_settings.task_rules_path = "/tmp/does_not_exist_rules_xyz.yaml"
    mock_settings.audit_store_url = "http://audit-store:9200"
    mock_settings.inference_timeout_seconds = 30
    mock_settings.health_check_timeout_seconds = 5
    mock_settings.log_level = "INFO"

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        with pytest.raises(SystemExit) as exc_info:
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover

    assert exc_info.value.code == 1, (
        f"Expected sys.exit(1) for missing YAML, got code={exc_info.value.code}"
    )


# ---------------------------------------------------------------------------
# 29.4.6 — Valid config → app.state fully populated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_valid_config_sets_app_state(tmp_path):
    """Valid config with real YAML files → app.state has classifier_rules, model_matrix, http_client.

    Validates: Requirements 14.8 (successful startup state)
    """
    rules_file = tmp_path / "rules.yaml"
    matrix_file = tmp_path / "matrix.yaml"
    policy_file = tmp_path / "policy.yaml"
    shutil.copy("task_classifier_rules.yaml", rules_file)
    shutil.copy("model_matrix.yaml", matrix_file)
    shutil.copy("policy_matrix.yaml", policy_file)

    mock_settings = _valid_mock_settings(str(matrix_file), str(rules_file), str(policy_file))

    app = create_app()
    with patch.object(main_mod, "settings", mock_settings):
        async with app.router.lifespan_context(app):
            # All three required state attributes must be populated
            assert app.state.classifier_rules is not None, (
                "app.state.classifier_rules must be set after successful startup"
            )
            assert app.state.model_matrix is not None, (
                "app.state.model_matrix must be set after successful startup"
            )
            assert app.state.http_client is not None, (
                "app.state.http_client must be set after successful startup"
            )
            # Spot-check loaded content
            assert "llama3.2:3b" in app.state.model_matrix.models, (
                "model_matrix must contain the llama3.2:3b entry from model_matrix.yaml"
            )
            assert app.state.classifier_rules.total_keyword_count > 0, (
                "classifier_rules must have at least one keyword loaded"
            )
