"""
JSON file storage manager for the Model Registry.

Implements JsonFileManager: an in-memory cache of ModelRecord objects
backed by an atomic write strategy (write to .tmp then os.replace) against
a JSON file on the PersistentVolume (STORAGE_PATH). Provides load(), get_all(),
get_by_name(), get_by_task(), add(), update_status(), storage_reachable(),
and the private _persist() atomic write helper.
"""

import json
import os
import sys
from datetime import datetime

from model_registry.exceptions import DuplicateNameError, ModelNotFoundError, PersistenceError
from model_registry.schemas.model import ModelRecord, ModelStatus, TaskType


class JsonFileManager:
    """
    In-memory cache of ModelRecord objects with atomic JSON file persistence.

    The manager holds the authoritative dict of ModelRecord objects keyed by
    model name. All reads are served from memory; writes flush the full list to
    disk atomically via a .tmp file + os.replace rename.
    """

    def __init__(self, storage_path: str) -> None:
        """
        Initialise the manager with a path to the backing JSON file.

        Args:
            storage_path: Absolute or relative path to models.json on the
                          PersistentVolume (value of STORAGE_PATH env var).
        """
        self._storage_path: str = storage_path
        self._records: dict[str, ModelRecord] = {}
        self._storage_ok: bool = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        Load records from STORAGE_PATH into memory.

        Startup logic:
          1. If file missing: create parent dirs, write "[]", set _records = {},
             set _storage_ok = True.
          2. If file readable: parse JSON, validate each dict as ModelRecord,
             build _records dict keyed by name, set _storage_ok = True.
          3. If I/O error or JSON parse error: attempt to overwrite with "[]";
             if that also fails, log structured error then sys.exit(1);
             if overwrite succeeds, set _records = {} and _storage_ok = True.
        """
        if not os.path.exists(self._storage_path):
            # File missing — create empty store
            try:
                parent_dir = os.path.dirname(self._storage_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(self._storage_path, "w", encoding="utf-8") as fh:
                    fh.write("[]")
                self._records = {}
                self._storage_ok = True
            except OSError as exc:
                self._log_error(
                    event="storage_init_failed",
                    message=f"Could not create storage file: {exc}",
                )
                sys.exit(1)
            return

        # File exists — attempt to read and parse it
        try:
            with open(self._storage_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
            data = json.loads(raw)
            records: dict[str, ModelRecord] = {}
            for item in data:
                record = ModelRecord.model_validate(item)
                records[record.name] = record
            self._records = records
            self._storage_ok = True
        except (OSError, json.JSONDecodeError, Exception) as exc:
            # Attempt recovery: overwrite with "[]"
            try:
                with open(self._storage_path, "w", encoding="utf-8") as fh:
                    fh.write("[]")
                self._records = {}
                self._storage_ok = True
            except OSError as overwrite_exc:
                self._log_error(
                    event="storage_unrecoverable",
                    message=(
                        f"Failed to read storage ({exc}) and could not "
                        f"overwrite with empty store: {overwrite_exc}"
                    ),
                )
                sys.exit(1)

    # ------------------------------------------------------------------
    # Read operations (never raise)
    # ------------------------------------------------------------------

    def get_all(self) -> list[ModelRecord]:
        """
        Return all stored ModelRecord objects.

        Returns:
            A list of all records currently in memory. Never raises.
        """
        return list(self._records.values())

    def get_by_name(self, name: str) -> ModelRecord | None:
        """
        Return the ModelRecord with the given name, or None if absent.

        Args:
            name: Exact model name (case-sensitive).

        Returns:
            The matching ModelRecord, or None. Never raises.
        """
        return self._records.get(name)

    def get_by_task(self, task_type: TaskType) -> list[ModelRecord]:
        """
        Return all active ModelRecords that support the given task type.

        A record is included if and only if:
          - task_type is in record.tasks
          - record.status == ModelStatus.active

        Args:
            task_type: The TaskType enum value to filter by.

        Returns:
            A (possibly empty) list of matching ModelRecord objects. Never raises.
        """
        return [
            record
            for record in self._records.values()
            if task_type in record.tasks and record.status == ModelStatus.active
        ]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add(self, record: ModelRecord) -> ModelRecord:
        """
        Add a new ModelRecord to the store.

        Auto-populates registered_at with the current UTC ISO-8601 timestamp
        if the caller did not supply one. Calls _persist() after updating
        in-memory state; rolls back if _persist() raises.

        Args:
            record: The ModelRecord to add.

        Returns:
            The stored ModelRecord (with registered_at populated).

        Raises:
            DuplicateNameError: If a record with the same name already exists.
            PersistenceError: If the atomic write to STORAGE_PATH fails.
        """
        if record.name in self._records:
            raise DuplicateNameError(record.name)

        # Auto-populate registered_at if absent
        if not record.registered_at:
            record = record.model_copy(
                update={"registered_at": datetime.utcnow().isoformat() + "Z"}
            )

        # Update in-memory state
        self._records[record.name] = record

        # Attempt atomic persist — roll back on failure
        try:
            self._persist()
        except PersistenceError:
            # Roll back: remove the newly added entry
            del self._records[record.name]
            raise

        return record

    def update_status(self, name: str, status: ModelStatus) -> ModelRecord:
        """
        Update the status of an existing ModelRecord.

        Calls _persist() after updating in-memory state; rolls back the status
        change if _persist() raises.

        Args:
            name: Exact name of the model to update.
            status: The new ModelStatus value.

        Returns:
            The updated ModelRecord.

        Raises:
            ModelNotFoundError: If no record with the given name exists.
            PersistenceError: If the atomic write to STORAGE_PATH fails.
        """
        if name not in self._records:
            raise ModelNotFoundError(name)

        # Save original for potential rollback
        original_record = self._records[name]
        original_status = original_record.status

        # Update in-memory state
        updated_record = original_record.model_copy(update={"status": status})
        self._records[name] = updated_record

        # Attempt atomic persist — roll back on failure
        try:
            self._persist()
        except PersistenceError:
            # Roll back: restore original status
            self._records[name] = original_record.model_copy(
                update={"status": original_status}
            )
            raise

        return updated_record

    def update_api_key(self, name: str, api_key: str) -> ModelRecord:
        """
        Update the api_key of an existing ModelRecord.

        Calls _persist() after updating in-memory state; rolls back the
        change if _persist() raises. Mirrors update_status().

        Args:
            name:    Exact name of the model to update.
            api_key: The new provider API key (plaintext).

        Returns:
            The updated ModelRecord.

        Raises:
            ModelNotFoundError: If no record with the given name exists.
            PersistenceError: If the atomic write to STORAGE_PATH fails.
        """
        if name not in self._records:
            raise ModelNotFoundError(name)

        original_record = self._records[name]
        original_api_key = original_record.api_key

        updated_record = original_record.model_copy(update={"api_key": api_key})
        self._records[name] = updated_record

        try:
            self._persist()
        except PersistenceError:
            self._records[name] = original_record.model_copy(
                update={"api_key": original_api_key}
            )
            raise

        return updated_record

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def storage_reachable(self) -> bool:
        """
        Check whether STORAGE_PATH exists and is readable.

        Returns:
            True if the file exists and is readable; False otherwise.
        """
        return os.path.exists(self._storage_path) and os.access(
            self._storage_path, os.R_OK
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """
        Atomically write the full in-memory record list to STORAGE_PATH.

        Steps:
          1. Serialise _records.values() to JSON (indent=2, ensure_ascii=False).
          2. Compute temp path as storage_path + ".tmp".
          3. Write bytes to temp path.
          4. Call os.replace(tmp_path, storage_path).
          5. On OSError: attempt silent os.unlink(tmp_path), log structured
             error, raise PersistenceError. Does NOT update in-memory state —
             the caller is responsible for any rollback.

        Raises:
            PersistenceError: If writing or renaming the temp file fails.
        """
        records_list = [r.model_dump(mode="json") for r in self._records.values()]
        json_bytes = json.dumps(records_list, indent=2, ensure_ascii=False).encode(
            "utf-8"
        )

        tmp_path = self._storage_path + ".tmp"

        try:
            with open(tmp_path, "wb") as fh:
                fh.write(json_bytes)
            os.replace(tmp_path, self._storage_path)
        except OSError as exc:
            # Attempt silent cleanup of the temp file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

            self._log_error(
                event="persist_failed",
                message=str(exc),
            )
            raise PersistenceError(
                message=str(exc),
                model_name=None,
            ) from exc

    def _log_error(self, event: str, message: str) -> None:
        """
        Emit a structured JSON error entry to stdout.

        Args:
            event: A short event identifier string.
            message: Human-readable error description.
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": "ERROR",
            "event": event,
            "storage_path": self._storage_path,
            "message": message,
        }
        print(json.dumps(entry), flush=True)
