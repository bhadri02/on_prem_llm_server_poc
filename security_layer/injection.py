"""
injection.py — Prompt injection detector for the Security & Governance Layer.

Provides two public functions:

- ``load_injection_patterns``: loads and compiles regex patterns from a YAML
  file at startup; returns ``None`` on any failure so the lifespan handler can
  abort startup gracefully.
- ``scan_for_injection``: per-request scan that returns 1.0 on first match or
  0.0 when no pattern matches (including when the pattern list is empty).
"""

import re
import pathlib
from typing import Optional

import yaml

from security_layer.logging_config import get_logger

logger = get_logger(__name__)


def load_injection_patterns(path: str) -> Optional[list[re.Pattern]]:
    """Load and compile injection patterns from a YAML file.

    Reads the file at *path*, parses it with ``yaml.safe_load``, and compiles
    every entry in ``data["patterns"]`` as a case-insensitive regular
    expression.

    Returns:
        A (possibly empty) list of compiled :class:`re.Pattern` objects on
        success, or ``None`` on any of the following failures:

        - The file does not exist (``FileNotFoundError``).
        - The file content is not valid YAML (``yaml.YAMLError``).
        - One or more entries are not valid regex patterns (``re.error``).

    Args:
        path: Filesystem path to the YAML patterns file.
    """
    try:
        content = pathlib.Path(path).read_text()
        data = yaml.safe_load(content)
        raw_patterns: list[str] = data.get("patterns", [])
        return [re.compile(p, re.IGNORECASE) for p in raw_patterns]
    except FileNotFoundError:
        logger.error(f"Injection patterns file not found: {path}")
    except yaml.YAMLError as e:
        logger.error(f"Malformed injection patterns YAML: {e}")
    except re.error as e:
        logger.error(f"Invalid regex in injection patterns: {e}")
    return None


def scan_for_injection(messages: list[dict], patterns: list[re.Pattern]) -> float:
    """Scan messages for prompt injection patterns.

    Concatenates the ``content`` field of every message with a single space
    separator, then applies each compiled pattern via :func:`re.search`.

    Returns:
        ``1.0`` on the first match found, ``0.0`` if no pattern matches
        (including when *patterns* is an empty list).

    Args:
        messages: List of message dicts; each dict may contain a ``content``
            key whose value is the text to scan.
        patterns: List of compiled :class:`re.Pattern` objects to apply.
    """
    text = " ".join(m.get("content", "") for m in messages)
    for pattern in patterns:
        if pattern.search(text):
            return 1.0
    return 0.0
