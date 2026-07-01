"""
pii.py — Presidio wrapper for PII detection and masking.

Provides:

- ``POC_ENTITIES``: the three entity types detected in the POC.
- ``MIN_CONFIDENCE``: the minimum Presidio confidence score required to mask
  an entity (0.7).
- ``mask_text``: detects and masks PII in a single text string.
- ``mask_messages``: applies ``mask_text`` to every message in a list and
  aggregates detected entity types across all messages.
"""

from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Entity types scanned for during the POC phase.
POC_ENTITIES: list[str] = ["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON"]

#: Minimum Presidio confidence score required for an entity to be masked.
MIN_CONFIDENCE: float = 0.7


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def mask_text(
    text: str,
    analyzer: AnalyzerEngine,
    anonymizer: AnonymizerEngine,
    pii_enabled: bool,
) -> tuple[str, list[str]]:
    """Detect and mask PII in a single text string.

    When *pii_enabled* is ``False`` **or** *text* is empty the function
    returns immediately without calling Presidio, so callers require no
    conditional logic around the flag.

    When entities are detected, each is replaced with the token
    ``[REDACTED_<ENTITY_TYPE>]`` (e.g. ``[REDACTED_EMAIL_ADDRESS]``).

    Returns:
        A 2-tuple ``(masked_text, entity_types)`` where:

        - *masked_text* is the anonymised version of *text* (or the original
          text when no masking was applied).
        - *entity_types* is a deduplicated list of the entity type strings
          that were found (e.g. ``["EMAIL_ADDRESS"]``), or ``[]`` when no PII
          was detected or masking was skipped.

    Args:
        text: The input string to scan and mask.
        analyzer: A Presidio :class:`~presidio_analyzer.AnalyzerEngine`
            instance.
        anonymizer: A Presidio :class:`~presidio_anonymizer.AnonymizerEngine`
            instance.
        pii_enabled: When ``False``, skip detection entirely and return the
            original text with an empty entity list.
    """
    if not pii_enabled or not text:
        return text, []

    results = analyzer.analyze(
        text=text,
        entities=POC_ENTITIES,
        language="en",
        score_threshold=MIN_CONFIDENCE,
    )
    if not results:
        return text, []

    # Build one OperatorConfig per entity type so each entity is replaced
    # with its own labelled token.
    operators = {
        entity: OperatorConfig("replace", {"new_value": f"[REDACTED_{entity}]"})
        for entity in POC_ENTITIES
    }
    anonymized = anonymizer.anonymize(
        text=text,
        analyzer_results=results,
        operators=operators,
    )

    # Deduplicate entity types while preserving order (set → sorted list keeps
    # output deterministic; using dict.fromkeys preserves insertion order).
    entity_types: list[str] = list(dict.fromkeys(r.entity_type for r in results))
    return anonymized.text, entity_types


def mask_messages(
    messages: list[dict],
    analyzer: AnalyzerEngine,
    anonymizer: AnonymizerEngine,
    pii_enabled: bool,
) -> tuple[list[dict], list[str]]:
    """Apply PII masking to the ``content`` field of every message in a list.

    Calls :func:`mask_text` for each message and accumulates the detected
    entity types across all messages, deduplicating the final union.

    Returns:
        A 2-tuple ``(updated_messages, all_entity_types)`` where:

        - *updated_messages* is a new list of message dicts with ``content``
          fields replaced by their masked equivalents (or unchanged when no
          PII was detected in that message).
        - *all_entity_types* is the deduplicated union of every entity type
          found across all messages (e.g. ``["EMAIL_ADDRESS", "PERSON"]``).

    Args:
        messages: List of message dicts; each dict may contain a ``content``
            key whose value is the text to scan.
        analyzer: A Presidio :class:`~presidio_analyzer.AnalyzerEngine`
            instance.
        anonymizer: A Presidio :class:`~presidio_anonymizer.AnonymizerEngine`
            instance.
        pii_enabled: Forwarded to :func:`mask_text`; when ``False`` all
            messages are returned unchanged with an empty entity list.
    """
    updated_messages: list[dict] = []
    seen_entity_types: dict[str, None] = {}  # ordered set via dict keys

    for message in messages:
        content = message.get("content", "")
        masked_content, entity_types = mask_text(
            text=content,
            analyzer=analyzer,
            anonymizer=anonymizer,
            pii_enabled=pii_enabled,
        )
        # Build updated message dict, replacing only the content field.
        updated_message = {**message, "content": masked_content}
        updated_messages.append(updated_message)

        # Accumulate entity types (deduplication via dict key insertion order).
        for entity_type in entity_types:
            seen_entity_types[entity_type] = None

    return updated_messages, list(seen_entity_types.keys())
