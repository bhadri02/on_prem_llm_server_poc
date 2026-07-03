# Feature: agent-framework — Unit tests for session store
# Requirements: 9.1, 9.5
"""
tests/test_session_store.py

Unit tests for `agent/session_store.py` covering `SessionStore`.

Requirements covered:
  9.1 — init_session() creates or replaces a session entry with an empty list.
  9.5 — Session entry is retained after completion; get_steps() preserves insertion order.
"""

import asyncio

import pytest

from agent.session_store import SessionStore


# ---------------------------------------------------------------------------
# 1. init_session() replaces existing entry — Requirement 9.1
# ---------------------------------------------------------------------------


class TestInitSessionReplaces:
    """Two init_session() calls with the same session_id must leave an empty list."""

    async def test_double_init_produces_empty_list(self):
        """Calling init_session() twice on the same id discards the first entry
        so that get_steps() returns an empty list afterwards."""
        store = SessionStore()
        sid = "session-replace-test"

        # First init + append some data
        await store.init_session(sid)
        await store.append_step(sid, step=1, tool_called="calculator", result_summary="42")

        steps_after_first = await store.get_steps(sid)
        assert len(steps_after_first) == 1, "Expected one step after first init + append"

        # Second init should wipe the previous entry
        await store.init_session(sid)
        steps_after_second = await store.get_steps(sid)

        assert steps_after_second == [], (
            f"Expected empty list after re-init, got {steps_after_second}"
        )


# ---------------------------------------------------------------------------
# 2. append_step() with unknown session_id is a no-op
# ---------------------------------------------------------------------------


class TestAppendStepNoOp:
    """append_step() must silently do nothing for an unknown session_id."""

    async def test_append_to_unknown_session_is_noop(self):
        """Appending to a session that was never initialised must not raise and
        must not create a phantom entry in the store."""
        store = SessionStore()
        unknown_sid = "session-that-does-not-exist"

        # Should not raise
        await store.append_step(
            unknown_sid, step=1, tool_called="get_current_time", result_summary="2025-01-01"
        )

        # The session must still be absent
        steps = await store.get_steps(unknown_sid)
        assert steps == [], (
            f"Expected empty list for uninitialised session, got {steps}"
        )

        # __contains__ must also report the session as absent
        assert unknown_sid not in store, (
            "Unknown session_id must not be registered in the store"
        )

    async def test_append_to_unknown_does_not_affect_known_session(self):
        """A no-op append to an unknown id must not touch a separately-initialised session."""
        store = SessionStore()
        known_sid = "known-session"
        unknown_sid = "ghost-session"

        await store.init_session(known_sid)
        await store.append_step(known_sid, step=1, tool_called="calculator", result_summary="1")

        # No-op append to a different, unknown session
        await store.append_step(unknown_sid, step=1, tool_called="web_search", result_summary="x")

        steps = await store.get_steps(known_sid)
        assert len(steps) == 1, (
            "Known session must be unaffected by a no-op append to an unknown session"
        )


# ---------------------------------------------------------------------------
# 3. Concurrent init_session() calls produce distinct, independent entries
# ---------------------------------------------------------------------------


class TestConcurrentInitSession:
    """Concurrent init_session() calls for different session_ids must not
    interfere with each other."""

    async def test_concurrent_inits_produce_independent_entries(self):
        """asyncio.gather() on init_session() for N distinct session_ids must
        result in each session having its own independent, empty step list."""
        store = SessionStore()
        session_ids = [f"concurrent-session-{i}" for i in range(10)]

        # Launch all initialisations concurrently
        await asyncio.gather(*[store.init_session(sid) for sid in session_ids])

        # Each session must exist and be empty
        for sid in session_ids:
            assert sid in store, f"Session '{sid}' should exist after concurrent init"
            steps = await store.get_steps(sid)
            assert steps == [], f"Session '{sid}' should have an empty step list, got {steps}"

    async def test_concurrent_inits_entries_are_independent_after_appends(self):
        """Steps appended to one concurrently-initialised session must not appear
        in any other session."""
        store = SessionStore()
        session_ids = [f"indep-session-{i}" for i in range(5)]

        await asyncio.gather(*[store.init_session(sid) for sid in session_ids])

        # Append one unique step per session
        await asyncio.gather(*[
            store.append_step(
                sid,
                step=idx,
                tool_called="calculator",
                result_summary=f"result-{idx}",
            )
            for idx, sid in enumerate(session_ids)
        ])

        # Each session must contain exactly its own step
        for idx, sid in enumerate(session_ids):
            steps = await store.get_steps(sid)
            assert len(steps) == 1, (
                f"Session '{sid}' expected 1 step, got {len(steps)}"
            )
            assert steps[0].result_summary == f"result-{idx}", (
                f"Session '{sid}' has wrong result_summary: {steps[0].result_summary!r}"
            )


# ---------------------------------------------------------------------------
# 4. get_steps() returns records in insertion order — Requirement 9.5
# ---------------------------------------------------------------------------


class TestGetStepsInsertionOrder:
    """get_steps() must return StepRecords in the exact order they were appended."""

    async def test_steps_returned_in_insertion_order(self):
        """Appending steps 1, 2, 3 must be returned in the same order by get_steps()."""
        store = SessionStore()
        sid = "ordered-session"
        await store.init_session(sid)

        steps_data = [
            (1, "calculator", "42"),
            (2, "get_current_time", "2025-01-01T00:00:00+00:00"),
            (3, "web_search", "some search result"),
        ]

        for step, tool, summary in steps_data:
            await store.append_step(sid, step=step, tool_called=tool, result_summary=summary)

        retrieved = await store.get_steps(sid)

        assert len(retrieved) == len(steps_data), (
            f"Expected {len(steps_data)} steps, got {len(retrieved)}"
        )
        for i, (expected_step, expected_tool, expected_summary) in enumerate(steps_data):
            assert retrieved[i].step == expected_step, (
                f"Position {i}: expected step={expected_step}, got {retrieved[i].step}"
            )
            assert retrieved[i].tool_called == expected_tool, (
                f"Position {i}: expected tool_called={expected_tool!r}, "
                f"got {retrieved[i].tool_called!r}"
            )
            assert retrieved[i].result_summary == expected_summary, (
                f"Position {i}: expected result_summary={expected_summary!r}, "
                f"got {retrieved[i].result_summary!r}"
            )

    async def test_get_steps_returns_a_copy(self):
        """Mutating the list returned by get_steps() must not affect the store's
        internal state."""
        store = SessionStore()
        sid = "copy-test-session"
        await store.init_session(sid)
        await store.append_step(sid, step=1, tool_called="calculator", result_summary="1")

        steps = await store.get_steps(sid)
        steps.clear()  # Mutate the returned list

        # Store must still hold the original record
        steps_again = await store.get_steps(sid)
        assert len(steps_again) == 1, (
            "Mutating the returned list must not affect the internal store"
        )

    async def test_get_steps_unknown_session_returns_empty_list(self):
        """get_steps() for a session that was never initialised returns []."""
        store = SessionStore()
        result = await store.get_steps("nonexistent-session")
        assert result == [], f"Expected [], got {result}"
