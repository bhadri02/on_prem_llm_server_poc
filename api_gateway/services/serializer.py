"""
Response serializer for the API Gateway (Layer 1).

Converts a completed IMFDocument back into an OpenAI-compatible
chat completion response dict.

Validates: Requirements 6.1–6.4
"""

from __future__ import annotations

from datetime import datetime, timezone

from api_gateway.schemas.imf import IMFDocument


def serialize_response(imf: IMFDocument) -> dict:
    """Serialize an IMFDocument into an OpenAI-compatible chat completion dict.

    Args:
        imf: An IMFDocument whose `response` block has been populated by the
             downstream security / inference pipeline.

    Returns:
        A dict that can be returned directly as a JSON response body.
    """
    return {
        "id": f"chatcmpl-{imf.request_id}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": imf.request.model or "",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": imf.response.content,
                },
                "finish_reason": imf.response.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": imf.response.usage.prompt_tokens,
            "completion_tokens": imf.response.usage.completion_tokens,
            "total_tokens": imf.response.usage.total_tokens,
        },
    }
