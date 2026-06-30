"""
Custom exception hierarchy for the Cache Service (Layer 4).

All exceptions store their constructor kwargs as instance attributes so that
callers can build structured log entries without re-parsing the exception message.

Exception hierarchy:
    CacheServiceError          (base)
    ├── RedisUnavailableError  (redis.RedisError in service calls)
    ├── EmbeddingLoadError     (SentenceTransformer failed to load)
    └── EmbeddingEncodeError   (encode() raised any exception)

Validates: Requirements 1.8, 1.9, 2.8, 2.9, 4.7, 6.1
"""

from __future__ import annotations


class CacheServiceError(Exception):
    """
    Base exception for all Cache Service errors.

    Args:
        message: Human-readable description of the error (optional).
    """

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


class RedisUnavailableError(CacheServiceError):
    """
    Raised when a Redis call raises ``redis.RedisError``.

    Stores ``operation`` (e.g. ``"read"`` or ``"write"``) as an instance
    attribute for use in structured log entries.

    Args:
        message:   Human-readable description (optional).
        operation: The cache operation that was being attempted, e.g.
                   ``"read"`` or ``"write"`` (optional).
    """

    def __init__(self, message: str = "", *, operation: str | None = None) -> None:
        self.operation = operation
        super().__init__(message)


class EmbeddingLoadError(CacheServiceError):
    """
    Raised when the ``SentenceTransformer`` model fails to load.

    Args:
        message: Human-readable description (optional).
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)


class EmbeddingEncodeError(CacheServiceError):
    """
    Raised when ``EmbeddingGenerator.encode()`` raises any exception.

    Args:
        message: Human-readable description (optional).
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
