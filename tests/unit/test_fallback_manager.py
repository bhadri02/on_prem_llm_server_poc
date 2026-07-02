"""
tests/unit/test_fallback_manager.py

Unit tests for intelligent_router.fallback_manager.

Covers:
  - advance() on a single-model chain returns None
  - advance() increments fallback_level by exactly 1
  - fallback_level never decreases
  - chain with 3 models advances correctly through all levels
  - has_next is False after last advance

Requirements: 3.7, 3.8, 4.3, 4.4, 4.7
"""

import pytest

from intelligent_router.fallback_manager import FallbackState, create_fallback_state
from intelligent_router.model_selector import ModelEntry, ModelMatrix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_matrix(*names: str) -> ModelMatrix:
    """Build a minimal ModelMatrix with a linear fallback chain from *names*.

    e.g. make_matrix("a", "b", "c") produces:
        a -> b -> c -> None
    """
    entries: dict[str, ModelEntry] = {}
    for i, name in enumerate(names):
        next_name = names[i + 1] if i + 1 < len(names) else None
        entries[name] = ModelEntry(
            name=name,
            backend="ollama",
            endpoint=f"http://localhost:11434/{name}",
            tasks=["chat"],
            health_url=f"http://localhost:11434/{name}/health",
            fallback=next_name,
        )
    task_defaults = {"chat": names[0]} if names else {}
    return ModelMatrix(models=entries, task_defaults=task_defaults)


# ---------------------------------------------------------------------------
# FallbackState direct construction tests
# ---------------------------------------------------------------------------


class TestFallbackStateProperties:
    def test_selected_model_returns_first_model(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        assert state.selected_model == "a"

    def test_selected_model_reflects_current_index(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=1, fallback_level=1)
        assert state.selected_model == "b"

    def test_has_next_true_when_not_at_end(self) -> None:
        state = FallbackState(chain=["a", "b"], current_index=0, fallback_level=0)
        assert state.has_next is True

    def test_has_next_false_on_single_model_chain(self) -> None:
        state = FallbackState(chain=["only"], current_index=0, fallback_level=0)
        assert state.has_next is False

    def test_has_next_false_at_last_position(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=2, fallback_level=2)
        assert state.has_next is False


class TestAdvanceSingleModel:
    """advance() on a single-model chain returns None."""

    def test_advance_single_model_returns_none(self) -> None:
        state = FallbackState(chain=["only"], current_index=0, fallback_level=0)
        result = state.advance()
        assert result is None

    def test_advance_single_model_does_not_change_index(self) -> None:
        state = FallbackState(chain=["only"], current_index=0, fallback_level=0)
        state.advance()
        assert state.current_index == 0

    def test_advance_single_model_does_not_change_fallback_level(self) -> None:
        state = FallbackState(chain=["only"], current_index=0, fallback_level=0)
        state.advance()
        assert state.fallback_level == 0


class TestAdvanceFallbackLevel:
    """advance() increments fallback_level by exactly 1 each time."""

    def test_advance_increments_fallback_level_by_one(self) -> None:
        state = FallbackState(chain=["a", "b"], current_index=0, fallback_level=0)
        state.advance()
        assert state.fallback_level == 1

    def test_advance_increments_fallback_level_by_one_each_call(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        state.advance()
        assert state.fallback_level == 1
        state.advance()
        assert state.fallback_level == 2

    def test_fallback_level_never_decreases(self) -> None:
        """fallback_level must be non-decreasing across all advance() calls."""
        state = FallbackState(chain=["a", "b", "c", "d"], current_index=0, fallback_level=0)
        levels = [state.fallback_level]
        while state.has_next:
            state.advance()
            levels.append(state.fallback_level)
        for i in range(1, len(levels)):
            assert levels[i] >= levels[i - 1], (
                f"fallback_level decreased from {levels[i-1]} to {levels[i]}"
            )

    def test_fallback_level_equals_current_index_throughout(self) -> None:
        """fallback_level must always equal current_index."""
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        assert state.fallback_level == state.current_index
        while state.has_next:
            state.advance()
            assert state.fallback_level == state.current_index


class TestAdvanceThreeModelChain:
    """chain with 3 models advances correctly through all levels."""

    def test_three_model_chain_first_advance(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        result = state.advance()
        assert result == "b"
        assert state.selected_model == "b"
        assert state.current_index == 1
        assert state.fallback_level == 1

    def test_three_model_chain_second_advance(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        state.advance()
        result = state.advance()
        assert result == "c"
        assert state.selected_model == "c"
        assert state.current_index == 2
        assert state.fallback_level == 2

    def test_three_model_chain_third_advance_returns_none(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        state.advance()
        state.advance()
        result = state.advance()
        assert result is None

    def test_three_model_chain_full_traversal_sequence(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        assert state.selected_model == "a"
        assert state.advance() == "b"
        assert state.advance() == "c"
        assert state.advance() is None


class TestHasNextAfterLastAdvance:
    """has_next is False after the last successful advance."""

    def test_has_next_false_after_last_advance_two_models(self) -> None:
        state = FallbackState(chain=["a", "b"], current_index=0, fallback_level=0)
        state.advance()
        assert state.has_next is False

    def test_has_next_false_after_last_advance_three_models(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        state.advance()
        state.advance()
        assert state.has_next is False

    def test_has_next_true_before_last_advance(self) -> None:
        state = FallbackState(chain=["a", "b", "c"], current_index=0, fallback_level=0)
        assert state.has_next is True
        state.advance()
        assert state.has_next is True  # still one more
        state.advance()
        assert state.has_next is False  # now exhausted


# ---------------------------------------------------------------------------
# create_fallback_state tests
# ---------------------------------------------------------------------------


class TestCreateFallbackState:
    def test_creates_state_with_correct_chain(self) -> None:
        matrix = make_matrix("primary", "fallback1", "fallback2")
        state = create_fallback_state("primary", matrix)
        assert state.chain == ["primary", "fallback1", "fallback2"]

    def test_creates_state_with_current_index_zero(self) -> None:
        matrix = make_matrix("primary", "fallback1")
        state = create_fallback_state("primary", matrix)
        assert state.current_index == 0

    def test_creates_state_with_fallback_level_zero(self) -> None:
        matrix = make_matrix("primary", "fallback1")
        state = create_fallback_state("primary", matrix)
        assert state.fallback_level == 0

    def test_selected_model_is_primary_on_creation(self) -> None:
        matrix = make_matrix("primary", "fallback1")
        state = create_fallback_state("primary", matrix)
        assert state.selected_model == "primary"

    def test_single_model_chain_from_matrix(self) -> None:
        matrix = make_matrix("only")
        state = create_fallback_state("only", matrix)
        assert state.chain == ["only"]
        assert state.has_next is False

    def test_unknown_primary_produces_single_element_chain(self) -> None:
        """If the primary model doesn't exist in the matrix, chain is just [primary]."""
        matrix = make_matrix("known")
        state = create_fallback_state("unknown", matrix)
        assert state.chain == ["unknown"]
        assert state.current_index == 0
        assert state.fallback_level == 0
