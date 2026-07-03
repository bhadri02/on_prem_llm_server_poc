"""
agent_framework/agent/session_store.py

In-memory session store for the Agent Framework (Layer 6).

Short-term session state lives in a plain Python dict protected by a single
asyncio.Lock. Because FastAPI runs on a single asyncio event loop, one lock
is sufficient for the POC. On pod restart the dict is lost — intentionally
acceptable for the POC.

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
"""

import asyncio
from dataclasses import dataclass


@dataclass
class StepRecord:
    """Represents a single ReAct loop step that involved a tool call."""

    step: int
    tool_called: str
    result_summary: str  # first 200 chars of tool result


class SessionStore:
    """In-process dict keyed by session_id, protected by asyncio.Lock."""

    def __init__(self) -> None:
        self._store: dict[str, list[StepRecord]] = {}
        self._lock = asyncio.Lock()

    async def init_session(self, session_id: str) -> None:
        """Creates or replaces a session entry with an empty step list."""
        async with self._lock:
            self._store[session_id] = []

    async def append_step(
        self,
        session_id: str,
        step: int,
        tool_called: str,
        result_summary: str,
    ) -> None:
        """Appends a step record to the session. No-op if session_id not found."""
        async with self._lock:
            if session_id in self._store:
                self._store[session_id].append(
                    StepRecord(
                        step=step,
                        tool_called=tool_called,
                        result_summary=result_summary,
                    )
                )

    async def get_steps(self, session_id: str) -> list[StepRecord]:
        """Returns a copy of the step list for session_id, or [] if not found."""
        async with self._lock:
            return list(self._store.get(session_id, []))

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._store


# Module-level singleton — created at process start.
session_store = SessionStore()
