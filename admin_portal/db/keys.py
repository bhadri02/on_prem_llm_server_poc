"""
admin_portal/db/keys.py

Shared API-key generation/hashing helpers, used by both the startup seed
(db/seed.py) and the admin key-management endpoints (routers/users.py).
Raw key values are never persisted — only their SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_raw_key() -> str:
    """Generate a fresh random raw API key. Shown to the caller exactly once."""
    return secrets.token_urlsafe(32)


def hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw API key — the only form persisted to the DB."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    """First 8 characters of the raw key — safe to store/display without exposing the secret."""
    return raw_key[:8]
