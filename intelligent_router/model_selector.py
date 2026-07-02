"""
intelligent_router/model_selector.py

Model matrix loading and model selection logic for the Intelligent Router (Layer 3).

Provides:
  - ModelEntry: dataclass representing a single model in the matrix.
  - ModelMatrix: dataclass holding the full model registry and task defaults.
  - InvalidPinnedModelError: raised when a pinned model is absent/unknown.
  - NoModelForTaskError: raised when no model can be found for a task type and
    the 'chat' fallback default is also absent.
  - load_model_matrix(path): reads model_matrix.yaml, validates it, and returns
    a ModelMatrix on success, or None on any failure (logging a specific ERROR
    in each case).
  - select_model(task_type, routing_mode, pinned_model, matrix): returns
    (selected_model_name, effective_routing_mode) or raises one of the custom
    exceptions above.
  - get_fallback_chain(model_name, matrix): follows fallback links starting from
    model_name, stopping on None or on a cycle, returning the ordered chain.
"""

import pathlib
from dataclasses import dataclass
from typing import Optional

import yaml

from intelligent_router.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions (subtask 6.3)
# ---------------------------------------------------------------------------


class InvalidPinnedModelError(Exception):
    """Raised when routing_mode is 'pinned' but the pinned model is invalid.

    A model is considered invalid when:
      - ``pinned_model`` is None or an empty string, OR
      - ``pinned_model`` names a model not present in the ModelMatrix.
    """

    def __init__(self, model: Optional[str]) -> None:
        self.model = model
        super().__init__(
            f"Pinned model {model!r} is invalid or not present in the model matrix"
        )


class NoModelForTaskError(Exception):
    """Raised when no model mapping exists for the given task type and the
    'chat' default entry is also absent from task_defaults.
    """

    def __init__(self, task_type: str) -> None:
        self.task_type = task_type
        super().__init__(
            f"No model configured for task type {task_type!r} "
            "and 'chat' default is also absent"
        )


# ---------------------------------------------------------------------------
# Data classes (subtask 6.1)
# ---------------------------------------------------------------------------


@dataclass
class ModelEntry:
    """Represents a single model entry in the model matrix YAML.

    Attributes:
        name:       Unique model identifier (matches the key in the YAML map).
        backend:    Backend type (e.g. 'ollama', 'vllm', 'tgi').
        endpoint:   Full URL of the inference endpoint.
        tasks:      List of task types this model is capable of serving.
        health_url: URL used by the Health Checker to test backend liveness.
        fallback:   Name of the fallback model to use if this model is
                    unavailable, or None if this is the last model in the chain.
    """

    name: str
    backend: str
    endpoint: str
    tasks: list[str]
    health_url: str
    fallback: Optional[str]


@dataclass
class ModelMatrix:
    """Holds the full model capability matrix loaded from model_matrix.yaml.

    Attributes:
        models:        Mapping of model_name -> ModelEntry.
        task_defaults: Mapping of task_type -> primary model_name.
    """

    models: dict[str, ModelEntry]
    task_defaults: dict[str, str]


# ---------------------------------------------------------------------------
# Matrix loading (subtask 6.2)
# ---------------------------------------------------------------------------


def load_model_matrix(path: str) -> Optional[ModelMatrix]:
    """Load the model matrix from the YAML file at *path*.

    Returns a :class:`ModelMatrix` instance on success.
    Returns ``None`` — and logs a specific ERROR — on any of the following
    failure conditions:

    - ``FileNotFoundError``: the file does not exist at *path*.
    - ``yaml.YAMLError``: the file content is not valid YAML.
    - The ``models`` map is missing or empty.
    - The ``task_defaults`` map is missing or empty.
    - Any other unexpected exception during reading or parsing.

    Each failure condition produces a distinct ERROR log message so that the
    startup handler in ``main.py`` can surface the root cause without
    additional diagnostics.
    """
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)

        if not isinstance(data, dict):
            logger.error(
                f"Model matrix file is empty or not a YAML mapping: {path}; "
                "refusing to start"
            )
            return None

        raw_models = data.get("models") or {}
        if not raw_models:
            logger.error(
                f"Model matrix 'models' map is empty: {path}; refusing to start"
            )
            return None

        task_defaults = data.get("task_defaults") or {}
        if not task_defaults:
            logger.error(
                f"Model matrix 'task_defaults' map is empty: {path}; refusing to start"
            )
            return None

        models: dict[str, ModelEntry] = {
            name: ModelEntry(name=name, **entry)
            for name, entry in raw_models.items()
        }

        return ModelMatrix(models=models, task_defaults=task_defaults)

    except FileNotFoundError:
        logger.error(f"Model matrix file not found: {path}")
    except yaml.YAMLError as exc:
        logger.error(f"Malformed model matrix YAML: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to read model matrix file '{path}': {exc}")

    return None


# ---------------------------------------------------------------------------
# Model selection (subtask 6.3)
# ---------------------------------------------------------------------------


def select_model(
    task_type: str,
    routing_mode: str,
    pinned_model: Optional[str],
    matrix: ModelMatrix,
) -> tuple[str, str]:
    """Select the inference model and resolve the effective routing mode.

    Args:
        task_type:     The classified task type (e.g. 'code', 'chat').
        routing_mode:  Either 'pinned' or 'auto' (absent/None treated as 'auto').
        pinned_model:  The value of ``request.model`` from the IMF; only
                       relevant when ``routing_mode == 'pinned'``.
        matrix:        The loaded :class:`ModelMatrix`.

    Returns:
        A ``(selected_model_name, effective_routing_mode)`` tuple where
        ``effective_routing_mode`` is always either ``'pinned'`` or ``'auto'``.

    Raises:
        InvalidPinnedModelError: When ``routing_mode`` is ``'pinned'`` and
            ``pinned_model`` is absent, empty, or not present in *matrix.models*.
        NoModelForTaskError: When ``routing_mode`` is ``'auto'`` (or absent),
            no entry exists for *task_type* in *task_defaults*, AND the
            ``'chat'`` fallback entry is also absent.
    """
    if routing_mode == "pinned":
        if not pinned_model or pinned_model not in matrix.models:
            raise InvalidPinnedModelError(pinned_model)
        return pinned_model, "pinned"

    # auto mode: look up the primary model for the given task type,
    # falling back to the 'chat' default if the task type is not mapped.
    primary = matrix.task_defaults.get(task_type) or matrix.task_defaults.get("chat")
    if not primary:
        raise NoModelForTaskError(task_type)
    return primary, "auto"


# ---------------------------------------------------------------------------
# Fallback chain (subtask 6.4)
# ---------------------------------------------------------------------------


def get_fallback_chain(model_name: str, matrix: ModelMatrix) -> list[str]:
    """Return the ordered fallback chain starting from *model_name*.

    Follows ``ModelEntry.fallback`` links until either:
      - a ``None`` fallback is encountered (end of the chain), or
      - a model name already present in the ``visited`` set is encountered
        (cycle detected — the cycle member is NOT appended again).

    If *model_name* itself is not in *matrix.models*, only *model_name* is
    returned (the chain still starts from the requested model, even if no
    entry exists to continue from).

    Args:
        model_name: Name of the starting model (typically the primary model).
        matrix:     The loaded :class:`ModelMatrix`.

    Returns:
        A list of model name strings in traversal order, always containing at
        least *model_name*.
    """
    chain: list[str] = []
    current: Optional[str] = model_name
    visited: set[str] = set()

    while current and current not in visited:
        chain.append(current)
        visited.add(current)
        entry = matrix.models.get(current)
        current = entry.fallback if entry else None

    return chain
