"""
agent/orchestrator.py — LangGraph ReAct agent orchestrator (stub).

Full implementation is delivered in Task 8.
This stub is importable without errors and exposes the expected public surface.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_framework.agent.session_store import SessionStore


async def run_agent_session(
    imf: dict,
    tool_registry: dict,
    session_store: "SessionStore",
) -> tuple[dict, int]:
    """Placeholder — runs the ReAct agent loop for the given IMF.

    Returns (output_imf, http_status_code).
    Full implementation in Task 8.
    """
    raise NotImplementedError("orchestrator.run_agent_session is not yet implemented")
