"""
intelligent_router/task_classifier.py

Keyword-based task classifier for the Intelligent Router (Layer 3).

Provides:
  - PRIORITY_ORDER: fixed evaluation order for task types.
  - ClassifierRules: dataclass holding keyword rules and a default task type.
  - load_classifier_rules(path): loads rules from a YAML file, returns None on
    any failure (FileNotFoundError, yaml.YAMLError, or empty/None YAML content).
  - classify_task(messages, rules): concatenates message content fields,
    applies keyword rules in PRIORITY_ORDER, returns the first matching task
    type or rules.default if no match.
"""

import pathlib
from dataclasses import dataclass, field
from typing import Optional

import yaml

from intelligent_router.logging_config import get_logger

logger = get_logger(__name__)

# Fixed evaluation order: higher-priority task types are checked first.
PRIORITY_ORDER = ["code", "reasoning", "summarization", "translation", "chat"]


@dataclass
class ClassifierRules:
    """Holds keyword rules loaded from task_classifier_rules.yaml.

    Attributes:
        rules:   Mapping of task_type -> list of keyword strings.
        default: Task type returned when no keyword rule matches.
    """

    rules: dict[str, list[str]]
    default: str = "chat"

    @property
    def total_keyword_count(self) -> int:
        """Total number of keyword entries across all task types."""
        return sum(len(kws) for kws in self.rules.values())


def load_classifier_rules(path: str) -> Optional[ClassifierRules]:
    """Load classifier rules from the YAML file at *path*.

    Returns a :class:`ClassifierRules` instance on success.
    Returns ``None`` — and logs a specific ERROR — on:
      - ``FileNotFoundError``: the file does not exist at *path*.
      - ``yaml.YAMLError``: the file content is not valid YAML.
      - Any other exception during reading or parsing.
      - The YAML parses to ``None`` (empty file), which is treated as an error
        because an empty YAML document provides no usable content; an empty
        *rules map* inside a valid YAML document is valid and returns a
        ``ClassifierRules`` with ``rules={}``.
    """
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if data is None:
            # Empty YAML file — not the same as an empty rules map.
            logger.error(
                f"Task rules file is empty (parsed to None): {path}"
            )
            return None
        rules = data.get("rules", {})
        default = data.get("default", "chat")
        return ClassifierRules(rules=rules, default=default)
    except FileNotFoundError:
        logger.error(f"Task rules file not found: {path}")
    except yaml.YAMLError as exc:
        logger.error(f"Malformed task rules YAML: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to read task rules file '{path}': {exc}")
    return None


def classify_task(messages: list[dict], rules: ClassifierRules) -> str:
    """Classify a list of IMF messages by scanning for keyword matches.

    Algorithm:
      1. Concatenate the ``content`` field of every message with a single
         space separator; ``None`` content is treated as an empty string.
      2. Convert the concatenated text to lowercase.
      3. Evaluate each task type in ``PRIORITY_ORDER`` in order; for each type,
         check whether any of its configured keywords appears as a
         case-insensitive substring of the concatenated text.
      4. Return the first matching task type, or ``rules.default`` if no
         keyword matches.

    Args:
        messages: List of message dicts, each expected to have a ``content``
                  key (may be ``None`` or absent).
        rules:    :class:`ClassifierRules` loaded from YAML.

    Returns:
        The matched task type string, or ``rules.default`` on no match.
    """
    text = " ".join(m.get("content") or "" for m in messages).lower()
    for task_type in PRIORITY_ORDER:
        keywords = rules.rules.get(task_type, [])
        if any(kw.lower() in text for kw in keywords):
            return task_type
    return rules.default
