"""
Property-based tests for model selection in the Intelligent Router.

Properties covered:
  - Property 3: Model Selection — Selected Model Always in Matrix
    In auto mode, select_model always returns a model name that exists in
    matrix.models, and the returned routing_mode is always "auto".
    In pinned mode with a valid model, select_model always returns exactly
    that model name.
"""

# ---------------------------------------------------------------------------
# Standard library / third-party
# ---------------------------------------------------------------------------
import string

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Register and load the 'ci' Hypothesis profile (max_examples=100).
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------
from intelligent_router.model_selector import (
    ModelEntry,
    ModelMatrix,
    NoModelForTaskError,
    select_model,
)

# ---------------------------------------------------------------------------
# Strategy: generate a ModelMatrix with random models and task_defaults
# ---------------------------------------------------------------------------

# Valid model name characters (simple alphanumeric + hyphens)
_MODEL_NAME_ALPHABET = string.ascii_lowercase + string.digits + "-"


def _model_name_strategy():
    """Strategy that generates valid model name strings."""
    return st.text(
        alphabet=_MODEL_NAME_ALPHABET,
        min_size=3,
        max_size=20,
    ).filter(lambda s: s[0].isalpha() and s[-1] != "-")


_TASK_TYPES = ["code", "reasoning", "summarization", "translation", "chat"]


def generated_model_matrix_strategy():
    """Strategy that generates a valid ModelMatrix with 1–4 models.

    All models are included in task_defaults so that auto-routing always
    has a valid model to select.
    """

    @st.composite
    def _build(draw):
        # Draw 1–4 unique model names
        num_models = draw(st.integers(min_value=1, max_value=4))
        model_names = draw(
            st.lists(
                _model_name_strategy(),
                min_size=num_models,
                max_size=num_models,
                unique=True,
            )
        )

        models: dict[str, ModelEntry] = {}
        for name in model_names:
            models[name] = ModelEntry(
                name=name,
                backend="ollama",
                endpoint=f"http://inference:{11434}",
                tasks=_TASK_TYPES,
                health_url=f"http://inference:{11434}/api/tags",
                fallback=None,
            )

        # Map each task type to one of the model names
        task_defaults: dict[str, str] = {
            task: draw(st.sampled_from(model_names))
            for task in _TASK_TYPES
        }

        return ModelMatrix(models=models, task_defaults=task_defaults)

    return _build()


# ---------------------------------------------------------------------------
# Property 3: Selected Model Always in Matrix (auto mode)
# ---------------------------------------------------------------------------

@given(
    task_type=st.sampled_from(["code", "reasoning", "summarization", "translation", "chat", "unknown_task"]),
    matrix=generated_model_matrix_strategy(),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_auto_select_always_returns_matrix_model(task_type, matrix):
    """**Validates: Requirements 3.1, 3.2, 3.6**

    Property 3: Model Selection — Selected Model Always in Matrix.

    For any task_type (including unknown ones) and any valid ModelMatrix,
    select_model in 'auto' mode MUST:
      1. Return a model name that is a key in matrix.models.
      2. Return routing_mode == "auto".

    For unknown task types, it falls back to the "chat" default.
    If no "chat" default exists (only when task_type is not in task_defaults AND
    "chat" is also not in task_defaults), it raises NoModelForTaskError.
    """
    # If neither the task type NOR "chat" is in task_defaults, select_model
    # will raise NoModelForTaskError — that is the correct behaviour.
    if task_type not in matrix.task_defaults and "chat" not in matrix.task_defaults:
        try:
            select_model(task_type, "auto", None, matrix)
            # Should have raised
            assert False, "Expected NoModelForTaskError was not raised"
        except NoModelForTaskError:
            return  # correct behaviour

    selected_name, effective_mode = select_model(task_type, "auto", None, matrix)

    assert selected_name in matrix.models, (
        f"select_model returned {selected_name!r} which is not in matrix.models "
        f"({list(matrix.models.keys())}) for task_type={task_type!r}"
    )
    assert effective_mode == "auto", (
        f"Expected routing_mode='auto' but got {effective_mode!r}"
    )


# ---------------------------------------------------------------------------
# Property 3 (continued): Pinned mode always returns the exact named model
# ---------------------------------------------------------------------------

@given(
    matrix=generated_model_matrix_strategy(),
    task_type=st.sampled_from(_TASK_TYPES),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_pinned_select_returns_exact_model(matrix, task_type):
    """**Validates: Requirements 3.1, 3.2, 3.6**

    In pinned mode with a valid model name present in matrix.models,
    select_model MUST return exactly that model name and routing_mode == "pinned".
    """
    # Pick any model from the matrix
    pinned = next(iter(matrix.models))

    selected_name, effective_mode = select_model(task_type, "pinned", pinned, matrix)

    assert selected_name == pinned, (
        f"Expected pinned model {pinned!r} but got {selected_name!r}"
    )
    assert effective_mode == "pinned", (
        f"Expected routing_mode='pinned' but got {effective_mode!r}"
    )
