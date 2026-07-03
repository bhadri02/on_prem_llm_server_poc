"""
tests/test_startup_validation.py

Unit tests for FastAPI lifespan startup validation (Task 9.2).

Validates that the lifespan handler calls sys.exit(1) when:
  - ROUTER_URL is missing or empty
  - GATEWAY_API_KEY is missing or empty
  - MAX_AGENT_STEPS is out of range (0 or 51)
  - TOOL_CATALOG_PATH points to a non-existent file
  - TOOL_CATALOG_PATH points to a malformed YAML file

Requirements: 5.3
"""

import sys
import tempfile
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_settings(**overrides) -> MagicMock:
    """Create a mock Settings object with valid defaults and apply overrides."""
    s = MagicMock()
    s.router_url = "http://router:8082"
    s.gateway_api_key = "poc-secret-key"
    s.tool_catalog_path = "/config/tools/catalog.yaml"
    s.max_agent_steps = 10
    s.port = 8083
    s.metrics_port = 9090
    for key, val in overrides.items():
        setattr(s, key, val)
    return s


def _make_valid_catalog_file() -> str:
    """Write a valid catalog.yaml to a temp file and return its path."""
    content = """
tools:
  - name: "calculator"
    description: "Evaluate a math expression"
    parameters:
      expression:
        type: string
        required: true
  - name: "get_current_time"
    description: "Get UTC time"
    parameters: {}
  - name: "web_search"
    description: "Search (mocked)"
    parameters:
      query:
        type: string
        required: true
"""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.flush()
    f.close()
    return f.name


async def _run_lifespan(settings_obj, tool_registry_return=None):
    """
    Run the lifespan context manager with patched settings and load_tool_registry.

    Returns True if lifespan completed normally (yielded), or raises SystemExit
    if sys.exit(1) was called.
    """
    from agent_framework.main import lifespan
    from fastapi import FastAPI

    app = FastAPI()

    with patch("agent_framework.main.settings", settings_obj):
        with patch(
            "agent_framework.main.load_tool_registry",
            return_value=tool_registry_return,
        ):
            async with lifespan(app):
                return True


# ---------------------------------------------------------------------------
# Missing / empty required env vars → sys.exit(1)
# ---------------------------------------------------------------------------


class TestLifespanMissingRequiredVars:
    """Req 5.3: Missing or empty required env vars must cause sys.exit(1)."""

    @pytest.mark.asyncio
    async def test_missing_router_url_exits(self):
        """Empty ROUTER_URL must cause sys.exit(1)."""
        s = _make_mock_settings(router_url="")
        # Provide a valid tool registry so the check doesn't fail there
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_none_router_url_exits(self):
        """None ROUTER_URL must cause sys.exit(1)."""
        s = _make_mock_settings(router_url=None)
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_missing_gateway_api_key_exits(self):
        """Empty GATEWAY_API_KEY must cause sys.exit(1)."""
        s = _make_mock_settings(gateway_api_key="")
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_none_gateway_api_key_exits(self):
        """None GATEWAY_API_KEY must cause sys.exit(1)."""
        s = _make_mock_settings(gateway_api_key=None)
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_missing_tool_catalog_path_exits(self):
        """Empty TOOL_CATALOG_PATH must cause sys.exit(1)."""
        s = _make_mock_settings(tool_catalog_path="")
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# MAX_AGENT_STEPS out of range → sys.exit(1)
# ---------------------------------------------------------------------------


