"""
pii.py — Presidio wrapper for PII detection and masking.

Provides:

- ``POC_ENTITIES``: the three entity types detected in the POC.
- ``MIN_CONFIDENCE``: the minimum Presidio confidence score required to mask
  an entity (0.7).
- ``mask_text``: detects and masks PII in a single text string.
- ``mask_messages``: applies ``mask_text`` to every message in a list and
  aggregates detected entity types across all messages.
- ``StreamingPiiMasker``: incremental variant of ``mask_text`` for a
  streaming chat response — see its own docstring for the buffering
  strategy and its accepted, deliberate limitation.
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
    entities: list[str] | None = None,
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
        entities: Entity types to scan for. Defaults to ``POC_ENTITIES`` when
            omitted; callers on the real request path should pass
            ``settings.pii_entities_list`` instead so the configured
            PII_ENTITIES env var actually takes effect.
    """
    if not pii_enabled or not text:
        return text, []

    scan_entities = entities if entities is not None else POC_ENTITIES

    results = analyzer.analyze(
        text=text,
        entities=scan_entities,
        language="en",
        score_threshold=MIN_CONFIDENCE,
    )
    if not results:
        return text, []

    # Build one OperatorConfig per entity type so each entity is replaced
    # with its own labelled token.
    operators = {
        entity: OperatorConfig("replace", {"new_value": f"[REDACTED_{entity}]"})
        for entity in scan_entities
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
    entities: list[str] | None = None,
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
            entities=entities,
        )
        # Build updated message dict, replacing only the content field.
        updated_message = {**message, "content": masked_content}
        updated_messages.append(updated_message)

        # Accumulate entity types (deduplication via dict key insertion order).
        for entity_type in entity_types:
            seen_entity_types[entity_type] = None

    return updated_messages, list(seen_entity_types.keys())


# ---------------------------------------------------------------------------
# Streaming PII masking
# ---------------------------------------------------------------------------


class StreamingPiiMasker:
    """Incrementally masks a growing chat-response text stream.

    Real token-by-token streaming means the client could see raw text
    before it's been scanned for PII — mask_text() only ever sees the
    *complete* response. This holds back the last ``HOLD_BACK_CHARS`` of
    raw text before emitting anything, so Presidio sees enough trailing
    context to correctly classify an entity that starts near the current
    edge of the buffer before that text is flushed to the client. On each
    flush it re-scans the *entire* buffer up to the safe cutoff (cheap at
    chat-message scale) and reuses the existing ``mask_text()`` — no new
    PII-detection logic, only new buffering logic — rather than trying to
    diff incremental NER results, which would be fragile to
    context-dependent reclassification as more text arrives.

    Accepted limitation (a deliberate trade-off, not a bug): an entity that
    is not yet fully visible even after ``HOLD_BACK_CHARS`` of trailing
    context can still be flushed unmasked. This is pathologically rare for
    the entity types configured here (PII_ENTITIES) — all well under 100
    characters — but is a real, known gap inherent to masking a stream
    rather than a complete response; see the platform's streaming design
    notes (CLAUDE.md).
    """

    #: Raw characters to hold back from the growing edge of the buffer
    #: before a span is considered safe to flush — generous headroom
    #: relative to the longest PII entity Presidio detects here.
    HOLD_BACK_CHARS = 200

    #: Minimum amount of new raw text since the last flush attempt before
    #: re-running Presidio at all — keeps a fast token-by-token stream from
    #: invoking the NLP pipeline on every single delta.
    MIN_SCAN_INTERVAL_CHARS = 24

    def __init__(
        self,
        analyzer: AnalyzerEngine,
        anonymizer: AnonymizerEngine,
        pii_enabled: bool,
        entities: list[str] | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._anonymizer = anonymizer
        self._pii_enabled = pii_enabled
        self._entities = entities
        self._raw = ""
        self._raw_at_last_attempt = 0
        self._emitted_masked_len = 0
        self._entity_types: dict[str, None] = {}  # ordered set

    def feed(self, delta: str) -> str:
        """Add newly-arrived raw text; return newly-safe-to-emit masked text
        (may be empty — either nothing new is safe yet, or not enough new
        text has arrived since the last scan attempt)."""
        self._raw += delta
        if len(self._raw) - self._raw_at_last_attempt < self.MIN_SCAN_INTERVAL_CHARS:
            return ""
        self._raw_at_last_attempt = len(self._raw)
        return self._flush(final=False)

    def finish(self) -> str:
        """Flush everything remaining. Call exactly once, at stream end."""
        return self._flush(final=True)

    def entity_types(self) -> list[str]:
        """Deduplicated entity types detected so far, insertion order."""
        return list(self._entity_types.keys())

    def _flush(self, final: bool) -> str:
        safe_upto = len(self._raw) if final else max(0, len(self._raw) - self.HOLD_BACK_CHARS)
        candidate_raw = self._raw[:safe_upto]
        if not candidate_raw:
            return ""

        masked_text, entities = mask_text(
            candidate_raw, self._analyzer, self._anonymizer, self._pii_enabled, entities=self._entities
        )
        for entity_type in entities:
            self._entity_types[entity_type] = None

        if len(masked_text) <= self._emitted_masked_len:
            return ""

        new_chunk = masked_text[self._emitted_masked_len:]
        self._emitted_masked_len = len(masked_text)
        return new_chunk
