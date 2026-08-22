"""
admin_portal/tests/test_chat_streaming.py

Tests for POST /portal/chat/completions' streaming path (stream=true) —
see admin_portal/routers/chat.py's _stream_chat_completions. The
non-streaming path is unaffected (existing behavior, not modified beyond
adding the `stream` field default False).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def set_required_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "test-key")


@pytest.fixture
def app():
    from admin_portal.main import app as _app
    from admin_portal.services.session_auth import AuthContext, get_current_session

    fake_ctx = AuthContext(user=MagicMock(), roles=["developer"], api_key_raw="session-key-raw")

    async def _override_session():
        return fake_ctx

    _app.dependency_overrides[get_current_session] = _override_session
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


_REQUEST_BODY = {
    "model": "llama3.2:3b",
    "messages": [{"role": "user", "content": "hi"}],
    "temperature": 0.7,
    "stream": True,
}


class TestStreamingRelay:
    async def test_relays_upstream_sse_bytes_unchanged(self, client):
        upstream_body = (
            b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        with respx.mock(assert_all_called=True) as mock:
            mock.post("http://api-gateway:8080/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, content=upstream_body, headers={"content-type": "text/event-stream"}
                )
            )
            response = await client.post("/portal/chat/completions", json=_REQUEST_BODY)

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert response.content == upstream_body

    async def test_upstream_unreachable_yields_inband_error_and_done(self, client):
        with respx.mock(assert_all_called=True) as mock:
            mock.post("http://api-gateway:8080/v1/chat/completions").mock(
                side_effect=httpx.ConnectError("refused")
            )
            response = await client.post("/portal/chat/completions", json=_REQUEST_BODY)

        assert response.status_code == 200  # streaming already committed to 200
        body_text = response.text
        assert body_text.endswith("data: [DONE]\n\n")
        assert '"code": "502"' in body_text or '"code":"502"' in body_text

    async def test_non_streaming_request_unaffected(self, client):
        """stream=false (or omitted) still uses the buffered, non-streaming path."""
        with respx.mock(assert_all_called=True) as mock:
            mock.post("http://api-gateway:8080/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
            )
            response = await client.post(
                "/portal/chat/completions",
                json={**_REQUEST_BODY, "stream": False},
            )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "hi"
