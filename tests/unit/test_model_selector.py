"""
tests/unit/test_model_selector.py

Unit tests for intelligent_router.model_selector.

Covers:
  - load_model_matrix: file not found → None + ERROR log
  - load_model_matrix: malformed YAML → None + ERROR log
  - load_model_matrix: empty 'models' map → None + ERROR log
  - load_model_matrix: empty 'task_defaults' map → None + ERROR log
  - load_model_matrix: valid YAML → correct ModelMatrix and ModelEntry objects
  - select_model: auto mode selects the correct primary model
  - select_model: pinned mode with a valid model name succeeds
  - select_model: pinned mode with an unknown model raises InvalidPinnedModelError
  - select_model: missing task_type falls back to the 'chat' default
  - select_model: missing task_type AND missing 'chat' default raises NoModelForTaskError
  - get_fallback_chain: follows fallback links correctly
  - get_fallback_chain: stops on None (end of chain)
  - get_fallback_chain: single-model matrix returns chain of length 1
  - get_fallback_chain: cycle detection stops the traversal (no infinite loop)
  - get_fallback_chain: unknown starting model returns chain of length 1

Note on log capture:
  get_logger() attaches a StreamHandler(sys.stdout) with propagate=False, so
  pytest's caplog cannot intercept records.  Tests that need to assert on ERROR
  logs attach a StringIO handler directly to the module-level logger, identical
  to the pattern used in test_task_classifier.py.
"""

import io
import logging
import textwrap

import pytest

from intelligent_router.model_selector import (
    InvalidPinnedModelError,
    ModelEntry,
    ModelMatrix,
    NoModelForTaskError,
    get_fallback_chain,
    load_model_matrix,
    select_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_error_logs(fn):
    """Call fn() and return ERROR-level message strings from the model_selector logger."""
    import intelligent_router.model_selector as _mod

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _mod.logger.addHandler(handler)
    try:
        fn()
    finally:
        _mod.logger.removeHandler(handler)
    lines = buf.getvalue().splitlines()
    return [line for line in lines if line.strip()]


def _make_entry(
    name: str,
    backend: str = "ollama",
    endpoint: str = "http://inference:11434",
    tasks: list[str] | None = None,
    health_url: str = "http://inference:11434/api/tags",
    fallback: str | None = None,
) -> ModelEntry:
    """Build a ModelEntry with sensible defaults."""
    return ModelEntry(
        name=name,
        backend=backend,
        endpoint=endpoint,
        tasks=tasks or ["chat"],
        health_url=health_url,
        fallback=fallback,
    )


def _make_matrix(
    models: dict[str, ModelEntry] | None = None,
    task_defaults: dict[str, str] | None = None,
) -> ModelMatrix:
    """Build a ModelMatrix with sensible defaults."""
    if models is None:
        entry = _make_entry("llama3")
        models = {"llama3": entry}
    if task_defaults is None:
        task_defaults = {"chat": "llama3"}
    return ModelMatrix(models=models, task_defaults=task_defaults)


# Minimal valid YAML content used across multiple tests.
VALID_YAML = textwrap.dedent("""\
    models:
      llama3:
        backend: ollama
        endpoint: http://inference-ollama:11434
        tasks:
          - chat
          - code
        health_url: http://inference-ollama:11434/api/tags
        fallback: null
      mistral:
        backend: ollama
        endpoint: http://inference-ollama:11434
        tasks:
          - chat
        health_url: http://inference-ollama:11434/api/tags
        fallback: null
    task_defaults:
      chat: llama3
      code: llama3
      reasoning: llama3
      summarization: llama3
      translation: llama3
""")


# ---------------------------------------------------------------------------
# load_model_matrix — file not found
# ---------------------------------------------------------------------------


class TestLoadModelMatrixFileNotFound:
    def test_returns_none_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "no_such_file.yaml")
        result = load_model_matrix(missing)
        assert result is None

    def test_logs_error_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "no_such_file.yaml")
        errors = _capture_error_logs(lambda: load_model_matrix(missing))
        assert any(
            "not found" in m.lower() or "no_such_file" in m for m in errors
        ), f"Expected a 'not found' ERROR, got: {errors}"


# ---------------------------------------------------------------------------
# load_model_matrix — malformed YAML
# ---------------------------------------------------------------------------


