"""
tests/unit/test_inference_client.py

Unit tests for intelligent_router.inference_client.

Covers:
  call_inference
    - HTTP 200 with valid IMF (response.content present) → returns parsed dict
    - HTTP 500 → raises InferenceError(reason="non_200") with status_code=500
    - HTTP 200 with empty/non-JSON body → raises InferenceError(reason="parse_error")
    - HTTP 200 with valid JSON but null response.content → raises InferenceError(reason="missing_content")
    - httpx.TimeoutException → raises InferenceError(reason="timeout")
    - httpx.ConnectError → raises InferenceError(reason="connect_error")
    - X-Request-Id header is set on every POST
    - Content-Type: application/json is set on every POST

pytest-httpx 0.30 (httpx_mock fixture) intercepts all outbound httpx calls.
asyncio_mode = auto (pytest.ini) — no @pytest.mark.asyncio decorator needed.
"""

import httpx
import pytest

from intelligent_router.inference_client import InferenceError, call_inference

INFERENCE_URL = "http://inference-adapter:8087"
INFER_URL = f"{INFERENCE_URL}/infer"
REQUEST_ID = "00000000-0000-4000-8000-000000000001"
TIMEOUT = 30.0

# Minimal valid IMF dict sent as the POST body
SAMPLE_IMF = {
    "request_id": REQUEST_ID,
    "request": {"messages": [{"role": "user", "content": "Hello"}]},
    "governance": {"content_safety_passed": True},
    "routing": {"selected_model": "llama3.2-3b"},
    "cache": {"lookup_hit": False},
    "response": {"content": None},
}

# A valid inference response — response.content is non-null
VALID_RESPONSE_IMF = {
    **SAMPLE_IMF,
    "response": {
        "content": "The answer is 42.",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    },
}


# ---------------------------------------------------------------------------
# HTTP 200 with valid IMF — returns parsed dict
# ---------------------------------------------------------------------------


class TestCallInferenceSuccess:
    async def test_returns_parsed_dict_on_200(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=VALID_RESPONSE_IMF)

        async with httpx.AsyncClient() as client:
            result = await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert result == VALID_RESPONSE_IMF

    async def test_response_content_is_accessible(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=VALID_RESPONSE_IMF)

        async with httpx.AsyncClient() as client:
            result = await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert result["response"]["content"] == "The answer is 42."


# ---------------------------------------------------------------------------
# HTTP 500 → raises InferenceError(reason="non_200")
# ---------------------------------------------------------------------------


class TestCallInferenceNon200:
    async def test_raises_inference_error_on_500(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=500)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "non_200"

    async def test_status_code_is_preserved_on_non_200(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=500)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.status_code == 500

    async def test_raises_on_404(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=404)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "non_200"
        assert exc_info.value.status_code == 404

    async def test_raises_on_503(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=503)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "non_200"


# ---------------------------------------------------------------------------
# Empty / non-JSON body → raises InferenceError(reason="parse_error")
# ---------------------------------------------------------------------------


class TestCallInferenceParseError:
    async def test_raises_parse_error_on_empty_body(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=200, content=b"")

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "parse_error"

    async def test_raises_parse_error_on_invalid_json(self, httpx_mock):
        httpx_mock.add_response(
            url=INFER_URL, status_code=200, content=b"this is not json"
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "parse_error"

    async def test_raises_parse_error_on_partial_json(self, httpx_mock):
        httpx_mock.add_response(
            url=INFER_URL, status_code=200, content=b'{"truncated": '
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "parse_error"


# ---------------------------------------------------------------------------
# Valid JSON with null response.content → raises InferenceError(reason="missing_content")
# ---------------------------------------------------------------------------


class TestCallInferenceMissingContent:
    async def test_raises_missing_content_when_response_content_is_null(self, httpx_mock):
        body = {**VALID_RESPONSE_IMF, "response": {"content": None, "finish_reason": "stop"}}
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=body)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "missing_content"

    async def test_raises_missing_content_when_response_block_absent(self, httpx_mock):
        """No 'response' key at all in the returned JSON."""
        body = {k: v for k, v in VALID_RESPONSE_IMF.items() if k != "response"}
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=body)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "missing_content"

    async def test_raises_missing_content_when_content_key_absent(self, httpx_mock):
        """response block exists but 'content' key is missing."""
        body = {**VALID_RESPONSE_IMF, "response": {"finish_reason": "stop"}}
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=body)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "missing_content"

    async def test_raises_missing_content_when_content_is_empty_string(self, httpx_mock):
        """Empty string is falsy — treated the same as null."""
        body = {**VALID_RESPONSE_IMF, "response": {"content": "", "finish_reason": "stop"}}
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=body)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "missing_content"


