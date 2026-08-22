"""
intelligent_router/inference_client.py

HTTP client for the Inference Adapter (/infer endpoint).

Raises InferenceError (with a machine-readable `reason` attribute) on every
failure so the pipeline can trigger the Fallback Manager without catching
generic exceptions.

Reasons:
  "non_200"        — Inference Adapter returned a non-200 HTTP status
  "parse_error"    — Response body is empty or not valid JSON
  "missing_content"— Parsed JSON is missing response.content (null or absent)
  "timeout"        — httpx.TimeoutException raised
  "connect_error"  — httpx.ConnectError raised (adapter unreachable)
"""

import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class InferenceError(Exception):
    """Raised by call_inference on any non-successful inference call.

    Attributes:
        reason      -- machine-readable failure category (see module docstring)
        status_code -- HTTP status code when reason is "non_200", else None
    """

    def __init__(self, message: str, reason: str, status_code: int = None):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


async def call_inference(
    imf: dict,
    inference_url: str,
    request_id: str,
    timeout_seconds: float,
    http_client: httpx.AsyncClient,
) -> dict:
    """POST the full IMF to ``{inference_url}/infer`` and return the response IMF.

    Args:
        imf             -- full IMF dict to forward as the JSON body
        inference_url   -- base URL of the Inference Adapter (e.g. "http://inference-adapter:8087")
        request_id      -- request UUID for the X-Request-Id header and log correlation
        timeout_seconds -- per-request HTTP timeout
        http_client     -- shared httpx.AsyncClient instance

    Returns:
        Parsed response dict (the completed IMF with response block populated).

    Raises:
        InferenceError(reason="non_200")        -- non-200 HTTP status
        InferenceError(reason="parse_error")    -- invalid / empty JSON body
        InferenceError(reason="missing_content")-- response.content null or absent
        InferenceError(reason="timeout")        -- httpx.TimeoutException
        InferenceError(reason="connect_error")  -- httpx.ConnectError
    """
    try:
        resp = await http_client.post(
            f"{inference_url}/infer",
            json=imf,
            headers={
                "Content-Type": "application/json",
                "X-Request-Id": request_id,
            },
            timeout=timeout_seconds,
        )

        if resp.status_code != 200:
            raise InferenceError(
                f"Inference returned HTTP {resp.status_code}",
                reason="non_200",
                status_code=resp.status_code,
            )

        # Parse JSON body
        try:
            body = resp.json()
        except Exception:
            raise InferenceError(
                "Inference response is not valid JSON",
                reason="parse_error",
            )

        # Validate response.content is present and non-null
        response_block = body.get("response") or {}
        if not response_block.get("content"):
            raise InferenceError(
                "Inference response missing response.content",
                reason="missing_content",
            )

        return body

    except httpx.TimeoutException:
        raise InferenceError(
            f"Inference timeout after {timeout_seconds}s",
            reason="timeout",
        )
    except httpx.ConnectError:
        raise InferenceError(
            "Inference adapter unreachable",
            reason="connect_error",
        )


async def call_inference_stream(
    imf: dict,
    inference_url: str,
    request_id: str,
    timeout_seconds: float,
    http_client: httpx.AsyncClient,
) -> AsyncIterator[dict]:
    """POST the full IMF to ``{inference_url}/infer/stream`` and yield each
    parsed newline-delimited-JSON line (see inference_adapter's streaming
    wire protocol — inference_adapter/routers/infer.py's module docstring).

    Each yielded dict is one of:
        {"type": "delta", "content": "<text>"}
        {"type": "done", "imf": {...}}
        {"type": "error", "event": "<code>", "status_code": <int>, ...}

    The caller (pipeline.py's streaming pipeline) decides what to do with
    an in-band "error" line — typically raising InferenceError itself to
    trigger the same fallback path call_inference's non-streaming callers
    use, if no content has been sent to the end client yet.

    Raises:
        InferenceError(reason="non_200")     -- /infer/stream itself
                                                 returned non-200 (should not
                                                 happen by design, but
                                                 handled defensively)
        InferenceError(reason="parse_error") -- a line isn't valid JSON
        InferenceError(reason="timeout")     -- httpx.TimeoutException
        InferenceError(reason="connect_error") -- httpx.ConnectError
    """
    try:
        async with http_client.stream(
            "POST",
            f"{inference_url}/infer/stream",
            json=imf,
            headers={
                "Content-Type": "application/json",
                "X-Request-Id": request_id,
            },
            timeout=timeout_seconds,
        ) as resp:
            if resp.status_code != 200:
                raise InferenceError(
                    f"Inference stream returned HTTP {resp.status_code}",
                    reason="non_200",
                    status_code=resp.status_code,
                )
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except InferenceError:
                    raise
                except Exception:
                    raise InferenceError(
                        "Inference stream line is not valid JSON",
                        reason="parse_error",
                    )
    except httpx.TimeoutException:
        raise InferenceError(
            f"Inference stream timeout after {timeout_seconds}s",
            reason="timeout",
        )
    except httpx.ConnectError:
        raise InferenceError(
            "Inference adapter unreachable",
            reason="connect_error",
        )
