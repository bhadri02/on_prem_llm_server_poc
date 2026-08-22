"""
AnthropicClient — async HTTP client wrapping ``httpx.AsyncClient`` for the
Anthropic Messages API (``POST {base_url}/v1/messages``).

Mirrors OllamaClient's typed-exception shape so infer.py can handle both
backends with parallel error-mapping logic — same categories (timeout,
connection failure, 5xx, 4xx, unparseable response), distinct exception
classes so error responses stay attributable to the right backend.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx
import json


# ---------------------------------------------------------------------------
# Typed exception hierarchy
# ---------------------------------------------------------------------------


class AnthropicError(Exception):
    """Base class for all Anthropic client errors."""


class AnthropicTimeoutError(AnthropicError):
    """Raised when the Anthropic request exceeds the configured timeout."""


class AnthropicConnectionError(AnthropicError):
    """Raised when a network-level connection to Anthropic cannot be established."""


class AnthropicBackendError(AnthropicError):
    """Raised when Anthropic returns an HTTP 5xx response."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Anthropic backend error: HTTP {status_code}")
        self.status_code = status_code


class AnthropicRequestError(AnthropicError):
    """Raised when Anthropic returns an HTTP 4xx response.

    Includes 401/403 for an invalid or revoked provider API key — infer.py
    maps this the same way as any other rejected request (422), since from
    the caller's perspective the request itself was unprocessable with the
    credentials on file.
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Anthropic request error: HTTP {status_code}")
        self.status_code = status_code


class AnthropicInvalidResponseError(AnthropicError):
    """Raised when the Anthropic response body cannot be parsed as valid
    JSON, or is missing the fields IMFMapper needs."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AnthropicClient:
    """Async HTTP client for the Anthropic Messages API.

    Args:
        base_url:    Base URL, e.g. ``"https://api.anthropic.com"`` (from the
                     Model Registry record's ``endpoint`` field).
        api_version: Value of the required ``anthropic-version`` header.
        timeout:     Request timeout in seconds, applied to connect + read.
    """

    def __init__(self, base_url: str, api_version: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    async def messages(self, payload: dict, api_key: str) -> dict:
        """Send a request to the Anthropic ``/v1/messages`` endpoint.

        Args:
            payload: Anthropic Messages API request body as a dict
                     (``model``, ``messages``, ``max_tokens``, etc.).
            api_key: The provider API key resolved from the Model Registry.

        Returns:
            Parsed JSON response body as a ``dict``.

        Raises:
            AnthropicTimeoutError, AnthropicConnectionError,
            AnthropicBackendError, AnthropicRequestError,
            AnthropicInvalidResponseError
        """
        url = f"{self._base_url}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }

        try:
            response = await self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AnthropicTimeoutError(
                f"Request to Anthropic timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise AnthropicConnectionError(
                f"Could not connect to Anthropic at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise AnthropicConnectionError(
                f"Transport error communicating with Anthropic: {exc}"
            ) from exc

        status = response.status_code

        if 500 <= status <= 599:
            raise AnthropicBackendError(status)

        if 400 <= status <= 499:
            raise AnthropicRequestError(status)

        try:
            return response.json()
        except Exception as exc:
            raise AnthropicInvalidResponseError(
                "Failed to parse Anthropic response as JSON"
            ) from exc

    async def messages_stream(self, payload: dict, api_key: str) -> AsyncIterator[dict]:
        """Stream a response from Anthropic's ``/v1/messages`` endpoint (SSE).

        Anthropic's streaming wire format is server-sent events: alternating
        ``event: <type>`` and ``data: <json>`` lines. This parses that
        manually (httpx has no built-in SSE decoder) and yields a
        normalised, backend-agnostic shape:

            {"type": "content_delta", "text": "<delta>"}   — zero or more
            {"type": "done", "stop_reason": ..., "usage": {...}}  — exactly one, last

        Args:
            payload: Anthropic Messages API request body (``stream`` is
                     forced to ``True`` regardless of the value passed in).
            api_key: The provider API key resolved from the Model Registry.

        Raises:
            AnthropicTimeoutError, AnthropicConnectionError,
            AnthropicBackendError, AnthropicRequestError,
            AnthropicInvalidResponseError — same meaning as messages().
        """
        url = f"{self._base_url}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }
        stream_payload = {**payload, "stream": True}
        usage_acc = {"input_tokens": 0, "output_tokens": 0}
        stop_reason: str | None = None

        try:
            async with self._client.stream(
                "POST", url, json=stream_payload, headers=headers
            ) as response:
                status = response.status_code
                if 500 <= status <= 599:
                    raise AnthropicBackendError(status)
                if 400 <= status <= 499:
                    raise AnthropicRequestError(status)

                event_type: str | None = None
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                        continue
                    if not line.startswith("data:"):
                        continue

                    data_raw = line[len("data:"):].strip()
                    if not data_raw:
                        continue
                    try:
                        data = json.loads(data_raw)
                    except Exception as exc:
                        raise AnthropicInvalidResponseError(
                            "Failed to parse Anthropic SSE data line"
                        ) from exc

                    if event_type == "content_block_delta":
                        text = (data.get("delta") or {}).get("text")
                        if text:
                            yield {"type": "content_delta", "text": text}
                    elif event_type == "message_start":
                        usage = (data.get("message") or {}).get("usage") or {}
                        if "input_tokens" in usage:
                            usage_acc["input_tokens"] = usage["input_tokens"]
                    elif event_type == "message_delta":
                        delta = data.get("delta") or {}
                        if delta.get("stop_reason"):
                            stop_reason = delta["stop_reason"]
                        usage = data.get("usage") or {}
                        if "output_tokens" in usage:
                            usage_acc["output_tokens"] = usage["output_tokens"]
                    elif event_type == "error":
                        raise AnthropicInvalidResponseError(
                            f"Anthropic stream returned an error event: {data}"
                        )

        except httpx.TimeoutException as exc:
            raise AnthropicTimeoutError(
                f"Request to Anthropic timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise AnthropicConnectionError(
                f"Could not connect to Anthropic at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise AnthropicConnectionError(
                f"Transport error communicating with Anthropic: {exc}"
            ) from exc

        yield {"type": "done", "stop_reason": stop_reason, "usage": usage_acc}

    async def close(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` session."""
        await self._client.aclose()