class TestLoadModelMatrixMalformedYaml:
    def test_returns_none_on_malformed_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("models: [\ndefault: chat", encoding="utf-8")
        result = load_model_matrix(str(bad))
        assert result is None

    def test_logs_error_on_malformed_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("models: [\ndefault: chat", encoding="utf-8")
        errors = _capture_error_logs(lambda: load_model_matrix(str(bad)))
        assert any(
            "malformed" in m.lower() or "yaml" in m.lower() for m in errors
        ), f"Expected a malformed-YAML ERROR, got: {errors}"


# ---------------------------------------------------------------------------
# load_model_matrix — empty 'models' map
# ---------------------------------------------------------------------------


class TestLoadModelMatrixEmptyModels:
    def test_returns_none_when_models_map_empty(self, tmp_path):
        yaml_content = "models: {}\ntask_defaults:\n  chat: llama3\n"
        f = tmp_path / "empty_models.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is None

    def test_logs_error_when_models_map_empty(self, tmp_path):
        yaml_content = "models: {}\ntask_defaults:\n  chat: llama3\n"
        f = tmp_path / "empty_models.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        errors = _capture_error_logs(lambda: load_model_matrix(str(f)))
        assert any(
            "models" in m.lower() and ("empty" in m.lower() or "refusing" in m.lower())
            for m in errors
        ), f"Expected 'models map is empty' ERROR, got: {errors}"

    def test_returns_none_when_models_key_absent(self, tmp_path):
        yaml_content = "task_defaults:\n  chat: llama3\n"
        f = tmp_path / "no_models.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is None


# ---------------------------------------------------------------------------
# load_model_matrix — empty 'task_defaults' map
# ---------------------------------------------------------------------------