class TestLifespanMaxAgentStepsRange:
    """Req 5.3 / design: max_agent_steps must be in [1, 50]."""

    @pytest.mark.asyncio
    async def test_max_steps_zero_exits(self):
        """MAX_AGENT_STEPS=0 must cause sys.exit(1)."""
        s = _make_mock_settings(max_agent_steps=0)
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_max_steps_51_exits(self):
        """MAX_AGENT_STEPS=51 must cause sys.exit(1)."""
        s = _make_mock_settings(max_agent_steps=51)
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_max_steps_negative_exits(self):
        """Negative MAX_AGENT_STEPS must cause sys.exit(1)."""
        s = _make_mock_settings(max_agent_steps=-1)
        mock_registry = {"calculator": MagicMock()}

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=mock_registry)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_max_steps_1_succeeds(self):
        """MAX_AGENT_STEPS=1 (boundary value) must succeed."""
        s = _make_mock_settings(max_agent_steps=1)
        mock_registry = {"calculator": MagicMock()}

        # Should not raise — lifespan completes normally
        result = await _run_lifespan(s, tool_registry_return=mock_registry)
        assert result is True

    @pytest.mark.asyncio
    async def test_max_steps_50_succeeds(self):
        """MAX_AGENT_STEPS=50 (boundary value) must succeed."""
        s = _make_mock_settings(max_agent_steps=50)
        mock_registry = {"calculator": MagicMock()}

        result = await _run_lifespan(s, tool_registry_return=mock_registry)
        assert result is True

    @pytest.mark.asyncio
    async def test_max_steps_10_succeeds(self):
        """MAX_AGENT_STEPS=10 (default) must succeed."""
        s = _make_mock_settings(max_agent_steps=10)
        mock_registry = {"calculator": MagicMock()}

        result = await _run_lifespan(s, tool_registry_return=mock_registry)
        assert result is True


# ---------------------------------------------------------------------------
# Tool catalog failures → sys.exit(1)
# ---------------------------------------------------------------------------


class TestLifespanToolCatalogFailures:
    """Req 5.3: load_tool_registry returning None must cause sys.exit(1)."""

    @pytest.mark.asyncio
    async def test_registry_none_exits(self):
        """load_tool_registry() returning None must cause sys.exit(1)."""
        s = _make_mock_settings()

        with pytest.raises(SystemExit) as exc_info:
            await _run_lifespan(s, tool_registry_return=None)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_registry_none_from_missing_file(self):
        """
        Missing TOOL_CATALOG_PATH file → sys.exit(1).
        Uses the real load_tool_registry so the FileNotFoundError path is exercised.
        """
        s = _make_mock_settings(tool_catalog_path="/nonexistent/path/catalog.yaml")

        with pytest.raises(SystemExit) as exc_info:
            from agent_framework.main import lifespan
            from fastapi import FastAPI

            app = FastAPI()
            with patch("agent_framework.main.settings", s):
                # Load_tool_registry is NOT patched here — it will encounter FileNotFoundError
                async with lifespan(app):
                    pass
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_malformed_yaml_exits(self):
        """
        Malformed YAML at TOOL_CATALOG_PATH → sys.exit(1).
        This test creates a temporary file with invalid YAML syntax.
        """
        import tempfile

        # Create a file with malformed YAML
        malformed_yaml = "tools:\n  - name: calculator\n    invalid yaml: [unclosed"
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        f.write(malformed_yaml)
        f.flush()
        temp_path = f.name
        f.close()

        try:
            s = _make_mock_settings(tool_catalog_path=temp_path)

            # Unpatch load_tool_registry so it actually tries to load the file
            from agent_framework.tools.registry import load_tool_registry

            with patch("agent_framework.main.settings", s):
                # Call the real load_tool_registry which should return None
                with pytest.raises(SystemExit) as exc_info:
                    from agent_framework.main import lifespan
                    from fastapi import FastAPI

                    app = FastAPI()
                    async with lifespan(app):
                        pass
                assert exc_info.value.code == 1
        finally:
            # Cleanup
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_valid_registry_succeeds(self):
        """A non-None registry with valid settings must not exit."""
        s = _make_mock_settings()
        mock_registry = {
            "calculator": MagicMock(),
            "get_current_time": MagicMock(),
            "web_search": MagicMock(),
        }

        result = await _run_lifespan(s, tool_registry_return=mock_registry)
        assert result is True


# ---------------------------------------------------------------------------
# Startup state is stored on app.state
# ---------------------------------------------------------------------------


class TestLifespanAppState:
    """After successful startup, app.state should hold settings and tool_registry."""

    @pytest.mark.asyncio
    async def test_app_state_has_settings_and_registry(self):
        """app.state.settings and app.state.tool_registry are set on startup."""
        from agent_framework.main import lifespan
        from fastapi import FastAPI

        s = _make_mock_settings()
        mock_registry = {"calculator": MagicMock()}
        app = FastAPI()

        with patch("agent_framework.main.settings", s):
            with patch(
                "agent_framework.main.load_tool_registry",
                return_value=mock_registry,
            ):
                async with lifespan(app):
                    assert app.state.settings is s
                    assert app.state.tool_registry is mock_registry
