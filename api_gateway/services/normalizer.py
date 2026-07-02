"""
Request normalizer for the API Gateway (Layer 1).

Converts an OpenAIChatRequest into an IMFDocument, populating all
required fields and initializing governance/routing/cache/response
blocks to their schema defaults.

Validates: Requirements 4.1–4.12, 11.1–11.5
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from api_gateway.schemas.imf import (
    IMFCache,
    IMFDocument,
    IMFGovernance,
    IMFMessage,
    IMFRequest,
    IMFResponse,
    IMFRouting,
    IMFUser,
)
from api_gateway.schemas.openai import OpenAIChatRequest


def build_imf(payload: OpenAIChatRequest) -> IMFDocument:
    """Normalize an OpenAI-compatible chat request into an IMFDocument.

    Args:
        payload: Validated OpenAIChatRequest from the incoming HTTP body.

    Returns:
        A fully populated IMFDocument ready for downstream processing.
    """
    request_id = str(uuid.uuid4())

    # Map OpenAI messages → IMF messages, preserving order, role, and content
    imf_messages = [
        IMFMessage(role=msg.role, content=msg.content)
        for msg in payload.messages
    ]

    return IMFDocument(
        request_id=request_id,
        trace_id=request_id,          # POC: trace_id == request_id
        span_id="",
        timestamp_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        user=IMFUser(
            user_id="poc-user",
            department="poc",
            roles=["developer"],
            auth_method="api_key",
        ),
        request=IMFRequest(
            model=payload.model,
            messages=imf_messages,
            stream=payload.stream,
            max_tokens=payload.max_tokens if payload.max_tokens is not None else 2048,
            temperature=payload.temperature if payload.temperature is not None else 0.7,
        ),
        governance=IMFGovernance(),
        routing=IMFRouting(),
        cache=IMFCache(),
        response=IMFResponse(),
        metadata={},
        extensions={},
    )
