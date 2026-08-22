"""
Unit tests for security_layer.pii.StreamingPiiMasker.

Uses a fake analyzer/anonymizer pair that does real (simple) substring
detection instead of mocking exact call args — the masker's own buffering
logic (hold-back window, scan-interval throttle, incremental emission) is
what's under test here, not Presidio itself (see test_pii.py for that).
"""

import os

_SL_ENV = {
    "DOWNSTREAM_ROUTER_URL": "http://router:8082",
    "AUDIT_STORE_URL": "http://audit:9200",
    "AUDIT_API_KEY": "test-key",
    "INJECTION_PATTERNS_PATH": "/tmp/patterns.yaml",
}
for _k, _v in _SL_ENV.items():
    os.environ.setdefault(_k, _v)

from security_layer.pii import StreamingPiiMasker  # noqa: E402


class _FakeAnalyzerResult:
    def __init__(self, entity_type: str, start: int, end: int, score: float = 0.9):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


class _FakeAnalyzer:
    """Finds a fixed literal substring ("secret@example.com") anywhere in
    the text and reports it as an EMAIL_ADDRESS entity — real enough to
    exercise masking without depending on the actual spaCy/Presidio model
    (slow to load, unnecessary for testing buffering behavior)."""

    NEEDLE = "secret@example.com"

    def analyze(self, text, entities, language, score_threshold):
        results = []
        start = text.find(self.NEEDLE)
        if start != -1:
            results.append(_FakeAnalyzerResult("EMAIL_ADDRESS", start, start + len(self.NEEDLE)))
        return results


class _FakeAnonymizeResult:
    def __init__(self, text: str):
        self.text = text


class _FakeAnonymizer:
    def anonymize(self, text, analyzer_results, operators):
        out = text
        # Replace from the end so earlier offsets stay valid.
        for r in sorted(analyzer_results, key=lambda r: r.start, reverse=True):
            token = f"[REDACTED_{r.entity_type}]"
            out = out[: r.start] + token + out[r.end :]
        return _FakeAnonymizeResult(out)


def _make_masker(pii_enabled: bool = True) -> StreamingPiiMasker:
    return StreamingPiiMasker(_FakeAnalyzer(), _FakeAnonymizer(), pii_enabled, entities=["EMAIL_ADDRESS"])


# ---------------------------------------------------------------------------
# Basic accumulation / scan throttling
# ---------------------------------------------------------------------------


class TestScanThrottle:
    def test_short_feeds_produce_no_output_until_threshold(self):
        masker = _make_masker()
        # Each feed is well under MIN_SCAN_INTERVAL_CHARS on its own.
        out1 = masker.feed("Hi")
        out2 = masker.feed(" ")
        assert out1 == ""
        assert out2 == ""

    def test_crossing_threshold_triggers_a_scan(self):
        masker = _make_masker()
        long_enough = "x" * (StreamingPiiMasker.MIN_SCAN_INTERVAL_CHARS + 1)
        # Still within HOLD_BACK_CHARS of the (small) buffer, so nothing is
        # emitted yet even though a scan did happen — confirmed by finish().
        out = masker.feed(long_enough)
        assert out == ""
        # finish() flushes everything regardless of hold-back.
        assert masker.finish() == long_enough


# ---------------------------------------------------------------------------
# Hold-back window behavior
# ---------------------------------------------------------------------------


class TestHoldBackWindow:
    def test_nothing_emitted_while_under_hold_back_size(self):
        masker = _make_masker()
        text = "a" * (StreamingPiiMasker.HOLD_BACK_CHARS - 10)
        out = masker.feed(text)
        assert out == ""

    def test_emits_only_the_safe_prefix_once_buffer_exceeds_hold_back(self):
        masker = _make_masker()
        # Enough text to exceed HOLD_BACK_CHARS by 30 chars.
        text = "a" * (StreamingPiiMasker.HOLD_BACK_CHARS + 30)
        out = masker.feed(text)
        assert len(out) == 30
        assert out == "a" * 30

    def test_finish_flushes_the_remaining_held_back_tail(self):
        masker = _make_masker()
        text = "a" * (StreamingPiiMasker.HOLD_BACK_CHARS + 30)
        first = masker.feed(text)
        rest = masker.finish()
        assert first + rest == text


# ---------------------------------------------------------------------------
# PII masking correctness across the buffering logic
# ---------------------------------------------------------------------------


class TestPiiMaskingAcrossBuffer:
    def test_entity_well_before_edge_gets_masked_when_flushed(self):
        masker = _make_masker()
        prefix = "Contact me at secret@example.com please. "
        filler = "z" * (StreamingPiiMasker.HOLD_BACK_CHARS + 50)
        out = masker.feed(prefix + filler)
        assert "secret@example.com" not in out
        assert "[REDACTED_EMAIL_ADDRESS]" in out

    def test_entity_type_recorded(self):
        masker = _make_masker()
        prefix = "Contact me at secret@example.com please. "
        filler = "z" * (StreamingPiiMasker.HOLD_BACK_CHARS + 50)
        masker.feed(prefix + filler)
        assert masker.entity_types() == ["EMAIL_ADDRESS"]

    def test_entity_still_inside_hold_back_window_is_not_prematurely_flushed_unmasked(self):
        """An entity sitting right at the growing edge must not be emitted
        (masked or not) until it's outside the hold-back window — this is
        the core safety property, not just a masking-correctness check."""
        masker = _make_masker()
        filler = "z" * StreamingPiiMasker.HOLD_BACK_CHARS
        out = masker.feed(filler + "secret@example.com")
        assert "secret@example.com" not in out
        assert "[REDACTED_EMAIL_ADDRESS]" not in out  # not emitted at all yet

    def test_final_flush_masks_an_entity_that_was_at_the_edge(self):
        masker = _make_masker()
        filler = "z" * StreamingPiiMasker.HOLD_BACK_CHARS
        masker.feed(filler + "secret@example.com")
        rest = masker.finish()
        assert "secret@example.com" not in rest
        assert "[REDACTED_EMAIL_ADDRESS]" in rest

    def test_no_double_emission_of_already_flushed_text(self):
        """Uses distinguishable (non-repeating) content so a substring check
        actually proves something — repeated characters would make "first
        not in second" trivially true/false regardless of correctness."""
        masker = _make_masker()
        chunk = "".join(f"[{i:04d}]" for i in range(50))  # unique 6-char tokens, well over HOLD_BACK_CHARS
        first = masker.feed(chunk)
        second = masker.feed("[more-unique-tail-content]" * 3)
        assert first not in second
        assert second not in first
        combined = first + second + masker.finish()
        assert combined == chunk + "[more-unique-tail-content]" * 3


# ---------------------------------------------------------------------------
# pii_enabled=False
# ---------------------------------------------------------------------------


class TestPiiDisabled:
    def test_no_masking_when_disabled(self):
        masker = _make_masker(pii_enabled=False)
        text = "Contact secret@example.com now " + "z" * StreamingPiiMasker.HOLD_BACK_CHARS
        out = masker.feed(text)
        rest = masker.finish()
        assert "secret@example.com" in (out + rest)
        assert masker.entity_types() == []
