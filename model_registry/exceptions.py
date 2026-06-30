"""
Custom exception classes for the Model Registry.

Defines DuplicateNameError, ModelNotFoundError, and PersistenceError.
These are raised by the storage layer and translated to HTTP responses
(409, 404, 500 respectively) by exception handlers registered on the FastAPI app.
"""


class DuplicateNameError(Exception):
    """Raised when a POST /models request contains a name that already exists."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Model with name '{name}' already exists.")


class ModelNotFoundError(Exception):
    """Raised when a requested model name is not found in the store."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Model '{name}' not found.")


class PersistenceError(Exception):
    """Raised when an atomic write to STORAGE_PATH fails."""

    def __init__(self, message: str, model_name: str | None = None) -> None:
        self.model_name = model_name
        super().__init__(f"Storage write failed. {message}")