# ---------------------------------------------------------------------------
# httpx.TimeoutException → raises InferenceError(reason="timeout")
# ---------------------------------------------------------------------------


class TestCallInferenceTimeout:
    async def test_raises_timeout_on_connect_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out", request=None),
            url=INFER_URL,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "timeout"

    async def test_raises_timeout_on_read_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ReadTimeout("read timed out", request=None),
            url=INFER_URL,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "timeout"

    async def test_raises_timeout_on_pool_timeout(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.PoolTimeout("pool timed out", request=None),
            url=INFER_URL,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "timeout"


# ---------------------------------------------------------------------------
# httpx.ConnectError → raises InferenceError(reason="connect_error")
# ---------------------------------------------------------------------------


class TestCallInferenceConnectError:
    async def test_raises_connect_error(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url=INFER_URL,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.reason == "connect_error"

    async def test_connect_error_has_no_status_code(self, httpx_mock):
        httpx_mock.add_exception(
            httpx.ConnectError("unreachable"),
            url=INFER_URL,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError) as exc_info:
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        assert exc_info.value.status_code is None


# ---------------------------------------------------------------------------
# Header verification — X-Request-Id and Content-Type on every POST
# ---------------------------------------------------------------------------


class TestCallInferenceHeaders:
    async def test_x_request_id_header_is_set(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=VALID_RESPONSE_IMF)

        async with httpx.AsyncClient() as client:
            await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert requests[0].headers.get("x-request-id") == REQUEST_ID

    async def test_content_type_is_application_json(self, httpx_mock):
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=VALID_RESPONSE_IMF)

        async with httpx.AsyncClient() as client:
            await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        content_type = requests[0].headers.get("content-type", "")
        assert "application/json" in content_type

    async def test_headers_present_even_when_error_raised(self, httpx_mock):
        """Headers are sent even when the server returns an error status."""
        httpx_mock.add_response(url=INFER_URL, status_code=500)

        async with httpx.AsyncClient() as client:
            with pytest.raises(InferenceError):
                await call_inference(SAMPLE_IMF, INFERENCE_URL, REQUEST_ID, TIMEOUT, client)

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert requests[0].headers.get("x-request-id") == REQUEST_ID
        assert "application/json" in requests[0].headers.get("content-type", "")

    async def test_different_request_id_is_forwarded(self, httpx_mock):
        """Ensure the actual request_id argument appears in the header."""
        custom_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        httpx_mock.add_response(url=INFER_URL, status_code=200, json=VALID_RESPONSE_IMF)

        async with httpx.AsyncClient() as client:
            await call_inference(SAMPLE_IMF, INFERENCE_URL, custom_id, TIMEOUT, client)

        requests = httpx_mock.get_requests()
        assert requests[0].headers.get("x-request-id") == custom_id


# ---------------------------------------------------------------------------
# InferenceError — class contract
# ---------------------------------------------------------------------------


class TestInferenceErrorClass:
    def test_reason_attribute(self):
        err = InferenceError("msg", reason="non_200", status_code=503)
        assert err.reason == "non_200"

    def test_status_code_attribute(self):
        err = InferenceError("msg", reason="non_200", status_code=503)
        assert err.status_code == 503

    def test_status_code_defaults_to_none(self):
        err = InferenceError("msg", reason="timeout")
        assert err.status_code is None

    def test_is_exception_subclass(self):
        assert issubclass(InferenceError, Exception)

    def test_message_is_accessible(self):
        err = InferenceError("something went wrong", reason="parse_error")
        assert str(err) == "something went wrong"
