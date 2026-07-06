"""
Unit tests for JsonFileManager (storage/json_file_manager.py).

Tests cover: startup with missing file, valid file, and malformed JSON; add()
round-trip, duplicate detection, update_status() not-found, and _persist()
failure with rollback.

Validates: Requirements 1.3, 1.4, 1.12, 12.2
"""

import json
import os
from unittest.mock import patch

import pytest

from model_registry.exceptions import (
    DuplicateNameError,
    ModelNotFoundError,
    PersistenceError,
)
from model_registry.schemas.model import ModelRecord, ModelStatus, TaskType
from model_registry.storage.json_file_manager import JsonFileManager


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_record(name: str = "test-model", **overrides) -> ModelRecord:
    """Build a minimal valid ModelRecord for testing."""
    defaults = dict(
        name=name,
        version="1.0",
        backend="ollama",
        endpoint="http://localhost:11434",
        tasks=[TaskType.chat],
        status=ModelStatus.active,
        registered_at="2026-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return ModelRecord(**defaults)


# ---------------------------------------------------------------------------
# Startup: missing file
# ---------------------------------------------------------------------------


def test_load_missing_file_creates_empty_store(tmp_path):
    """load() on a non-existent path creates the file with '[]' and an empty store."""
    storage_path = str(tmp_path / "models.json")
    mgr = JsonFileManager(storage_path)

    mgr.load()

    # In-memory state is empty
    assert mgr.get_all() == []
    # File was created with empty JSON array
    assert os.path.exists(storage_path)
    content = open(storage_path).read().strip()
    assert content == "[]"


# ---------------------------------------------------------------------------
# Startup: valid JSON file
# ---------------------------------------------------------------------------


def test_load_valid_json_loads_records(tmp_path):
    """load() with a valid models.json parses and loads all records into memory."""
    storage_path = str(tmp_path / "models.json")

    record = make_record("llama3.2:3b")
    with open(storage_path, "w", encoding="utf-8") as fh:
        json.dump([record.model_dump(mode="json")], fh)

    mgr = JsonFileManager(storage_path)
    mgr.load()

    records = mgr.get_all()
    assert len(records) == 1
    assert records[0].name == "llama3.2:3b"
    assert records[0].version == "1.0"


# ---------------------------------------------------------------------------
# Startup: malformed JSON
# ---------------------------------------------------------------------------


def test_load_malformed_json_recovers(tmp_path):
    """load() with malformed JSON overwrites the file with '[]' and continues."""
    storage_path = str(tmp_path / "models.json")

    with open(storage_path, "w", encoding="utf-8") as fh:
        fh.write("not valid json{{{")

    mgr = JsonFileManager(storage_path)
    mgr.load()  # should NOT raise or call sys.exit

    # In-memory state is empty
    assert mgr.get_all() == []
    # File was recovered to a valid empty array
    content = open(storage_path).read().strip()
    assert content == "[]"


# ---------------------------------------------------------------------------
# add(): persistence round-trip
# ---------------------------------------------------------------------------


def test_add_persists_to_disk(tmp_path):
    """add() writes the record to disk; a fresh manager loading the same file
    returns the same record."""
    storage_path = str(tmp_path / "models.json")

    mgr = JsonFileManager(storage_path)
    mgr.load()

    record = make_record("my-model")
    mgr.add(record)

    # Reload with a fresh instance
    mgr2 = JsonFileManager(storage_path)
    mgr2.load()
    records = mgr2.get_all()

    assert len(records) == 1
    assert records[0].name == "my-model"
    assert records[0].version == record.version
    assert records[0].backend == record.backend


# ---------------------------------------------------------------------------
# add(): duplicate name
# ---------------------------------------------------------------------------


def test_add_raises_duplicate_name_error(tmp_path):
    """add() raises DuplicateNameError when a record with the same name exists."""
    storage_path = str(tmp_path / "models.json")

    mgr = JsonFileManager(storage_path)
    mgr.load()

    record = make_record("duplicate")
    mgr.add(record)

    with pytest.raises(DuplicateNameError) as exc_info:
        mgr.add(make_record("duplicate", version="2.0"))

    assert "duplicate" in str(exc_info.value)
    # Store still has only one record
    assert len(mgr.get_all()) == 1


# ---------------------------------------------------------------------------
# update_status(): not-found
# ---------------------------------------------------------------------------


def test_update_status_raises_model_not_found(tmp_path):
    """update_status() raises ModelNotFoundError for an unknown model name."""
    storage_path = str(tmp_path / "models.json")

    mgr = JsonFileManager(storage_path)
    mgr.load()

    with pytest.raises(ModelNotFoundError) as exc_info:
        mgr.update_status("nonexistent", ModelStatus.retired)

    assert "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _persist(): failure causes PersistenceError + rollback
# ---------------------------------------------------------------------------


def test_persist_failure_raises_and_rolls_back(tmp_path):
    """When os.replace raises OSError, add() re-raises PersistenceError
    and the new record is NOT present in the in-memory store."""
    storage_path = str(tmp_path / "models.json")

    mgr = JsonFileManager(storage_path)
    mgr.load()

    record = make_record("should-not-persist")

    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(PersistenceError):
            mgr.add(record)

    # Rollback: record must not appear in the store
    assert mgr.get_by_name("should-not-persist") is None
    assert len(mgr.get_all()) == 0
