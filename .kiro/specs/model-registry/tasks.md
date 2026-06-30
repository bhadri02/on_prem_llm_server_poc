# Implementation Plan: Model Registry

## Overview

This plan covers all implementation tasks for the Model Registry — a lightweight FastAPI service that centralises LLM model metadata for the Enterprise On-Prem LLM Platform POC. The service stores model records in a JSON file on a PersistentVolume, exposes a REST API for the Intelligent Router and platform operators, and is deployed via a Helm chart.

Tasks follow the module structure defined in design.md. The core implementation chain (Tasks 1–10) must be completed in order; Tasks 11–13 (Dockerfile, seed data, Helm chart) and Tasks 14–15 (tests) depend on the full application being wired in Task 10 but are otherwise independent of each other.

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2"] },
    { "wave": 3, "tasks": ["3"] },
    { "wave": 4, "tasks": ["4"] },
    { "wave": 5, "tasks": ["5"] },
    { "wave": 6, "tasks": ["6", "7", "9"] },
    { "wave": 7, "tasks": ["8"] },
    { "wave": 8, "tasks": ["10"] },
    { "wave": 9, "tasks": ["11", "12", "13", "14", "15"] }
  ]
}
```

> Wave 6 tasks (Auth Middleware, Logging Middleware, Health Router) all depend on Task 5 (Storage Layer) and can be implemented in parallel.
> Wave 9 tasks (Dockerfile, Seed Data, Helm Chart, Unit Tests, PBT) all depend on Task 10 (App Factory) and can be implemented in parallel.

---

## Tasks

- [x] 1. Project scaffolding — create `model_registry/` package tree
  - Create the directory layout as defined in design.md §Components and Interfaces:
    - `model_registry/__init__.py`
    - `model_registry/main.py` (empty stub)
    - `model_registry/config.py` (empty stub)
    - `model_registry/exceptions.py` (empty stub)
    - `model_registry/storage/__init__.py`
    - `model_registry/storage/json_file_manager.py` (empty stub)
    - `model_registry/routers/__init__.py`
    - `model_registry/routers/models.py` (empty stub)
    - `model_registry/routers/health.py` (empty stub)
    - `model_registry/schemas/__init__.py`
    - `model_registry/schemas/model.py` (empty stub)
    - `model_registry/middleware/__init__.py`
    - `model_registry/middleware/auth.py` (empty stub)
    - `model_registry/middleware/logging.py` (empty stub)
  - Create `requirements.txt` (or `pyproject.toml`) pinning exact versions:
    - `fastapi==0.115.0`
    - `uvicorn[standard]==0.30.6`
    - `pydantic==2.7.4`
    - `pydantic-settings==2.3.4`
    - `httpx==0.27.0` (for testing)
    - `pytest==8.2.2`
    - `pytest-anyio==0.0.0` (anyio backend for async tests)
    - `anyio==4.4.0`
    - `hypothesis==6.111.2`
  - Create `.dockerignore`, `.gitignore` appropriate for a Python project
  - **Validates: Requirements 10.1** (establishes the package structure underpinning all chart deliverables)

- [x] 2. Configuration module (`config.py`)
  - Implement `Settings` class using `pydantic-settings` `BaseSettings`:
    - `storage_path: str` — read from `STORAGE_PATH` env var, default `"/data/models.json"`
    - `log_level: str` — read from `LOG_LEVEL` env var, default `"INFO"`
    - `registry_api_key: str` — read from `REGISTRY_API_KEY` env var, default `""`
  - Expose a `get_settings()` cached function (use `@lru_cache`) so the app shares one instance
  - **Validates: Requirements 8.1, 8.2, 9.5, 10.5**

- [x] 3. Pydantic schemas (`schemas/model.py`)
  - Implement the following exactly as specified in design.md §Data Models:
    - `ModelStatus` (str enum: `active`, `staging`, `retired`)
    - `TaskType` (str enum: `chat`, `code`, `reasoning`, `summarization`, `translation`, `vision`, `embeddings`)
    - `ModelRecordCreate` — all required and optional fields, `ConfigDict(extra="forbid")`, `name` field with `pattern=r'^[a-zA-Z0-9._-]+$'`, `tasks` field with `min_length=1`
    - `ModelRecord` — extends `ModelRecordCreate`, `registered_at: str` always present
    - `StatusUpdateRequest` — `status: ModelStatus`, `ConfigDict(extra="forbid")`
    - `HealthResponse` — `status: str`, `storage: str | None = None`
  - **Validates: Requirements 1.6, 1.7, 1.8, 1.9, 3.4**

- [x] 4. Custom exceptions (`exceptions.py`)
  - Implement:
    - `DuplicateNameError(Exception)` — stores `name: str`; message: `"Model with name '{name}' already exists."`
    - `ModelNotFoundError(Exception)` — stores `name: str`; message: `"Model '{name}' not found."`
    - `PersistenceError(Exception)` — stores `message: str` and optional `model_name: str | None`; message: `"Storage write failed. {message}"`
  - **Validates: Requirements 1.10, 1.12, 4.3, 5.2, 12.2**

- [x] 5. Storage layer (`storage/json_file_manager.py`)
  - Implement `JsonFileManager` class with the full interface from design.md §Storage Layer Design:
    - `__init__(self, storage_path: str)` — stores path, initialises `_records: dict[str, ModelRecord] = {}`, `_storage_ok: bool = False`
    - `load(self) -> None` — startup logic:
      - If file missing: create parent dirs, write `"[]"`, set `_records = {}`, set `_storage_ok = True`
      - If file readable: parse JSON, validate each dict as `ModelRecord`, build `_records` dict keyed by name, set `_storage_ok = True`
      - If I/O error or JSON parse error: attempt to overwrite with `"[]"`; if that also fails, log structured error then `sys.exit(1)`; if overwrite succeeds, set `_records = {}`
    - `get_all(self) -> list[ModelRecord]` — returns `list(self._records.values())`; never raises
    - `get_by_name(self, name: str) -> ModelRecord | None` — returns `self._records.get(name)`; never raises
    - `get_by_task(self, task_type: TaskType) -> list[ModelRecord]` — returns records where `task_type in record.tasks AND record.status == ModelStatus.active`; never raises
    - `add(self, record: ModelRecord) -> ModelRecord` — checks uniqueness (raises `DuplicateNameError`), auto-populates `registered_at` if absent (UTC ISO-8601), updates `_records`, calls `_persist()` (raises `PersistenceError` on failure with rollback)
    - `update_status(self, name: str, status: ModelStatus) -> ModelRecord` — raises `ModelNotFoundError` if absent, updates status in memory, calls `_persist()` (raises `PersistenceError` on failure with rollback)
    - `storage_reachable(self) -> bool` — returns `os.path.exists(self._storage_path) and os.access(self._storage_path, os.R_OK)`
    - `_persist(self) -> None` — atomic write as specified in design.md §Atomic Write:
      1. Serialise `_records.values()` to JSON (indent=2, ensure_ascii=False)
      2. Compute temp path as `storage_path + ".tmp"`
      3. Write bytes to temp path
      4. Call `os.replace(tmp_path, storage_path)`
      5. On `OSError`: attempt silent `os.unlink(tmp_path)`, log structured error, raise `PersistenceError`; do NOT update in-memory state (the caller must roll back before calling)
  - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.11, 12.1, 12.2, 12.3**

- [x] 6. Auth middleware (`middleware/auth.py`)
  - Implement `AuthMiddleware(BaseHTTPMiddleware)` as specified in design.md §Authentication Middleware Design:
    - `_requires_auth(method: str, path: str) -> bool` helper:
      - Returns `True` for `POST /models`
      - Returns `True` for `PATCH` where path matches `^/models/[^/]+/status$`
      - Returns `False` for all other combinations
    - `dispatch` method:
      - If `_requires_auth` returns `False`: pass through with `await call_next(request)`
      - If `_requires_auth` returns `True` and `settings.registry_api_key` is empty/unset: pass through (POC convenience mode)
      - If `_requires_auth` returns `True` and key is set: extract `X-API-Key` header, use `hmac.compare_digest` to compare against `settings.registry_api_key`; on mismatch return `JSONResponse(status_code=401, content={"detail": "Invalid or missing X-API-Key header."})`
  - Import and use the `get_settings()` function from `config.py`
  - **Validates: Requirements 4.6, 5.4, 8.3, 8.4, 8.5, 8.6**

- [x] 7. Logging middleware (`middleware/logging.py`)
  - Implement `LoggingMiddleware(BaseHTTPMiddleware)` as specified in design.md §Logging Middleware Design:
    - Capture `start = time.monotonic()` before `await call_next(request)`
    - Compute `latency_ms = (time.monotonic() - start) * 1000`
    - Determine `level = "ERROR" if response.status_code >= 500 else "INFO"`
    - Check `LOG_LEVEL` from settings; suppress the entry if the effective level is higher than `INFO` (i.e. if `log_level` is `WARNING`/`ERROR`, suppress INFO entries; always emit ERROR)
    - Emit exactly one JSON line to stdout via `print(json.dumps(entry), flush=True)` containing: `timestamp` (ISO-8601 UTC + "Z"), `level`, `method`, `path`, `status_code`, `latency_ms` (rounded to 2 dp)
    - NEVER read or log the `X-API-Key` header value
  - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6**

- [x] 8. Models router (`routers/models.py`)
  - Create an `APIRouter` and implement all 5 endpoints. Route ordering is critical — declare `GET /by-task/{task_type}` before `GET /{name}`:
    - `GET /models` → call `storage.get_all()`, return `list[ModelRecord]` with HTTP 200
    - `GET /models/by-task/{task_type}` → validate `task_type` against `TaskType` enum (422 on invalid); call `storage.get_by_task(task_type)`, return `list[ModelRecord]` with HTTP 200 (empty list is valid)
    - `GET /models/{name}` → validate `name` against pattern `^[a-zA-Z0-9._-]+$` (422 on invalid chars); call `storage.get_by_name(name)`, return `ModelRecord` (200) or raise `ModelNotFoundError` (404)
    - `POST /models` → accept `ModelRecordCreate` body; build `ModelRecord`; call `storage.add(record)`; return created record with HTTP 201; handle `DuplicateNameError` → 409; handle `PersistenceError` → 500
    - `PATCH /models/{name}/status` → accept `StatusUpdateRequest` body; call `storage.update_status(name, body.status)`; return updated `ModelRecord` with HTTP 200; handle `ModelNotFoundError` → 404; handle `PersistenceError` → 500
  - Register exception handlers on the router (or app) for `DuplicateNameError`, `ModelNotFoundError`, `PersistenceError` translating to 409, 404, 500 respectively with `{"detail": "..."}` bodies
  - Inject `storage` via `request.app.state.storage` (set during lifespan)
  - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 11.5, 11.6**

- [x] 9. Health router (`routers/health.py`)
  - Implement `GET /health` endpoint:
    - While startup is incomplete (`_ready == False`): return HTTP 503 `{"status": "starting"}`
    - After startup (`_ready == True`) and `storage.storage_reachable()` is True: return HTTP 200 `{"status": "ok", "storage": "reachable"}`
    - After startup (`_ready == True`) and `storage.storage_reachable()` is False: return HTTP 200 `{"status": "degraded", "storage": "unreachable"}`
  - Expose the `_ready` flag as a module-level variable (set to `True` by the app lifespan after `storage.load()` completes)
  - Do not require `X-API-Key` on this endpoint (no auth check)
  - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

- [x] 10. App factory (`main.py`)
  - Implement the `lifespan` async context manager:
    - On startup: call `get_settings()`; if `settings.registry_api_key` is empty, emit structured warning JSON to stdout; instantiate `JsonFileManager(settings.storage_path)` and call `storage.load()`; set `app.state.storage = storage`; set `_ready = True` in the health router module
    - On shutdown: set `_ready = False`
  - Create the `FastAPI` app with `title="Model Registry"` and `lifespan=lifespan`
  - Add middleware in order (outermost first): `app.add_middleware(LoggingMiddleware)` then `app.add_middleware(AuthMiddleware)`
  - Register exception handlers for `DuplicateNameError` (409), `ModelNotFoundError` (404), `PersistenceError` (500)
  - Include routers: `app.include_router(health_router)`, `app.include_router(models_router)`
  - Entrypoint: `if __name__ == "__main__": uvicorn.run("model_registry.main:app", host="0.0.0.0", port=5000)`
  - **Validates: Requirements 1.2, 7.2, 8.1, 8.2**

- [x] 11. Dockerfile
  - Create a production-ready `Dockerfile` at the repository root (or `model_registry/Dockerfile`):
    - Base image: `python:3.12-slim`
    - Set `WORKDIR /app`
    - Copy `requirements.txt` and run `pip install --no-cache-dir -r requirements.txt`
    - Copy the `model_registry/` package
    - Expose port `5000`
    - Set `ENV STORAGE_PATH=/data/models.json` and `ENV LOG_LEVEL=INFO` as defaults
    - `CMD ["uvicorn", "model_registry.main:app", "--host", "0.0.0.0", "--port", "5000"]`
  - Create `.dockerignore` excluding `__pycache__`, `*.pyc`, `.git`, `tests/`, `*.md`, `.kiro/`
  - **Validates: Requirements 10.1** (containerisation prerequisite for Helm deployment)

- [x] 12. Seed data (`models.json`)
  - Create `seed/models.json` with the three POC seed records from design.md:
    - `llama3-8b`: tasks `["chat","summarization","reasoning"]`, status `active`, vram 6, context 8192, fallback `mistral-7b`, notes `"POC primary model"`, registered_at `"2026-06-01T00:00:00Z"`
    - `mistral-7b`: tasks `["chat","summarization","translation"]`, status `active`, vram 4.5, context 8192, fallback `null`, notes `"POC secondary model"`, registered_at `"2026-06-01T00:00:00Z"`
    - `deepseek-coder`: tasks `["code"]`, status `active`, vram 5, context 16384, fallback `"llama3-8b"`, notes `"POC code model"`, registered_at `"2026-06-01T00:00:00Z"`
  - All three records use `backend: "ollama"` and `endpoint: "http://inference-ollama:11434"`
  - Document in a comment (or README note) that this file is copied to the PVC at first deployment
  - **Validates: Requirements 2.1, 11.1** (enables Router startup poll smoke test)

- [x] 13. Helm chart (`llm-platform/charts/model-registry/`)
  - Create all files specified in design.md §Helm Chart Structure:
    - `Chart.yaml` — `apiVersion: v2`, `name: model-registry`, `version: 0.1.0`, `appVersion: "0.1.0"`, description as per design
    - `values.yaml` — all fields from design.md §values.yaml: `replicaCount: 1`, image block, service block (ClusterIP port 5000), env block (LOG_LEVEL, STORAGE_PATH), `apiKeySecret` block, persistence block (enabled: true, 1Gi, ReadWriteOnce, storageClass: ""), resources block (requests cpu 100m / memory 128Mi; limits cpu 300m / memory 256Mi), `autoscaling.enabled: false`, liveness/readiness probes targeting `GET /health` on port 5000 (initialDelaySeconds 10, periodSeconds 15, timeoutSeconds 2, failureThreshold 3), `vault.enabled: false`
    - `templates/_helpers.tpl` — define `model-registry.fullname`, `model-registry.labels`, `model-registry.selectorLabels` helpers following standard Helm conventions
    - `templates/deployment.yaml` — single container, env vars from values (LOG_LEVEL, STORAGE_PATH) plus `REGISTRY_API_KEY` from `secretKeyRef` pointing to `apiKeySecret.name` / `apiKeySecret.key`, volumeMount at `/data`, liveness/readiness from values, resources from values
    - `templates/service.yaml` — ClusterIP, port 5000 → containerPort 5000, named `http`
    - `templates/pvc.yaml` — ReadWriteOnce, 1Gi, conditional storageClassName, name uses `model-registry.fullname` + `-data`
    - `templates/networkpolicy.yaml` — allow ingress from namespace `llm-platform` only on port 5000; deny all other ingress
    - `templates/servicemonitor.yaml` — Prometheus ServiceMonitor scraping the `http` port at `/health` every 30s
    - `README.md` — brief description, values reference table, secret creation command example
  - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7**

- [x] 14. Unit and example tests (`tests/`)
  - Create `tests/__init__.py`, `tests/conftest.py` with shared fixtures:
    - `settings_override` fixture that sets `STORAGE_PATH` to a `tmp_path` file and `REGISTRY_API_KEY` to a fixed test key
    - `async_client` fixture using `httpx.AsyncClient` with `ASGITransport` wrapping the FastAPI app
  - Create `tests/test_storage.py` — unit tests for `JsonFileManager`:
    - Startup with missing file creates empty store and empty JSON file
    - Startup with valid JSON file loads records correctly
    - Startup with malformed JSON attempts recovery (writes `[]`) and continues
    - `add()` persists to disk and round-trips correctly
    - `add()` raises `DuplicateNameError` on duplicate name
    - `update_status()` raises `ModelNotFoundError` on unknown name
    - `_persist()` failure raises `PersistenceError` and leaves in-memory state rolled back
  - Create `tests/test_health.py` — example tests for health endpoint:
    - Returns 503 `{"status":"starting"}` before `_ready = True`
    - Returns 200 `{"status":"ok","storage":"reachable"}` after successful startup
    - Returns 200 `{"status":"degraded","storage":"unreachable"}` when storage file is deleted post-startup
    - Does not require `X-API-Key`
  - Create `tests/test_models.py` — example tests for models router:
    - `GET /models` returns `[]` on empty store; returns all records after registrations
    - `GET /models/{name}` returns 200 with correct record; returns 404 for unknown name; returns 404 for wrong-case name; returns 422 for name with invalid chars
    - `POST /models` returns 201 with record; returns 409 on duplicate name; returns 422 on missing required field; returns 401 when `X-API-Key` is absent or wrong (checked before validation)
    - `PATCH /models/{name}/status` returns 200 with updated record; only `status` field changes; returns 404 for unknown name; returns 401 when key is absent or wrong; returns 404 for non-existent model only after key is validated (Req 5.6)
    - `GET /models/by-task/{task_type}` returns only active records matching the task; returns `[]` when no active match; returns 422 for invalid task_type; `by-task` is not treated as a model name
  - Create `tests/test_logging.py` — example tests for logging middleware:
    - Each request emits exactly one JSON line to stdout
    - Emitted entry contains `timestamp`, `level`, `method`, `path`, `status_code`, `latency_ms`
    - `level` is `"INFO"` for 2xx; `"ERROR"` for 5xx
    - `X-API-Key` value does not appear in the emitted JSON
  - **Validates: Requirements 1.3, 1.4, 1.12, 5.6, 7.1–7.4, 8.4, 9.1–9.6, 12.2**

- [ ] 15. Property-based tests (`tests/test_properties.py`) [PBT]
  - Use `hypothesis` with `@given` and `@settings(max_examples=100)` for all tests
  - Implement a reusable `model_record_strategy()` Hypothesis composite strategy that generates valid `ModelRecord`-shaped dicts with random but valid names, versions, backends, endpoints, tasks, statuses, and optional fields
  - Implement exactly the 12 properties from design.md §Correctness Properties, tagged with the format `# Feature: model-registry, Property N: <short name>`:
    - **P1 — Atomic write round-trip**: `@given(records=st.lists(model_record_strategy(), min_size=1, max_size=20))` — write list via `_persist`, reload via a fresh `JsonFileManager.load()`, assert all records equal. **Validates: Requirements 1.1, 12.1, 12.3**
    - **P2 — Required field validation**: `@given(missing_field=st.sampled_from(["name","version","backend","endpoint","tasks","status"]))` — POST with that field removed; assert 422 and the field name appears in the response detail. **Validates: Requirements 1.6, 4.2**
    - **P3 — Status and task enum enforcement**: `@given(bad_status=st.text().filter(...))` and separately `@given(bad_task=st.text().filter(...))` — assert POST returns 422; also assert PATCH with bad status returns 422. **Validates: Requirements 1.8, 1.9, 4.4, 4.5, 5.3**
    - **P4 — Name uniqueness on registration**: `@given(record=model_record_strategy())` — register a record, attempt to register again with the same name and any other field values; assert 409 and store count unchanged. **Validates: Requirements 1.10, 4.3**
    - **P5 — registered_at auto-population**: `@given(record=model_record_strategy())` — POST without `registered_at`; assert response contains a valid ISO-8601 UTC string in `registered_at`. **Validates: Requirements 1.11**
    - **P6 — GET /models completeness and null serialisation**: `@given(records=st.lists(model_record_strategy(), min_size=0, max_size=10))` — register N records (some with optional fields omitted); GET /models; assert response length == N; assert all required fields present and non-null; assert each absent optional field is present as `null`. **Validates: Requirements 2.1, 2.2, 2.3**
    - **P7 — GET /models/{name} case-sensitive exact match**: `@given(record=model_record_strategy())` — register record; GET with exact name → 200; GET with `.upper()` or `.lower()` variant → 404 (when different from original). **Validates: Requirements 3.1, 3.3**
    - **P8 — Invalid name characters → 422**: `@given(bad_name=st.text(min_size=1).filter(lambda s: re.search(r'[^a-zA-Z0-9._-]', s)))` — GET /models/{bad_name}; assert 422. **Validates: Requirements 3.4**
    - **P9 — Mutating endpoints require valid API key**: `@given(bad_key=st.text().filter(lambda k: k != TEST_KEY), record=model_record_strategy())` — POST and PATCH with bad key; assert 401; assert response body does not contain `TEST_KEY` value; assert store unchanged. **Validates: Requirements 4.6, 5.4, 8.3, 8.6**
    - **P10 — PATCH status preserves all other fields**: `@given(record=model_record_strategy(), new_status=st.sampled_from(list(ModelStatus)))` — register record; PATCH its status; assert all fields except `status` are identical to the original. **Validates: Requirements 5.5**
    - **P11 — by-task returns only active models for the queried task**: `@given(models=st.lists(model_record_strategy(), min_size=0, max_size=20), task=st.sampled_from(list(TaskType)))` — register models; GET /models/by-task/{task}; assert every returned record has `status == "active"` AND `task.value in record["tasks"]`; assert no staging/retired records appear. **Validates: Requirements 6.1, 6.2**
    - **P12 — Structured log completeness and key non-disclosure**: `@given(record=model_record_strategy())` — capture stdout during a request; assert exactly one JSON line emitted; assert required fields present; assert `level` is `"INFO"` for 2xx and `"ERROR"` for 5xx; assert `REGISTRY_API_KEY` value not in the emitted string. **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.6**
  - **Validates: All 12 correctness properties from design.md**


