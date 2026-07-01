"""
content_safety.py — Content safety filter for the Security & Governance Layer.

Provides:

- ``BLOCKLIST``: a hardcoded POC list of clearly unsafe keywords used as the
  default blocked-word list.
- ``check_content_safety``: per-request check that concatenates all message
  ``content`` fields and tests for case-insensitive substring matches against
  every entry in the supplied blocklist.
"""

from security_layer.logging_config import get_logger

logger = get_logger(__name__)

# POC blocklist — hardcoded clearly unsafe keywords.
# In production this would be loaded from a configurable source (e.g. Vault,
# a database, or a config file) so it can be updated without redeployment.
BLOCKLIST: list[str] = [
    "bomb",
    "exploit",
    "malware",
    "ransomware",
    "hack",
    "phishing",
    "terrorism",
    "violence",
    "weapons",
    "illegal drugs",
    "child abuse",
    "genocide",
]


def check_content_safety(messages: list[dict], blocklist: list[str]) -> bool:
    """Check messages against a blocked-word list.

    Concatenates the ``content`` field of every message (separated by a single
    space) and converts the result to lowercase, then tests whether any entry
    in *blocklist* appears as a case-insensitive substring.

    Returns:
        ``True`` (safe) if no blocklisted word is found in the concatenated
        text.  ``False`` (unsafe) on the first match.  Also returns ``True``
        when *blocklist* is empty, but logs a WARNING to alert operators that
        content safety is effectively disabled.

    Args:
        messages: List of message dicts; each dict may contain a ``content``
            key whose value is the text to scan.
        blocklist: List of blocked-word strings to test as case-insensitive
            substrings.
    """
    if not blocklist:
        logger.warning("Content safety blocklist is empty; all content passes")
        return True

    text = " ".join(m.get("content", "") for m in messages).lower()
    return not any(word.lower() in text for word in blocklist)
