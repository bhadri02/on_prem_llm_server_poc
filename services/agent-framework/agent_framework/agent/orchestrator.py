"""
agent_framework/agent/orchestrator.py

LangGraph ReAct agent orchestrator for the Agent Framework (Layer 6).

Entry point: run_agent_session(imf, tool_registry, session_store)

The orchestrator:
  1. Generates a UUID v4 session_id and initialises the session store entry.
  2. Emits an agent_session_start audit event.
  3. Builds a ChatOpenAI client pointing to the Router's /v1 endpoint.
  4. Builds a LangGraph create_react_agent graph with the registered tools.
  5. Streams events via graph.astream_events(), tracking step count and tool calls.
  6. On max steps reached: sets finish_reason="length".
  7. On Router errors (httpx): classifies and returns HTTP 502.
  8. On natural completion: sets finish_reason="stop".
  9. Emits agent_session_complete audit event and updates Prometheus metrics.
  10. Returns (output_imf, 200).

Requirements: 2.1–2.4, 3.1–3.7, 4.1–4.6, 9.2–9.3, 10.2–10.5, 11.1–11.3, 12.1
"""

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from agent_framework import metrics
from agent_framework.audit import emit_audit_event
from agent_framework.config import settings
from agent_framework.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_agent_session(
    imf: dict,
    tool_registry: dict,
    session_store: Any,
) -> tuple[dict, int]:
    """Run the ReAct agent loop for the given IMF.

    Returns (output_imf, http_status_code).
    """
    t0 = time.monotonic()
    request_id = imf.get("request_id", "unknown")
    user_id = imf.get("user", {}).get("user_id", "poc-user")
    session_id = str(uuid.uuid4())

    # 1. Initialise session store entry
    await session_store.init_session(session_id)

    # 2. Emit session start audit event
    emit_audit_event(
        {
            "audit_id": str(uuid.uuid4()),
            "request_id": request_id,
            "session_id": session_id,
            "timestamp_utc": _utcnow(),
            "user_id": user_id,
            "layer": "agent",
            "event_type": "agent_session_start",
            "outcome": "pass",
        }
    )

    # 3. Build LLM client pointing to Router /v1
    llm = ChatOpenAI(
        base_url=f"{settings.router_url}/v1",
        api_key=settings.gateway_api_key,
        model="llama3.2:3b",
        timeout=settings.agent_sub_call_timeout_seconds,
    )

    # 4. Build LangGraph ReAct agent graph
    tools = list(tool_registry.values())
    graph = create_react_agent(llm, tools=tools)

    # 5. Convert IMF messages to LangChain messages
    initial_messages = [
        _to_lc_message(m) for m in imf.get("request", {}).get("messages", [])
    ]

    step_count = 0
    tools_called: list[str] = []
    final_content: str | None = None
    finish_reason = "stop"

    try:
        # 6. Stream events from the graph
        async for event in graph.astream_events(
            {"messages": initial_messages},
            config={"recursion_limit": settings.max_agent_steps * 2 + 1},
            version="v2",
        ):
            kind = event.get("event")

            if kind == "on_chat_model_end":
                step_count += 1
                output = event.get("data", {}).get("output")
                if output:
                    content = _extract_content(output)
                    if content:
                        final_content = content

                if step_count >= settings.max_agent_steps:
                    finish_reason = "length"
                    break

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = event.get("data", {}).get("input", {})
                tools_called.append(tool_name)
                metrics.tool_calls_total.labels(tool_name=tool_name).inc()

                emit_audit_event(
                    {
                        "audit_id": str(uuid.uuid4()),
                        "request_id": request_id,
                        "session_id": session_id,
                        "timestamp_utc": _utcnow(),
                        "layer": "agent",
                        "event_type": "agent_tool_call",
                        "tool_name": tool_name,
                        "tool_input": _serialize_tool_input(tool_input),
                        "outcome": "pass",
                    }
                )

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                result_str = str(event.get("data", {}).get("output", ""))
                await session_store.append_step(
                    session_id=session_id,
                    step=step_count,
                    tool_called=tool_name,
                    result_summary=result_str[:200],
                )

    except Exception as exc:
        error_detail = _classify_router_error(exc)
        if error_detail is not None:
            # Router sub-call failure → HTTP 502
            output_imf = _build_output_imf(
                imf,
                session_id,
                step_count,
                tools_called,
                content=f"Router sub-call failed: {error_detail}",
                finish_reason=None,
            )
            _emit_session_complete(
                request_id,
                session_id,
                step_count,
                tools_called,
                int((time.monotonic() - t0) * 1000),
                "error",
            )
            metrics.sessions_total.labels(outcome="error").inc()
            metrics.errors_total.labels(error_code="502").inc()
            return output_imf, 502
        # Unhandled internal error → let it propagate to the 500 handler
        raise

    # 7. Handle exhausted step budget with no content
    if finish_reason == "length" and not final_content:
        final_content = (
            "Agent reached maximum step limit without producing a final answer."
        )

    if not final_content:
        final_content = "Agent completed without producing a text response."

    # 8. Build output IMF
    output_imf = _build_output_imf(
        imf, session_id, step_count, tools_called, final_content, finish_reason
    )
    if finish_reason == "length":
        output_imf["metadata"]["max_steps_reached"] = True

    # 9. Emit completion audit event and update metrics
    latency_ms = int((time.monotonic() - t0) * 1000)
    outcome = "max_steps_reached" if finish_reason == "length" else "pass"

    _emit_session_complete(
        request_id, session_id, step_count, tools_called, latency_ms, outcome
    )

    metrics.sessions_total.labels(outcome=outcome).inc()
    metrics.session_latency.observe(time.monotonic() - t0)

    # 10. Emit structured session completion log
    logger.info(
        "agent_session_complete",
        extra={
            "extra_fields": {
                "request_id": request_id,
                "session_id": session_id,
                "steps_taken": step_count,
                "tools_called": tools_called,
                "latency_ms": latency_ms,
                "outcome": outcome,
            }
        },
    )

    return output_imf, 200


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Returns the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _to_lc_message(m: dict):
    """Convert an IMF message dict to a LangChain message object."""
    role = m.get("role", "user")
    content = m.get("content", "")
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    # Default: treat unknown roles as human messages
    return HumanMessage(content=content)