class TestLoadModelMatrixEmptyTaskDefaults:
    def test_returns_none_when_task_defaults_empty(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            models:
              llama3:
                backend: ollama
                endpoint: http://inference:11434
                tasks: [chat]
                health_url: http://inference:11434/api/tags
                fallback: null
            task_defaults: {}
        """)
        f = tmp_path / "empty_defaults.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is None

    def test_logs_error_when_task_defaults_empty(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            models:
              llama3:
                backend: ollama
                endpoint: http://inference:11434
                tasks: [chat]
                health_url: http://inference:11434/api/tags
                fallback: null
            task_defaults: {}
        """)
        f = tmp_path / "empty_defaults.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        errors = _capture_error_logs(lambda: load_model_matrix(str(f)))
        assert any(
            "task_defaults" in m.lower() and ("empty" in m.lower() or "refusing" in m.lower())
            for m in errors
        ), f"Expected 'task_defaults map is empty' ERROR, got: {errors}"

    def test_returns_none_when_task_defaults_key_absent(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            models:
              llama3:
                backend: ollama
                endpoint: http://inference:11434
                tasks: [chat]
                health_url: http://inference:11434/api/tags
                fallback: null
        """)
        f = tmp_path / "no_defaults.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is None


# ---------------------------------------------------------------------------
# load_model_matrix — valid YAML
# ---------------------------------------------------------------------------


class TestLoadModelMatrixValidYaml:
    def test_returns_model_matrix_instance(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text(VALID_YAML, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert isinstance(result, ModelMatrix)

    def test_models_map_populated(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text(VALID_YAML, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is not None
        assert "llama3" in result.models
        assert "mistral" in result.models

    def test_model_entry_fields_correct(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text(VALID_YAML, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is not None
        entry = result.models["llama3"]
        assert isinstance(entry, ModelEntry)
        assert entry.name == "llama3"
        assert entry.backend == "ollama"
        assert entry.endpoint == "http://inference-ollama:11434"
        assert entry.tasks == ["chat", "code"]
        assert entry.health_url == "http://inference-ollama:11434/api/tags"
        assert entry.fallback is None

    def test_task_defaults_populated(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text(VALID_YAML, encoding="utf-8")
        result = load_model_matrix(str(f))
        assert result is not None
        assert result.task_defaults["chat"] == "llama3"
        assert result.task_defaults["code"] == "llama3"


# ---------------------------------------------------------------------------
# select_model — auto mode
# ---------------------------------------------------------------------------


class TestSelectModelAuto:
    def test_auto_mode_returns_primary_model_for_task(self):
        entry = _make_entry("llama3")
        matrix = ModelMatrix(
            models={"llama3": entry},
            task_defaults={"code": "llama3", "chat": "llama3"},
        )
        model, mode = select_model("code", "auto", None, matrix)
        assert model == "llama3"
        assert mode == "auto"

    def test_auto_mode_explicit(self):
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry}, task_defaults={"chat": "llama3"})
        model, mode = select_model("chat", "auto", None, matrix)
        assert model == "llama3"
        assert mode == "auto"

    def test_absent_routing_mode_treated_as_auto(self):
        """routing_mode values that are not 'pinned' are treated as auto."""
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry}, task_defaults={"chat": "llama3"})
        # routing_mode="" is not "pinned" so falls into auto path
        model, mode = select_model("chat", "", None, matrix)
        assert model == "llama3"
        assert mode == "auto"


# ---------------------------------------------------------------------------
# select_model — missing task_type falls back to 'chat' default
# ---------------------------------------------------------------------------


class TestSelectModelFallbackToChat:
    def test_unknown_task_type_falls_back_to_chat(self):
        entry = _make_entry("llama3")
        matrix = ModelMatrix(
            models={"llama3": entry},
            task_defaults={"chat": "llama3"},
        )
        model, mode = select_model("unknown_task", "auto", None, matrix)
        assert model == "llama3"
        assert mode == "auto"

    def test_missing_specific_task_uses_chat_default(self):
        """No 'code' in task_defaults, but 'chat' is present."""
        entry = _make_entry("llama3")
        matrix = ModelMatrix(
            models={"llama3": entry},
            task_defaults={"chat": "llama3"},
        )
        model, mode = select_model("code", "auto", None, matrix)
        assert model == "llama3"
        assert mode == "auto"


# ---------------------------------------------------------------------------
# select_model — NoModelForTaskError
# ---------------------------------------------------------------------------


class TestSelectModelNoModelForTask:
    def test_raises_when_task_type_absent_and_no_chat_default(self):
        entry = _make_entry("llama3")
        matrix = ModelMatrix(
            models={"llama3": entry},
            task_defaults={"code": "llama3"},  # no 'chat' default
        )
        with pytest.raises(NoModelForTaskError) as exc_info:
            select_model("summarization", "auto", None, matrix)
        assert exc_info.value.task_type == "summarization"

    def test_raises_with_empty_task_defaults(self):
        entry = _make_entry("llama3")
        # This should not happen at runtime (load_model_matrix rejects empty
        # task_defaults), but we test the selection logic independently.
        matrix = ModelMatrix(
            models={"llama3": entry},
            task_defaults={},
        )
        with pytest.raises(NoModelForTaskError):
            select_model("chat", "auto", None, matrix)

    def test_error_message_contains_task_type(self):
        entry = _make_entry("llama3")
        matrix = ModelMatrix(
            models={"llama3": entry},
            task_defaults={"code": "llama3"},
        )
        with pytest.raises(NoModelForTaskError) as exc_info:
            select_model("vision", "auto", None, matrix)
        assert "vision" in str(exc_info.value)


# ---------------------------------------------------------------------------
# select_model — pinned mode (valid)
# ---------------------------------------------------------------------------


class TestSelectModelPinnedValid:
    def test_pinned_mode_returns_exact_model_name(self):
        entry = _make_entry("mistral")
        matrix = ModelMatrix(
            models={"mistral": entry, "llama3": _make_entry("llama3")},
            task_defaults={"chat": "llama3"},
        )
        model, mode = select_model("chat", "pinned", "mistral", matrix)
        assert model == "mistral"
        assert mode == "pinned"

    def test_pinned_mode_ignores_task_type(self):
        """Pinned mode should return the requested model regardless of task_type."""
        entry = _make_entry("mistral")
        matrix = ModelMatrix(
            models={"mistral": entry, "llama3": _make_entry("llama3")},
            task_defaults={"chat": "llama3"},
        )
        model, mode = select_model("code", "pinned", "mistral", matrix)
        assert model == "mistral"
        assert mode == "pinned"


# ---------------------------------------------------------------------------
# select_model — pinned mode (invalid → InvalidPinnedModelError)
# ---------------------------------------------------------------------------


class TestSelectModelPinnedInvalid:
    def test_raises_for_unknown_pinned_model(self):
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry}, task_defaults={"chat": "llama3"})
        with pytest.raises(InvalidPinnedModelError) as exc_info:
            select_model("chat", "pinned", "does-not-exist", matrix)
        assert exc_info.value.model == "does-not-exist"

    def test_raises_when_pinned_model_is_none(self):
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry}, task_defaults={"chat": "llama3"})
        with pytest.raises(InvalidPinnedModelError) as exc_info:
            select_model("chat", "pinned", None, matrix)
        assert exc_info.value.model is None

    def test_raises_when_pinned_model_is_empty_string(self):
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry}, task_defaults={"chat": "llama3"})
        with pytest.raises(InvalidPinnedModelError) as exc_info:
            select_model("chat", "pinned", "", matrix)
        assert exc_info.value.model == ""

    def test_error_message_contains_model_name(self):
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry}, task_defaults={"chat": "llama3"})
        with pytest.raises(InvalidPinnedModelError) as exc_info:
            select_model("chat", "pinned", "gpt-4", matrix)
        assert "gpt-4" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_fallback_chain — basic traversal
# ---------------------------------------------------------------------------


class TestGetFallbackChain:
    def test_single_model_no_fallback_returns_length_one(self):
        entry = _make_entry("llama3", fallback=None)
        matrix = _make_matrix(models={"llama3": entry})
        chain = get_fallback_chain("llama3", matrix)
        assert chain == ["llama3"]

    def test_two_model_chain_follows_fallback_link(self):
        entry_a = _make_entry("model-a", fallback="model-b")
        entry_b = _make_entry("model-b", fallback=None)
        matrix = ModelMatrix(
            models={"model-a": entry_a, "model-b": entry_b},
            task_defaults={"chat": "model-a"},
        )
        chain = get_fallback_chain("model-a", matrix)
        assert chain == ["model-a", "model-b"]

    def test_three_model_chain(self):
        a = _make_entry("a", fallback="b")
        b = _make_entry("b", fallback="c")
        c = _make_entry("c", fallback=None)
        matrix = ModelMatrix(
            models={"a": a, "b": b, "c": c},
            task_defaults={"chat": "a"},
        )
        chain = get_fallback_chain("a", matrix)
        assert chain == ["a", "b", "c"]

    def test_stops_at_none_fallback(self):
        a = _make_entry("alpha", fallback="beta")
        b = _make_entry("beta", fallback=None)
        matrix = ModelMatrix(
            models={"alpha": a, "beta": b},
            task_defaults={"chat": "alpha"},
        )
        chain = get_fallback_chain("alpha", matrix)
        assert chain[-1] == "beta"
        assert len(chain) == 2

    def test_chain_always_starts_with_given_model(self):
        a = _make_entry("primary", fallback="secondary")
        b = _make_entry("secondary", fallback=None)
        matrix = ModelMatrix(
            models={"primary": a, "secondary": b},
            task_defaults={"chat": "primary"},
        )
        chain = get_fallback_chain("primary", matrix)
        assert chain[0] == "primary"

    def test_unknown_starting_model_returns_single_entry(self):
        """If starting model is not in the matrix, return it alone (no crash)."""
        entry = _make_entry("llama3")
        matrix = _make_matrix(models={"llama3": entry})
        chain = get_fallback_chain("unknown-model", matrix)
        assert chain == ["unknown-model"]


# ---------------------------------------------------------------------------
# get_fallback_chain — cycle detection
# ---------------------------------------------------------------------------


class TestGetFallbackChainCycleDetection:
    def test_direct_cycle_does_not_loop_forever(self):
        """a -> b -> a  (cycle between two models)."""
        a = _make_entry("a", fallback="b")
        b = _make_entry("b", fallback="a")  # cycle back to a
        matrix = ModelMatrix(
            models={"a": a, "b": b},
            task_defaults={"chat": "a"},
        )
        chain = get_fallback_chain("a", matrix)
        # Should contain each model exactly once; cycle member not re-appended.
        assert chain == ["a", "b"]

    def test_self_reference_cycle(self):
        """a -> a  (self-referential fallback)."""
        a = _make_entry("a", fallback="a")
        matrix = ModelMatrix(models={"a": a}, task_defaults={"chat": "a"})
        chain = get_fallback_chain("a", matrix)
        assert chain == ["a"]

    def test_three_node_cycle(self):
        """a -> b -> c -> a."""
        a = _make_entry("a", fallback="b")
        b = _make_entry("b", fallback="c")
        c = _make_entry("c", fallback="a")  # cycle back to a
        matrix = ModelMatrix(
            models={"a": a, "b": b, "c": c},
            task_defaults={"chat": "a"},
        )
        chain = get_fallback_chain("a", matrix)
        assert chain == ["a", "b", "c"]

    def test_each_model_appears_at_most_once(self):
        a = _make_entry("a", fallback="b")
        b = _make_entry("b", fallback="c")
        c = _make_entry("c", fallback="a")
        matrix = ModelMatrix(
            models={"a": a, "b": b, "c": c},
            task_defaults={"chat": "a"},
        )
        chain = get_fallback_chain("a", matrix)
        assert len(chain) == len(set(chain)), "Duplicate model names in fallback chain"
