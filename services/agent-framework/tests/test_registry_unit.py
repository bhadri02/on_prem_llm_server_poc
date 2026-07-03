# Feature: agent-framework — Unit tests for tool registry
# Requirements: 5.2, 5.3
"""
tests/test_registry_unit.py

Unit tests for `tools/registry.py` covering `load_tool_registry`.

Requirements covered:
  5.2 — Tool registry loads valid catalog and maps tool names to implementations
  5.3 — Tool registry returns None and logs ERROR for invalid/missing catalog data
"""

import logging
import pathlib

import pytest
from langchain_core.tools import BaseTool

from tools.registry import load_tool_registry

# Absolute path to the real catalog.yaml shipped with the service
CATALOG_PATH = str(pathlib.Path(__file__).parent.parent / "tools" / "catalog.yaml")


# ---------------------------------------------------------------------------
# 1. Valid catalog
# ---------------------------------------------------------------------------


class TestLoadToolRegistryValid:
    """Validates Requirement 5.2 — loading the real catalog succeeds."""

    def test_valid_catalog_returns_dict_with_expected_keys(self):
        """Loading catalog.yaml returns a dict with exactly the three known tool keys,
        each mapping to a BaseTool instance."""
        registry = load_tool_registry(CATALOG_PATH)

        assert registry is not None, "Expected a registry dict, got None"
        assert isinstance(registry, dict)
        assert set(registry.keys()) == {"calculator", "get_current_time", "web_search"}
        for name, tool in registry.items():
            assert isinstance(tool, BaseTool), (
                f"Expected BaseTool for '{name}', got {type(tool)}"
            )


# ---------------------------------------------------------------------------
# 2. Missing required fields
# ---------------------------------------------------------------------------


class TestLoadToolRegistryMissingFields:
    """Validates Requirement 5.3 — entries missing name or description → None + ERROR log."""

    def test_missing_name_returns_none_and_logs_error(self, tmp_path, caplog):
        """A catalog entry with no 'name' key causes load_tool_registry to return None
        and emit an ERROR-level log record."""
        catalog = tmp_path / "catalog_no_name.yaml"
        catalog.write_text(
            "tools:\n"
            "  - description: 'A tool with no name'\n"
        )

        with caplog.at_level(logging.ERROR, logger="tools.registry"):
            result = load_tool_registry(str(catalog))

        assert result is None
        assert any(r.levelno == logging.ERROR for r in caplog.records), (
            "Expected at least one ERROR log record"
        )

    def test_missing_description_returns_none_and_logs_error(self, tmp_path, caplog):
        """A catalog entry with no 'description' key causes load_tool_registry to return
        None and emit an ERROR-level log record."""
        catalog = tmp_path / "catalog_no_desc.yaml"
        catalog.write_text(
            "tools:\n"
            "  - name: 'calculator'\n"
        )

        with caplog.at_level(logging.ERROR, logger="tools.registry"):
            result = load_tool_registry(str(catalog))

        assert result is None
        assert any(r.levelno == logging.ERROR for r in caplog.records), (
            "Expected at least one ERROR log record"
        )


# ---------------------------------------------------------------------------
# 3. No implementation for the declared tool name
# ---------------------------------------------------------------------------


class TestLoadToolRegistryNoImplementation:
    """Validates Requirement 5.3 — unknown tool name → None + ERROR log mentioning the name."""

    def test_unknown_tool_name_returns_none_and_logs_error(self, tmp_path, caplog):
        """A catalog entry whose name has no corresponding implementation in impl_map
        causes load_tool_registry to return None and emit an ERROR containing the tool name."""
        catalog = tmp_path / "catalog_unknown.yaml"
        catalog.write_text(
            "tools:\n"
            "  - name: 'unknown_tool'\n"
            "    description: 'A tool with no implementation'\n"
        )

        with caplog.at_level(logging.ERROR, logger="tools.registry"):
            result = load_tool_registry(str(catalog))

        assert result is None
        error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert any("unknown_tool" in msg for msg in error_messages), (
            f"Expected ERROR mentioning 'unknown_tool', got: {error_messages}"
        )


# ---------------------------------------------------------------------------
# 4. File-level errors
# ---------------------------------------------------------------------------


class TestLoadToolRegistryFileErrors:
    """Validates Requirement 5.3 — file-not-found and malformed YAML → None + ERROR log."""

    def test_file_not_found_returns_none_and_logs_error(self, tmp_path, caplog):
        """Passing a path that does not exist causes load_tool_registry to return None
        and emit an ERROR-level log record."""
        nonexistent = str(tmp_path / "does_not_exist.yaml")

        with caplog.at_level(logging.ERROR, logger="tools.registry"):
            result = load_tool_registry(nonexistent)

        assert result is None
        assert any(r.levelno == logging.ERROR for r in caplog.records), (
            "Expected at least one ERROR log record for missing file"
        )

    def test_malformed_yaml_returns_none_and_logs_error(self, tmp_path, caplog):
        """A file containing invalid YAML causes load_tool_registry to return None
        and emit an ERROR-level log record."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(": invalid: yaml: ::::\n")

        with caplog.at_level(logging.ERROR, logger="tools.registry"):
            result = load_tool_registry(str(bad_yaml))

        assert result is None
        assert any(r.levelno == logging.ERROR for r in caplog.records), (
            "Expected at least one ERROR log record for malformed YAML"
        )
