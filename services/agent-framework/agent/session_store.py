"""
agent/session_store.py — In-memory session store.

Implements a concurrency-safe, in-process session dictionary protected by a
single asyncio.Lock. Intentionally simple for the POC — state is lost on pod
restart, which is acceptable.

Requirements satisfied:
  9.1 — init_session() creates or replaces a session entry with an empty list.
  9.2 — append_step() records step/tool_called/result_summary per tool call.
  9.4 — No external persistence; pure in-memory.
  9.5 — Session entry is retained after completion; no further appends are
         enforced by the orchestrator, not the store itself.
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
    """In-process dict keyed by session_id, protected by asyncio.Lock.

    A single asyncio.Lock is sufficient because FastAPI's async handlers run
    on a single asyncio event loop. All read/write operations acquire the lock
    via ``async with`` to prevent concurrent coroutines from interleaving.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[StepRecord]] = {}
        self._lock = asyncio.Lock()

    async def init_session(self, session_id: str) -> None:
        """Creates or replaces a session entry with an empty step list.

        Any existing entry for *session_id* is discarded (Req 9.1).
        """
        async with self._lock:
            self._store[session_id] = []

    async def append_step(
        self,
        session_id: str,
        step: int,
        tool_called: str,
        result_summary: str,
    ) -> None:
        """Appends a StepRecord to the session. No-op if session_id not found.

        The no-op behaviour for unknown session IDs prevents silent data loss
        from race conditions where the session has not yet been initialised.
        """
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
        """Returns a shallow copy of the step list for *session_id*.

        Returns an empty list if the session does not exist.
        The copy prevents callers from mutating the internal list.
        """
        async with self._lock:
            return list(self._store.get(session_id, []))

    def __contains__(self, session_id: str) -> bool:
        """Synchronous membership test — safe to call without the lock for
        read-only checks where stale results are acceptable (e.g. audit logging).
        """
        return session_id in self._store


# Module-level singleton — created once at process start.
session_store = SessionStore()
