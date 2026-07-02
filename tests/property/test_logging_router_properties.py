"""
Property-based tests for router log entry format in the Intelligent Router.

Properties covered:
  - Property 11: Every Log Entry Is a Single-Line JSON Object With Mandatory Fields
    For any router operation, every log line emitted by intelligent_router loggers:
      - Is valid JSON
      - Has a "timestamp" field (ISO-8601 ending in Z)
      - Has a "level" field in {DEBUG, INFO, WARNING, ERROR}
      - Contains no embedded newlines
    For routing_decision log entries, all 8 required fields are present.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import asyncio
import copy
import datetime
import io
import json
import logging
import re
import sys
import types
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Register and load the 'ci' Hypothesis profile (max_examples=100).
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from intelligent_router.task_classifier import ClassifierRules
from intelligent_router.model_selector import ModelMatrix, ModelEntry
from intelligent_router.main import create_app
from intelligent_router.logging_config import JSONFormatter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# ISO-8601 timestamp regex: ends with Z
_ISO8601_Z_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)

# Required fields in a routing_decision log entry
_ROUTING_DECISION_REQUIRED_FIELDS = {
    "request_id",
    "task_type",
    "selected_model",
    "routing_mode",
    "cache_hit",
    "fallback_level",
    "outcome",
    "latency_ms",
}


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

def _make_app_with_state():
    """Create a fresh test app with mock state."""
    rules = ClassifierRules(
        rules={
            "code": ["code", "function"],
            "reasoning": ["reason"],
            "summarization": ["summarize"],
            "translation": ["translate"],
        },
        default="chat",
    )
    model_entry = ModelEntry(
        name="test-model",
        backend="ollama",
        endpoint="http://inference:11434",
        tasks=["chat", "code", "reasoning", "summarization", "translation"],
        health_url="http://inference:11434/api/tags",
        fallback=None,
    )
    matrix = ModelMatrix(
        models={"test-model": model_entry},
        task_defaults={
            "chat": "test-model",
            "code": "test-model",
            "reasoning": "test-model",
            "summarization": "test-model",
            "translation": "test-model",
        },
    )
    mock_settings = MagicMock()
    mock_settings.cache_url = "http://cache:8086"
    mock_settings.inference_adapter_url = "http://inference-adapter:8087"
    mock_settings.audit_store_url = "http://audit-store:9200"
    mock_settings.inference_timeout_seconds = 120
    mock_settings.health_check_timeout_seconds = 5

    app = create_app()
    # Remove the real lifespan so it does not interfere with test requests
    app.router.lifespan_context = None
    app.state = types.SimpleNamespace(
        classifier_rules=rules,
        model_matrix=matrix,
        http_client=MagicMock(),
        settings=mock_settings,
    )
    return app


def _redirect_router_loggers(buf: io.StringIO):
    """Re-point all intelligent_router StreamHandlers with JSONFormatter to buf.

    Returns saved list of (logger, handler, original_stream) for restoration.
    """
    saved = []
    for name, logger_or_ref in logging.Logger.manager.loggerDict.items():
        if not name.startswith("intelligent_router"):
            continue
        if not isinstance(logger_or_ref, logging.Logger):
            continue
        for handler in logger_or_ref.handlers:
            if (
                isinstance(handler, logging.StreamHandler)
                and isinstance(handler.formatter, JSONFormatter)
            ):
                saved.append((logger_or_ref, handler, handler.stream))
                handler.stream = buf
    return saved


def _restore_router_loggers(saved):
    """Restore original streams."""
    for _lgr, handler, original_stream in saved:
        handler.stream = original_stream


def _make_valid_imf() -> dict:
    """Build a minimal valid IMF payload."""
    return {
        "request_id": str(uuid.uuid4()),
        "trace_id": None,
        "span_id": None,
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
        "user": {
            "user_id": "test-user",
            "department": "test",
            "roles": ["developer"],
            "auth_method": "api_key",
        },
        "request": {
            "messages": [{"role": "user", "content": "Hello there!"}],
            "model": None,
            "task_type": None,
            "stream": False,
            "max_tokens": None,
            "temperature": None,
        },
        "governance": {
            "pii_masked": False,
            "pii_fields_detected": [],
            "injection_score": 0.0,
            "jailbreak_score": 0.0,
            "content_safety_passed": True,
            "human_approval_required": False,
            "human_approval_status": "not_required",
            "policy_decisions": [],
        },
        "routing": {"selected_model": None, "routing_mode": "auto", "fallback_level": 0},
        "cache": {"lookup_hit": False, "cache_key": None},
        "response": {
            "content": None,
            "finish_reason": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
        "metadata": {},
        "extensions": {},
    }


def _make_inference_response(imf, *args, **kwargs):
    resp = copy.deepcopy(imf)
    resp["response"] = {
        "content": "Response.",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    return resp


def _assert_log_line_valid(line: str, check_routing_decision: bool = False):
    """Assert that a single log line meets all format requirements."""
    # 1. No embedded newline
    assert "\n" not in line, (
        f"Log line contains an embedded newline: {line!r}"
    )

    # 2. Valid JSON
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Log line is not valid JSON ({exc}): {line!r}"
        ) from exc

    # 3. timestamp present and ISO-8601 ending in Z
    assert "timestamp" in data, (
        f"Log line missing 'timestamp' field: {line!r}"
    )
    ts = data["timestamp"]
    assert isinstance(ts, str), f"timestamp must be a string: {ts!r}"
    assert _ISO8601_Z_RE.match(ts), (
        f"timestamp {ts!r} does not match ISO-8601 format ending in Z: {line!r}"
    )

    # 4. level present and valid
    assert "level" in data, (
        f"Log line missing 'level' field: {line!r}"
    )
    assert data["level"] in _VALID_LEVELS, (
        f"'level' value {data['level']!r} is not one of {_VALID_LEVELS}: {line!r}"
    )

    # 5. For routing_decision entries: all 8 required fields present
    if check_routing_decision and data.get("message") == "routing_decision":
        for field in _ROUTING_DECISION_REQUIRED_FIELDS:
            assert field in data, (
                f"routing_decision log entry missing required field {field!r}: "
                f"{line!r}"
            )


# ---------------------------------------------------------------------------
# Property 11: Every Log Entry Is Single-Line JSON
# ---------------------------------------------------------------------------

@given(
    operation=st.sampled_from([
        "route_success",
        "route_cache_hit",
        "route_error",
        "health",
        "openai_success",
    ])
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_every_log_entry_is_single_line_json(operation):
    """**Validates: Requirements 13.1, 13.2, 13.5**

    Property 11: Every Log Entry Is a Single-Line JSON Object With Mandatory Fields.

    For any router operation, every log line emitted by intelligent_router loggers:
      1. Is valid JSON (json.loads succeeds).
      2. Contains a "timestamp" field that is ISO-8601 UTC ending in "Z".
      3. Contains a "level" field in {DEBUG, INFO, WARNING, ERROR, CRITICAL}.
      4. Contains no embedded newline characters.
    For routing_decision log entries, all 8 required fields are present.
    """

    async def _run():
        app = _make_app_with_state()
        transport = httpx.ASGITransport(app=app)

        buf = io.StringIO()
        saved = _redirect_router_loggers(buf)

        try:
            with (
                patch("intelligent_router.pipeline.post_audit_event", new=AsyncMock()),
                patch("intelligent_router.pipeline.cache_write", new=AsyncMock()),
            ):
                if operation == "route_success":
                    with (
                        patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
                        patch("intelligent_router.pipeline.cache_lookup", new=AsyncMock(return_value={"hit": False})),
                        patch(
                            "intelligent_router.pipeline.call_inference",
                            new=AsyncMock(side_effect=_make_inference_response),
                        ),
                    ):
                        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                            await client.post("/route", json=_make_valid_imf())

                elif operation == "route_cache_hit":
                    cache_hit_resp = {
                        "hit": True,
                        "cache_key": "ck-abc",
                        "response": {
                            "content": "Cached content",
                            "finish_reason": "stop",
                            "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
                        },
                    }
                    with (
                        patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
                        patch("intelligent_router.pipeline.cache_lookup", new=AsyncMock(return_value=cache_hit_resp)),
                    ):
                        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                            await client.post("/route", json=_make_valid_imf())

                elif operation == "route_error":
                    with (
                        patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=False)),
                    ):
                        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                            await client.post("/route", json=_make_valid_imf())

                elif operation == "health":
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                        await client.get("/health")

                elif operation == "openai_success":
                    with (
                        patch("intelligent_router.pipeline.check_model_health", new=AsyncMock(return_value=True)),
                        patch("intelligent_router.pipeline.cache_lookup", new=AsyncMock(return_value={"hit": False})),
                        patch(
                            "intelligent_router.pipeline.call_inference",
                            new=AsyncMock(side_effect=_make_inference_response),
                        ),
                    ):
                        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                            await client.post(
                                "/v1/chat/completions",
                                json={"messages": [{"role": "user", "content": "hi"}]},
                            )

        finally:
            _restore_router_loggers(saved)

        return buf.getvalue()

    captured = asyncio.run(_run())

    lines = [line for line in captured.split("\n") if line.strip()]

    # If no log lines, the property trivially passes for this operation
    for line in lines:
        _assert_log_line_valid(
            line,
            check_routing_decision=(operation in ("route_success", "route_cache_hit", "openai_success")),
        )
