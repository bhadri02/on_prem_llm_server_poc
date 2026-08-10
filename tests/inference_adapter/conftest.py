"""
Shared pytest fixtures for the Inference Adapter test suite.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

import inference_adapter.routers.health as health_module
from inference_adapter.main import app

try:
    from hypothesis import HealthCheck, settings as h_settings

    h_settings.register_profile(
        "ci",
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    h_settings.load_profile("ci")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Canned Ollama response returned by mock_ollama_client.chat()
# ---------------------------------------------------------------------------
_CANNED_OLLAMA_RESPONSE = {
    "message": {"role": "assistant", "content": "Hello, world!"},
    "done_reason": "stop",
    "done": True,
    "prompt_eval_count": 10,
    "eval_count": 5,
    "total_duration": 1_500_000_000,
}


@pytest.fixture
def mock_ollama_client():
    """
    OllamaClient-like mock with async chat() and list_models() methods.

    chat()        → returns _CANNED_OLLAMA_RESPONSE
    list_models() → returns ["llama3.2:3b"]
    """
    mock = MagicMock()
    mock.chat = AsyncMock(return_value=_CANNED_OLLAMA_RESPONSE)
    mock.list_models = AsyncMock(return_value=["llama3.2:3b"])
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_anthropic_client():
    """AnthropicClient-like mock with an async messages() method.

    Tests exercising the cloud-backend path override `.messages` directly
    (mirrors how Ollama error-path tests override `mock_ollama_client.chat`).
    """
    mock = MagicMock()
    mock.messages = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "Hello from Claude!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 7},
        }
    )
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_registry_http_client():
    """Plain httpx.AsyncClient-like mock used for Model Registry calls
    (GET /models/{name}/secret) from model_secret_resolver. Tests override
    `.get` directly to simulate specific registry responses/failures."""
    mock = MagicMock()
    mock.get = AsyncMock()
    return mock


@pytest_asyncio.fixture
async def app_client(mock_ollama_client, mock_anthropic_client, mock_registry_http_client):
    """
    Async HTTP client backed by the real FastAPI app with a stub lifespan
    that injects mock_ollama_client into app.state without starting the
    metrics server or connecting to a real Ollama instance.
    """

    @asynccontextmanager
    async def stub_lifespan(application):
        health_module._startup_complete = False

        application.state.ollama_client = mock_ollama_client
        application.state.ollama_models = ["llama3.2:3b"]
        application.state.ollama_reachable = True
        application.state.anthropic_client = mock_anthropic_client
        application.state.http_client = mock_registry_http_client

        health_module._startup_complete = True
        yield
        health_module._startup_complete = False

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = stub_lifespan

    try:
        async with stub_lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                yield client
    finally:
        app.router.lifespan_context = original_lifespan
        health_module._startup_complete = False


@pytest.fixture(autouse=True)
def clear_model_secret_cache():
    """Clear model_secret_resolver's in-process cache between tests so a
    cached result from one test can't leak into the next (same pattern as
    api_gateway.services.key_resolver's cache-clearing fixture)."""
    from inference_adapter.services.model_secret_resolver import _cache

    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def valid_imf_doc():
    """
    Minimal valid IMFDocument dict with routing.selected_model = "llama3.2:3b"
    and one non-empty message — accepted by POST /infer without error.
    """
    return {
        "request_id": "test-request-id-001",
        "trace_id": "trace-001",
        "span_id": "span-001",
        "timestamp_utc": "2024-01-01T00:00:00Z",
        "user": {
            "user_id": "user-001",
            "department": "engineering",
            "roles": ["user"],
            "auth_method": "api_key",
        },
        "request": {
            "model": "llama3.2:3b",
            "task_type": "chat",
            "messages": [{"role": "user", "content": "Hello, how are you?"}],
            "stream": False,
            "max_tokens": 512,
            "temperature": 0.7,
        },
        "governance": {
            "pii_fields_detected": [],
            "pii_masked": False,
            "content_safety_passed": True,
        },
        "routing": {
            "selected_model": "llama3.2:3b",
            "routing_mode": "auto",
            "fallback_level": 0,
        },
        "cache": {"lookup_hit": False, "cache_key": None},
        "response": None,
        "metadata": {},
        "extensions": {},
    }
