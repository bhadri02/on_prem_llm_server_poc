"""Unit tests for intelligent_router.config — environment-driven Settings.

Tests construct Settings() directly with explicit keyword arguments rather
than relying on real environment variables, keeping tests hermetic and
avoiding any import-time side effects from the module-level singleton.
"""

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimum set of required kwargs to produce a valid Settings instance.
_REQUIRED = {
    "model_matrix_path": "/data/model_matrix.yaml",
    "task_rules_path": "/data/task_rules.yaml",
    "audit_store_url": "http://audit-store:9200",
}


def make_settings(**overrides):
    """Return a Settings instance constructed purely from keyword arguments.

    Pydantic-settings respects constructor kwargs before env-var lookup, so
    this approach never touches the real environment.
    """
    from intelligent_router.config import Settings

    kwargs = {**_REQUIRED, **overrides}
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# Required field validation
# ---------------------------------------------------------------------------


def test_missing_model_matrix_path_raises(monkeypatch):
    """MODEL_MATRIX_PATH absent → ValidationError."""
    from intelligent_router.config import Settings

    # Clear env so pydantic-settings cannot find the value there either
    monkeypatch.delenv("MODEL_MATRIX_PATH", raising=False)
    monkeypatch.setenv("TASK_RULES_PATH", "/data/task_rules.yaml")
    monkeypatch.setenv("AUDIT_STORE_URL", "http://audit-store:9200")

    with pytest.raises(ValidationError):
        Settings()


def test_missing_task_rules_path_raises(monkeypatch):
    """TASK_RULES_PATH absent → ValidationError."""
    from intelligent_router.config import Settings

    monkeypatch.setenv("MODEL_MATRIX_PATH", "/data/model_matrix.yaml")
    monkeypatch.delenv("TASK_RULES_PATH", raising=False)
    monkeypatch.setenv("AUDIT_STORE_URL", "http://audit-store:9200")

    with pytest.raises(ValidationError):
        Settings()


def test_missing_audit_store_url_raises(monkeypatch):
    """AUDIT_STORE_URL absent → ValidationError."""
    from intelligent_router.config import Settings

    monkeypatch.setenv("MODEL_MATRIX_PATH", "/data/model_matrix.yaml")
    monkeypatch.setenv("TASK_RULES_PATH", "/data/task_rules.yaml")
    monkeypatch.delenv("AUDIT_STORE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings()


# ---------------------------------------------------------------------------
# Optional fields — default values
# ---------------------------------------------------------------------------


def test_cache_url_default():
    """CACHE_URL unset → defaults to 'http://cache:8086'."""
    s = make_settings()
    assert s.cache_url == "http://cache:8086"


def test_inference_adapter_url_default():
    """INFERENCE_ADAPTER_URL unset → defaults to 'http://inference-adapter:8087'."""
    s = make_settings()
    assert s.inference_adapter_url == "http://inference-adapter:8087"


def test_log_level_default():
    """LOG_LEVEL unset → defaults to 'INFO' without raising."""
    s = make_settings()
    assert s.log_level == "INFO"


def test_inference_timeout_seconds_default():
    """INFERENCE_TIMEOUT_SECONDS unset → defaults to 120."""
    s = make_settings()
    assert s.inference_timeout_seconds == 120


def test_health_check_timeout_seconds_default():
    """HEALTH_CHECK_TIMEOUT_SECONDS unset → defaults to 5."""
    s = make_settings()
    assert s.health_check_timeout_seconds == 5


def test_port_default():
    """PORT unset → defaults to 8082."""
    s = make_settings()
    assert s.port == 8082


# ---------------------------------------------------------------------------
# Optional fields — override via constructor kwargs
# ---------------------------------------------------------------------------


def test_cache_url_override():
    """CACHE_URL override is reflected in the Settings instance."""
    s = make_settings(cache_url="http://my-cache:9999")
    assert s.cache_url == "http://my-cache:9999"


def test_log_level_override():
    """LOG_LEVEL override is reflected in the Settings instance."""
    s = make_settings(log_level="DEBUG")
    assert s.log_level == "DEBUG"


def test_inference_timeout_override():
    """INFERENCE_TIMEOUT_SECONDS override is reflected in the Settings instance."""
    s = make_settings(inference_timeout_seconds=60)
    assert s.inference_timeout_seconds == 60


# ---------------------------------------------------------------------------
# Module-level singleton smoke test
# ---------------------------------------------------------------------------


def test_module_singleton_is_settings_instance_when_env_vars_set(monkeypatch):
    """When all required env vars are present the singleton is a Settings instance."""
    import importlib

    monkeypatch.setenv("MODEL_MATRIX_PATH", "/tmp/mm.yaml")
    monkeypatch.setenv("TASK_RULES_PATH", "/tmp/tr.yaml")
    monkeypatch.setenv("AUDIT_STORE_URL", "http://audit:9200")

    import intelligent_router.config as cfg_module

    importlib.reload(cfg_module)

    from intelligent_router.config import Settings

    assert isinstance(cfg_module.settings, Settings)


def test_module_singleton_is_none_when_env_vars_absent(monkeypatch):
    """When required env vars are absent the guarded singleton is None (not a crash)."""
    import importlib

    monkeypatch.delenv("MODEL_MATRIX_PATH", raising=False)
    monkeypatch.delenv("TASK_RULES_PATH", raising=False)
    monkeypatch.delenv("AUDIT_STORE_URL", raising=False)

    import intelligent_router.config as cfg_module

    importlib.reload(cfg_module)

    # The guarded singleton is None — not a Settings instance — which is fine;
    # main.py lifespan handles the missing-var case via sys.exit(1).
    assert cfg_module.settings is None
