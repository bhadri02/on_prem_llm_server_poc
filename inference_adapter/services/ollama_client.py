"""
OllamaClient — async HTTP client wrapping ``httpx.AsyncClient`` for the Ollama
inference engine.

Provides a typed error hierarchy so callers can react to distinct failure modes
(timeout, connection failure, bad HTTP status codes, unparseable response) without
inspecting raw httpx exceptions or HTTP status codes directly.

Validates: Requirements 13.5, 13.6, 9.1, 9.2, 9.3, 9.4
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx
import json


# ---------------------------------------------------------------------------
# Typed exception hierarchy
# ---------------------------------------------------------------------------


class OllamaError(Exception):
    """Base class for all Ollama client errors."""


class OllamaTimeoutError(OllamaError):
    """Raised when the Ollama request exceeds the configured timeout.

    Maps from ``httpx.TimeoutException``.
    """


class OllamaConnectionError(OllamaError):
    """Raised when a network-level connection to Ollama cannot be established.

    Maps from ``httpx.ConnectError`` and other transport-level errors
    (``httpx.TransportError`` subclasses excluding ``httpx.TimeoutException``).
    """


class OllamaBackendError(OllamaError):
    """Raised when Ollama returns an HTTP 5xx response.

    Args:
        status_code: The HTTP status code returned by Ollama (500–599).
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Ollama backend error: HTTP {status_code}")
        self.status_code = status_code


class OllamaRequestError(OllamaError):
    """Raised when Ollama returns an HTTP 4xx response.

    Args:
        status_code: The HTTP status code returned by Ollama (400–499).
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Ollama request error: HTTP {status_code}")
        self.status_code = status_code


class OllamaInvalidResponseError(OllamaError):
    """Raised when the Ollama response body cannot be parsed as valid JSON."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OllamaClient:
    """Async HTTP client for the Ollama inference engine.

    Uses a single shared ``httpx.AsyncClient`` session for all requests.
    The same ``timeout`` value is applied to both the connect and read phases.

    Args:
        base_url: Base URL of the Ollama server, e.g.
                  ``"http://inference-ollama:11434"``.
        timeout:  Request timeout in seconds applied to both the TCP connect
                  phase and the response read phase.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
        )

    # ------------------------------------------------------------------
    # Chat completion
    # ------------------------------------------------------------------

    async def chat(self, payload: dict) -> dict:
        """Send a chat completion request to the Ollama ``/api/chat`` endpoint.

        ``stream`` is always forced to ``False`` before the request is sent,
        regardless of the value present in *payload*.

        Args:
            payload: Ollama ``/api/chat`` request body as a dict. The ``stream``
                     field will be overwritten with ``False``.

        Returns:
            Parsed JSON response body as a ``dict``.

        Raises:
            OllamaTimeoutError: If the request times out.
            OllamaConnectionError: If a transport-level connection error occurs.
            OllamaBackendError: If Ollama returns HTTP 5xx.
            OllamaRequestError: If Ollama returns HTTP 4xx.
            OllamaInvalidResponseError: If the response body is not valid JSON.
        """
        # Enforce non-streaming for all POC requests (Requirement 14.1)
        payload = {**payload, "stream": False}

        url = f"{self._base_url}/api/chat"
        try:
            response = await self._client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"Request to Ollama timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Could not connect to Ollama at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            # Covers remaining transport-level errors (e.g. RemoteProtocolError)
            raise OllamaConnectionError(
                f"Transport error communicating with Ollama: {exc}"
            ) from exc

        status = response.status_code

        if 500 <= status <= 599:
            raise OllamaBackendError(status)

        if 400 <= status <= 499:
            raise OllamaRequestError(status)

        try:
            return response.json()
        except Exception as exc:
            raise OllamaInvalidResponseError(
                "Failed to parse Ollama response as JSON"
            ) from exc

    # ------------------------------------------------------------------
    # Streaming chat completion
    # ------------------------------------------------------------------

    async def chat_stream(self, payload: dict) -> AsyncIterator[dict]:
        """Stream a chat completion from Ollama's ``/api/chat`` endpoint.

        ``stream`` is always forced to ``True`` before the request is sent.
        Ollama's streaming response is newline-delimited JSON: each line is
        a partial ``{"message": {"content": "<delta>"}, "done": false}``
        object, with the final line carrying ``"done": true`` plus the same
        stats fields (``prompt_eval_count``, ``eval_count``,
        ``total_duration``, ``done_reason``) that ``chat()``'s single
        response carries.

        Yields:
            Each parsed JSON line as a dict, in arrival order.

        Raises:
            OllamaTimeoutError, OllamaConnectionError, OllamaBackendError,
            OllamaRequestError, OllamaInvalidResponseError — same meaning
            as chat(); raised mid-generator, so callers consuming this via
            ``async for`` see the exception at whichever iteration it
            occurs on (immediately, if the failure is in the initial
            connect/status-check before any line is read).
        """
        payload = {**payload, "stream": True}
        url = f"{self._base_url}/api/chat"
        try:
            async with self._client.stream("POST", url, json=payload) as response:
                status = response.status_code
                if 500 <= status <= 599:
                    raise OllamaBackendError(status)
                if 400 <= status <= 499:
                    raise OllamaRequestError(status)

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except Exception as exc:
                        raise OllamaInvalidResponseError(
                            "Failed to parse Ollama stream line"
                        ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"Request to Ollama timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Could not connect to Ollama at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise OllamaConnectionError(
                f"Transport error communicating with Ollama: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Model listing
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """Retrieve the list of model names available in Ollama.

        Issues a ``GET {base_url}/api/tags`` request and extracts the ``"name"``
        field from each entry in the ``"models"`` array.

        Returns:
            A list of model name strings (e.g. ``["llama3.2:3b"]``).

        Raises:
            OllamaTimeoutError: If the request times out.
            OllamaConnectionError: If a transport-level connection error occurs.
            OllamaInvalidResponseError: If the response body cannot be parsed or
                does not contain a ``"models"`` array.
        """
        url = f"{self._base_url}/api/tags"
        try:
            response = await self._client.get(url)
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"list_models request to Ollama timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Could not connect to Ollama at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise OllamaConnectionError(
                f"Transport error communicating with Ollama: {exc}"
            ) from exc

        try:
            data = response.json()
            return [entry["name"] for entry in data.get("models", [])]
        except Exception as exc:
            raise OllamaInvalidResponseError(
                "Failed to parse Ollama /api/tags response"
            ) from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` session."""
        await self._client.aclose()
