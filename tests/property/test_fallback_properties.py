"""
Property-based tests for fallback chain traversal in the Intelligent Router.

Properties covered:
  - Property 5: Fallback Level Monotonicity
    FallbackState.fallback_level never decreases; after N successful advances
    it equals N; when all models are exhausted, has_next is False.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Register and load the 'ci' Hypothesis profile (max_examples=100).
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from intelligent_router.fallback_manager import FallbackState, create_fallback_state
from intelligent_router.model_selector import ModelEntry, ModelMatrix


# ---------------------------------------------------------------------------
# Helper: build a chained ModelMatrix with N models
# ---------------------------------------------------------------------------

def _build_chained_matrix(chain_length: int) -> tuple[ModelMatrix, str]:
    """Build a ModelMatrix with *chain_length* models chained via fallback links.

    Returns (matrix, primary_model_name).
    """
    assert chain_length >= 1

    model_names = [f"model-{i}" for i in range(chain_length)]

    models: dict[str, ModelEntry] = {}
    for i, name in enumerate(model_names):
        # Each model's fallback points to the next in the chain, except the last
        fallback = model_names[i + 1] if i + 1 < chain_length else None
        models[name] = ModelEntry(
            name=name,
            backend="ollama",
            endpoint=f"http://inference-{i}:11434",
            tasks=["chat"],
            health_url=f"http://inference-{i}:11434/health",
            fallback=fallback,
        )

    task_defaults = {"chat": model_names[0]}
    matrix = ModelMatrix(models=models, task_defaults=task_defaults)
    return matrix, model_names[0]


# ---------------------------------------------------------------------------
# Property 5: Fallback Level Monotonicity
# ---------------------------------------------------------------------------

@given(
    chain_length=st.integers(min_value=1, max_value=5),
    failures=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_fallback_level_monotonicity(chain_length, failures):
    """**Validates: Requirements 3.7, 3.8, 4.3, 4.4, 4.5, 4.7, 6.3, 6.4**

    Property 5: Fallback Level Monotonicity.

    Given a fallback chain of length `chain_length` and `failures` attempted
    advances:

    1. fallback_level after N successful advances == N.
    2. fallback_level never decreases between successive advance() calls.
    3. When failures >= chain_length (all models exhausted), the final
       fallback_level == chain_length - 1 and has_next == False.
    4. advance() returns None exactly when the chain is exhausted.
    """
    matrix, primary = _build_chained_matrix(chain_length)
    state: FallbackState = create_fallback_state(primary, matrix)

    # Validate initial state
    assert state.fallback_level == 0, (
        f"Initial fallback_level should be 0, got {state.fallback_level}"
    )
    assert state.current_index == 0, (
        f"Initial current_index should be 0, got {state.current_index}"
    )
    assert state.selected_model == primary, (
        f"Initial selected_model should be {primary!r}, got {state.selected_model!r}"
    )

    # Advance min(failures, chain_length) times and track monotonicity
    actual_advances = min(failures, chain_length)
    prev_level = 0
    steps_advanced = 0

    for step in range(failures):
        if not state.has_next:
            # Chain exhausted — advance() should return None
            result = state.advance()
            assert result is None, (
                f"advance() should return None when chain is exhausted, "
                f"got {result!r} at step {step}"
            )
            # Level should not have changed
            assert state.fallback_level == prev_level, (
                f"fallback_level changed from {prev_level} to {state.fallback_level} "
                f"after a no-op advance() at step {step}"
            )
            continue

        result = state.advance()

        assert result is not None, (
            f"advance() returned None but has_next was True before advance at step {step}"
        )

        # Monotonicity: level must have increased by exactly 1
        assert state.fallback_level == prev_level + 1, (
            f"fallback_level should be {prev_level + 1} after advance, "
            f"got {state.fallback_level} at step {step}"
        )
        assert state.fallback_level >= prev_level, (
            f"fallback_level decreased from {prev_level} to {state.fallback_level} "
            f"at step {step} — monotonicity violated"
        )

        prev_level = state.fallback_level
        steps_advanced += 1

    # After exhausting chain: fallback_level == chain_length - 1, has_next == False
    if failures >= chain_length:
        assert state.fallback_level == chain_length - 1, (
            f"After exhausting chain of length {chain_length}, expected "
            f"fallback_level == {chain_length - 1}, got {state.fallback_level}"
        )
        assert not state.has_next, (
            f"has_next should be False when chain is exhausted, "
            f"chain_length={chain_length}, failures={failures}"
        )

    # fallback_level always matches steps_actually_advanced
    expected_level = min(failures, chain_length - 1)
    assert state.fallback_level == expected_level, (
        f"Expected fallback_level == {expected_level} "
        f"(chain_length={chain_length}, failures={failures}), "
        f"got {state.fallback_level}"
    )


# ---------------------------------------------------------------------------
# Deterministic edge cases
# ---------------------------------------------------------------------------

def test_single_model_chain_advance_returns_none():
    """advance() on a single-model chain returns None immediately."""
    matrix, primary = _build_chained_matrix(1)
    state = create_fallback_state(primary, matrix)

    assert state.has_next is False
    result = state.advance()
    assert result is None
    assert state.fallback_level == 0  # unchanged


def test_two_model_chain_full_traversal():
    """Two-model chain: first advance succeeds, second returns None."""
    matrix, primary = _build_chained_matrix(2)
    state = create_fallback_state(primary, matrix)

    assert state.fallback_level == 0
    assert state.has_next is True

    result1 = state.advance()
    assert result1 == "model-1"
    assert state.fallback_level == 1
    assert state.has_next is False

    result2 = state.advance()
    assert result2 is None
    assert state.fallback_level == 1  # unchanged after exhaustion