def _extract_content(output) -> str | None:
    """Extract text content from a LangChain AIMessage or similar output."""
    if isinstance(output, AIMessage):
        content = output.content
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            # Content blocks: extract text parts
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "".join(parts).strip()
            return text if text else None
        return None
    if hasattr(output, "content"):
        content = output.content
        if isinstance(content, str) and content.strip():
            return content
    return None


def _serialize_tool_input(tool_input: dict) -> str:
    """JSON-serialize tool input, truncated to 4096 characters."""
    try:
        serialized = json.dumps(tool_input, default=str, ensure_ascii=False)
    except Exception:
        serialized = str(tool_input)
    return serialized[:4096]


def _classify_router_error(exc: Exception) -> str | None:
    """
    Returns a human-readable error detail string for known Router failure modes,
    or None for unexpected exceptions that should propagate to the 500 handler.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "Router sub-call timed out after 30s"
    if isinstance(exc, httpx.ConnectError):
        return "Router is unreachable"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Router returned HTTP {exc.response.status_code}"
    return None


def _build_output_imf(
    imf: dict,
    session_id: str,
    step_count: int,
    tools_called: list[str],
    content: str | None,
    finish_reason: str | None,
) -> dict:
    """
    Build the output IMF by copying all fields from the input IMF,
    then populating metadata and response with agent-specific values.
    """
    output = {**imf}
    output["metadata"] = {
        "agent_session_id": session_id,
        "agent_steps_taken": step_count,
        "tools_called": tools_called,
    }
    output["response"] = {
        "content": content,
        "finish_reason": finish_reason,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    return output


def _emit_session_complete(
    request_id: str,
    session_id: str,
    step_count: int,
    tools_called: list[str],
    latency_ms: int,
    outcome: str,
) -> None:
    """Emit the agent_session_complete audit event."""
    emit_audit_event(
        {
            "audit_id": str(uuid.uuid4()),
            "request_id": request_id,
            "session_id": session_id,
            "timestamp_utc": _utcnow(),
            "layer": "agent",
            "event_type": "agent_session_complete",
            "steps_taken": step_count,
            "tools_called": tools_called,
            "total_latency_ms": latency_ms,
            "outcome": outcome,
        }
    )
