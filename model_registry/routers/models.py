"""
Models router for the Model Registry.

Implements the /models endpoints:
  GET  /models                       — list all registered models (masked)
  GET  /models/by-task/{task_type}   — list active models supporting a task (masked)
  GET  /models/{name}                — retrieve a single model by name (masked)
  POST /models                       — register a new model (auth required)
  PATCH /models/{name}/status        — update a model's status (auth required)
  PATCH /models/{name}/api-key       — set/update a cloud model's provider API key (auth required)
  GET  /models/{name}/secret         — internal-only: returns the raw api_key (auth required)

Route ordering: by-task and {name}/secret are declared before {name} to avoid
path-parameter clashes.

"Masked" responses use ModelRecordPublic — api_key is never included, only
an api_key_set boolean. Only GET /models/{name}/secret returns the raw key,
and it requires the same X-API-Key as the other mutating endpoints (Inference
Adapter uses it to resolve cloud-backend credentials at dispatch time).
"""

import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from model_registry.exceptions import (
    DuplicateNameError,
    ModelNotFoundError,
    PersistenceError,
)
from model_registry.schemas.model import (
    ApiKeyUpdateRequest,
    ModelRecord,
    ModelRecordCreate,
    ModelRecordPublic,
    StatusUpdateRequest,
    TaskType,
)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/models", tags=["models"])

# Path-parameter character-set constraint
_NAME_RE = re.compile(r"^[a-zA-Z0-9._:-]+$")  # ':' allowed — Ollama tags are "name:tag"


def _get_record_or_404(name: str, request: Request) -> ModelRecord:
    """Validate *name*'s character set and look it up, raising HTTPException
    (422 on invalid chars, 404 if not found) exactly like every existing
    by-name endpoint in this router. Shared to avoid repeating the same
    validate-then-lookup block on every handler."""
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid model name '{name}'. "
                "Name must match pattern ^[a-zA-Z0-9._:-]+$"
            ),
        )

    storage = request.app.state.storage
    record = storage.get_by_name(name)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{name}' not found.",
        )
    return record


# ---------------------------------------------------------------------------
# GET /models  — list all records
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=list[ModelRecordPublic],
    status_code=status.HTTP_200_OK,
    summary="List all registered models",
)
async def list_models(request: Request) -> list[ModelRecordPublic]:
    """Return every ModelRecord currently held in the store (api_key masked)."""
    storage = request.app.state.storage
    return [ModelRecordPublic.from_record(r) for r in storage.get_all()]


# ---------------------------------------------------------------------------
# GET /models/by-task/{task_type}  — MUST come before GET /{name}
# ---------------------------------------------------------------------------


@router.get(
    "/by-task/{task_type}",
    response_model=list[ModelRecordPublic],
    status_code=status.HTTP_200_OK,
    summary="List active models by task type",
)
async def list_models_by_task(task_type: str, request: Request) -> list[ModelRecordPublic]:
    """
    Return all active ModelRecords that support *task_type* (api_key masked).

    Raises HTTP 422 if *task_type* is not a member of the ``TaskType`` enum.
    An empty list is a valid result when no active model supports the task.
    """
    # Validate task_type against the TaskType enum (422 on invalid)
    try:
        validated_task = TaskType(task_type)
    except ValueError:
        valid_values = [t.value for t in TaskType]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid task_type '{task_type}'. "
                f"Accepted values: {valid_values}"
            ),
        )

    storage = request.app.state.storage
    return [ModelRecordPublic.from_record(r) for r in storage.get_by_task(validated_task)]


# ---------------------------------------------------------------------------
# GET /models/{name}  — retrieve a single record
# ---------------------------------------------------------------------------


@router.get(
    "/{name}",
    response_model=ModelRecordPublic,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a model by name",
)
async def get_model(name: str, request: Request) -> ModelRecordPublic:
    """
    Return the ModelRecord identified by *name* (api_key masked).

    Raises HTTP 422 if *name* contains characters outside ``[a-zA-Z0-9._:-]``.
    Raises HTTP 404 if no model with that name is registered.
    """
    record = _get_record_or_404(name, request)
    return ModelRecordPublic.from_record(record)


