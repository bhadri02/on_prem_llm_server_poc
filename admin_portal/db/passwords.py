"""
admin_portal/db/passwords.py

Password hashing for real login (Phase 6). Uses stdlib PBKDF2-HMAC-SHA256
rather than pulling in bcrypt/passlib — adequate for POC purposes without a
new dependency. Stored format: "<salt_hex>$<digest_hex>".
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, digest_hex = stored.split("$", 1)
    try:
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    except ValueError:
        return False
    return hmac.compare_digest(check.hex(), digest_hex)
