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

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from api_gateway.services.key_resolver import KeyProfile


def build_imf(payload: OpenAIChatRequest, user_profile: "KeyProfile | None" = None) -> IMFDocument:
    """Normalize an OpenAI-compatible chat request into an IMFDocument.

    Args:
        payload: Validated OpenAIChatRequest from the incoming HTTP body.
        user_profile: The identity resolved by AuthMiddleware
            (``request.state.user_profile``) from the caller's API key.
            ``None`` only in contexts that bypass auth entirely (e.g. direct
            unit tests of the normalizer) — falls back to the previous
            hardcoded POC identity so those callers keep working.

    Returns:
        A fully populated IMFDocument ready for downstream processing.
    """
    request_id = str(uuid.uuid4())

    # Map OpenAI messages → IMF messages, preserving order, role, and content
    imf_messages = [
        IMFMessage(role=msg.role, content=msg.content)
        for msg in payload.messages
    ]

    if user_profile is not None:
        user = IMFUser(
            user_id=user_profile.user_id,
            department=user_profile.department or "poc",
            roles=user_profile.roles,
            auth_method="api_key",
            key_id=user_profile.key_id,
            model_entitlements=user_profile.model_entitlements,
            rate_limit_override=user_profile.rate_limit_override,
        )
    else:
        user = IMFUser(
            user_id="poc-user",
            department="poc",
            roles=["developer"],
            auth_method="api_key",
        )

    return IMFDocument(
        request_id=request_id,
        trace_id=request_id,          # POC: trace_id == request_id
        span_id="",
        timestamp_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        user=user,
        request=IMFRequest(
            model=payload.model,
            messages=imf_messages,
            stream=payload.stream,
            max_tokens=payload.max_tokens if payload.max_tokens is not None else 2048,
            temperature=payload.temperature if payload.temperature is not None else 0.7,
        ),
        governance=IMFGovernance(),
        # A client-specified model means "pinned" routing — without this,
        # intelligent_router's select_model() always takes the auto branch
        # (imf.routing.routing_mode defaults to "auto" and nothing else ever
        # sets it), silently ignoring request.model unless it happens to
        # match the task's auto-default. See intelligent_router/model_selector.py.
        routing=IMFRouting(routing_mode="pinned" if payload.model else "auto"),
        cache=IMFCache(),
        response=IMFResponse(),
        metadata={},
        extensions={},
    )