# ---------------------------------------------------------------------------
# GET /models/{name}/secret  — internal only: returns the raw api_key
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/secret",
    status_code=status.HTTP_200_OK,
    summary="Retrieve a model's raw provider API key (internal, authed)",
)
async def get_model_secret(name: str, request: Request) -> dict:
    """
    Return ``{"api_key": <raw key or null>}`` for *name*.

    Internal use only — called by the Inference Adapter to resolve the
    credential it needs to dispatch to a cloud-backend model. Requires the
    same X-API-Key as the other mutating endpoints (see AuthMiddleware).

    Raises HTTP 422 on an invalid name, HTTP 404 if the model doesn't exist.
    """
    record = _get_record_or_404(name, request)
    return {"api_key": record.api_key}


# ---------------------------------------------------------------------------
# POST /models  — register a new model
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=ModelRecordPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new model",
)
async def create_model(body: ModelRecordCreate, request: Request) -> ModelRecordPublic:
    """
    Register a new model in the store.

    * Pydantic validates the request body; missing/invalid fields → 422.
    * ``registered_at`` is auto-populated with the current UTC timestamp if
      the caller does not provide it.
    * ``api_key`` (cloud backends only) is stored but never echoed back —
      the response is masked like every other endpoint in this router.
    * Raises HTTP 409 if a model with the same ``name`` already exists.
    * Raises HTTP 500 if the atomic write to disk fails.
    """
    # Auto-populate registered_at if the caller did not supply it
    registered_at = body.registered_at or (datetime.utcnow().isoformat() + "Z")

    record = ModelRecord(
        name=body.name,
        version=body.version,
        backend=body.backend,
        endpoint=body.endpoint,
        tasks=body.tasks,
        status=body.status,
        vram_required_gb=body.vram_required_gb,
        max_context_length=body.max_context_length,
        fallback_model=body.fallback_model,
        registered_at=registered_at,
        notes=body.notes,
        api_key=body.api_key,
    )

    storage = request.app.state.storage
    try:
        stored_record = storage.add(record)
    except DuplicateNameError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except PersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return ModelRecordPublic.from_record(stored_record)


# ---------------------------------------------------------------------------
# PATCH /models/{name}/status  — update a model's status
# ---------------------------------------------------------------------------


@router.patch(
    "/{name}/status",
    response_model=ModelRecordPublic,
    status_code=status.HTTP_200_OK,
    summary="Update a model's status",
)
async def update_model_status(
    name: str, body: StatusUpdateRequest, request: Request
) -> ModelRecordPublic:
    """
    Update the ``status`` field of an existing model.

    All other fields are preserved unchanged.

    * Raises HTTP 422 if *name* contains characters outside ``[a-zA-Z0-9._:-]``
      or if ``body.status`` is not a valid ``ModelStatus`` value.
    * Raises HTTP 404 if no model with that name is registered.
    * Raises HTTP 500 if the atomic write to disk fails.
    """
    # Validate name character set (422 on invalid chars)
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid model name '{name}'. "
                "Name must match pattern ^[a-zA-Z0-9._:-]+$"
            ),
        )

    storage = request.app.state.storage
    try:
        updated_record = storage.update_status(name, body.status)
    except ModelNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return ModelRecordPublic.from_record(updated_record)


# ---------------------------------------------------------------------------
# PATCH /models/{name}/api-key  — set/update a cloud model's provider key
# ---------------------------------------------------------------------------


@router.patch(
    "/{name}/api-key",
    response_model=ModelRecordPublic,
    status_code=status.HTTP_200_OK,
    summary="Set or update a model's provider API key",
)
async def update_model_api_key(
    name: str, body: ApiKeyUpdateRequest, request: Request
) -> ModelRecordPublic:
    """
    Update the ``api_key`` field of an existing model (cloud backends).

    The response is masked (``api_key_set: true``) — the raw value is never
    echoed back. All other fields are preserved unchanged.

    * Raises HTTP 422 if *name* contains characters outside ``[a-zA-Z0-9._:-]``
      or if ``api_key`` is empty.
    * Raises HTTP 404 if no model with that name is registered.
    * Raises HTTP 500 if the atomic write to disk fails.
    """
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid model name '{name}'. "
                "Name must match pattern ^[a-zA-Z0-9._:-]+$"
            ),
        )

    storage = request.app.state.storage
    try:
        updated_record = storage.update_api_key(name, body.api_key)
    except ModelNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return ModelRecordPublic.from_record(updated_record)