## Notes

- **POC constraints apply throughout**: no MLflow, no Vault, no HPA, no Istio mTLS, no Argo Rollouts. All deferred to Phase 2 per the platform master contract.
- **Route ordering in Task 8 is critical**: `GET /models/by-task/{task_type}` must be registered before `GET /models/{name}` in the FastAPI router to prevent `by-task` being matched as a model name path parameter.
- **Atomic writes (Task 5)**: `os.replace` is POSIX-atomic only when source and destination are on the same filesystem mount. The temp file must be written to `/data/models.json.tmp` (same PVC mount as `models.json`) to guarantee this.
- **Auth middleware ordering (Task 10)**: `LoggingMiddleware` is added first (outermost) so it wraps `AuthMiddleware` — the log entry captures the true final status code (including 401s from auth).
- **`_ready` flag bridging (Tasks 9 and 10)**: The health router module exposes `_ready` as a module-level bool. `main.py` imports and sets it directly after `storage.load()` completes. This ensures `/health` returns 503 during the entire startup window.
- **Testing approach**: Property-based tests (Task 15) use `hypothesis` in-process without mocking — they call `JsonFileManager` and `httpx.AsyncClient` directly against the real app. Do not use mocks to make tests pass.
- **Seed data (Task 12)** is a static file placed under `seed/`; it is not auto-loaded by the application. Operators copy it to the PVC as a first-deployment step (documented in the Helm chart README).
- **`hpa.yaml` is intentionally omitted** from the Helm chart (Task 13) per POC scope — `autoscaling.enabled: false` and `replicaCount: 1`.
- **Property 12 (log testing)**: Capturing stdout in a `pytest` context requires patching `sys.stdout` or using `capsys`. Use `capsys.readouterr()` after each async request to inspect emitted log lines.
