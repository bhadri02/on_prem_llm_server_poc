# Implementation Plan: Inference Layer

## Overview

This plan covers all implementation tasks for the Inference Layer (Layer 5) — a two-component deployment consisting of the **Inference Adapter** (FastAPI, port 8087) and **Ollama** (inference engine, port 11434), packaged together in a single Helm chart at `llm-platform/charts/inference-ollama/`.

Tasks follow the module structure defined in `design.md`. Foundation pieces (package scaffold, config, schemas, exceptions) are sequenced first. Core services (`OllamaClient`, `IMFMapper`) come next, followed by the routers, middleware, and application factory. Prometheus metrics, the Dockerfile, and Helm chart are parallel deliverables once the factory is complete. Tests are last, written against all layers once the application is fully wired.

All HTTP calls to Ollama in unit and property tests are intercepted using `respx` (async `httpx` mock). No live Ollama instance is required except for the optional integration test.

## Tasks

- [x] 1. Scaffold `inference_adapter` package and add dependencies
  - Create `inference_adapter/__init__.py` (empty) to establish the package.
  - Create `inference_adapter/routers/__init__.py`, `inference_adapter/schemas/__init__.py`, `inference_adapter/services/__init__.py`, `inference_adapter/middleware/__init__.py` (all empty).
  - Append the following pinned packages to the root `requirements.txt` (do not remove existing entries):
    - `httpx==0.27.2`
    - `respx==0.21.1`
    - `prometheus-client==0.21.1`
  - Confirm `hypothesis==6.111.2`, `pytest-asyncio==0.24.0`, and `fastapi` are already present; add if missing.
  - Create `tests/inference_adapter/__init__.py` (empty).
  - Verify: `pip install -r requirements.txt --dry-run` exits without error.
  - _Requirements: 13.3_

- [x] 2. Implement `inference_adapter/config.py` — Pydantic BaseSettings
  - [x] 2.1 Implement `Settings(BaseSettings)` with all fields, defaults, validators, and `@lru_cache get_settings()`
    - All fields read from env vars (no hardcoded values):
      - `ollama_base_url: str = "http://inference-ollama:11434"` — non-empty; fail-fast if malformed URL
      - `default_model: str = "llama3.2:3b"`
      - `default_max_tokens: int = Field(2048, gt=0)` — must be `<= max_tokens_limit` at startup
      - `max_tokens_limit: int = Field(4096, gt=0)`
      - `default_temperature: float = Field(0.7, ge=0.0, le=2.0)`
      - `ollama_timeout_seconds: int = Field(120, ge=1, le=600)` — outside range → startup error
      - `log_level: str = "INFO"` — invalid → fall back to `"INFO"` at runtime
      - `port: int = Field(8087, ge=1, le=65535)` — outside range → startup error
      - `metrics_port: int = Field(9090, ge=1, le=65535)` — outside range → startup error
    - Implement cross-field validator: `default_max_tokens <= max_tokens_limit`; violation → startup error
    - Set `model_config = {"env_prefix": "", "case_sensitive": False}`
    - Expose `@lru_cache get_settings() -> Settings`
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 13.4, 13.5_

- [x] 3. Implement `inference_adapter/schemas/imf.py` — IMF Pydantic models
  - [x] 3.1 Define all IMF request and response Pydantic models
    - `IMFMessage(BaseModel)`: `role: str`, `content: str`
    - `IMFUsage(BaseModel)`: `prompt_tokens: int = 0`, `completion_tokens: int = 0`, `total_tokens: int = 0`
    - `IMFResponse(BaseModel)`: `content: str | None = None`, `finish_reason: str | None = None`, `usage: IMFUsage | None = None`
    - `IMFGovernance(BaseModel)`: `pii_fields_detected: list[str] = []`; all other governance fields optional
    - `IMFRouting(BaseModel)`: `selected_model: str | None = None`, `routing_mode: str | None = None`, `fallback_level: int = 0`
    - `IMFUser(BaseModel)`: `user_id: str | None = None`, `department: str | None = None`, `roles: list[str] = []`; other fields optional
    - `IMFRequest(BaseModel)`: `model: str | None = None`, `task_type: str | None = None`, `messages: list[IMFMessage] = []`, `stream: bool = False`, `max_tokens: int | None = None`, `temperature: float | None = None`
    - `IMFCache(BaseModel)`: `lookup_hit: bool = False`, `cache_key: str | None = None`
    - `IMFDocument(BaseModel)`: `request_id: str | None = None`, `trace_id: str | None = None`, `span_id: str | None = None`, `timestamp_utc: str | None = None`, `user: IMFUser = Field(default_factory=IMFUser)`, `request: IMFRequest = Field(default_factory=IMFRequest)`, `governance: IMFGovernance = Field(default_factory=IMFGovernance)`, `routing: IMFRouting = Field(default_factory=IMFRouting)`, `cache: IMFCache | None = None`, `response: IMFResponse | None = None`, `metadata: dict = Field(default_factory=dict)`, `extensions: dict = Field(default_factory=dict)`
    - _Requirements: 1.1, 1.2, 2.1, 2.5, 2.6, 3.4, 7.1_

- [x] 4. Implement `inference_adapter/services/ollama_client.py` — `OllamaClient`
  - [x] 4.1 Implement `OllamaClient` wrapping `httpx.AsyncClient` with typed error hierarchy
    - Define exception classes in this module: `OllamaError` (base), `OllamaTimeoutError`, `OllamaConnectionError`, `OllamaBackendError(status_code: int)`, `OllamaRequestError(status_code: int)`, `OllamaInvalidResponseError`
    - `__init__(self, base_url: str, timeout: float)` — stores params; creates `httpx.AsyncClient` with `httpx.Timeout(timeout)` for both connect and read phases; client stored as `self._client`
    - `async chat(self, payload: dict) -> dict` — forces `payload["stream"] = False` before sending; `POST {base_url}/api/chat`; maps `httpx.TimeoutException` → `OllamaTimeoutError`; maps `httpx.ConnectError` / transport errors → `OllamaConnectionError`; maps HTTP 5xx → `OllamaBackendError(status_code)`; maps HTTP 4xx → `OllamaRequestError(status_code)`; maps JSON parse failure → `OllamaInvalidResponseError`; returns parsed dict on HTTP 200
    - `async list_models(self) -> list[str]` — `GET {base_url}/api/tags`; parses `response["models"]` array extracting `"name"` field from each entry; raises `OllamaTimeoutError` / `OllamaConnectionError` on failure
    - `async close(self) -> None` — awaits `self._client.aclose()`
    - _Requirements: 13.5, 13.6, 9.1, 9.2, 9.3, 9.4_

- [x] 5. Implement `inference_adapter/services/imf_mapper.py` — `IMFMapper`
  - [x] 5.1 Implement `IMFMapper.to_ollama_request()` — IMF → Ollama wire format translation
    - `model` ← `routing.selected_model` (never `request.model`)
    - `messages` ← `request.messages` array, preserving only `role` and `content` per entry
    - `stream` ← always `False`
    - `options.num_predict` resolution:
      - `request.max_tokens` is `None` / `0` / absent → `settings.default_max_tokens`
      - `request.max_tokens > settings.max_tokens_limit` → clamp to `settings.max_tokens_limit`, emit structured warning `{"event": "max_tokens_clamped", "requested": <value>, "clamped_to": <limit>}`
      - Otherwise → `request.max_tokens` as-is
    - `options.temperature` resolution: `request.temperature` when present and non-null; else `settings.default_temperature`
    - Output body contains **only** `model`, `messages`, `stream`, `options` (no IMF governance/routing/user fields)
    - Method is `@staticmethod`; pure function with no side-effects; identical inputs → identical outputs
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 7.6, 7.7, 7.8, 1.2, 1.3, 1.4, 1.5_

  - [x] 5.2 Implement `IMFMapper.to_imf_response()`, `resolve_finish_reason()`, and `resolve_token_counts()`
    - `resolve_finish_reason(done_reason: str | None) -> str | None`: returns `"stop"` iff `done_reason == "stop"`, `"length"` iff `done_reason == "length"`, `None` otherwise
    - `resolve_token_counts(prompt_eval_count: int | None, eval_count: int | None) -> tuple[int, int, int]`: nulls → 0; returns `(prompt_tokens, completion_tokens, total_tokens)` where `total = prompt + completion`; all values are non-negative integers
    - `to_imf_response(imf_in, ollama_resp, wall_clock_ms)`:
      - Raises `OllamaInvalidResponseError` if `message` or `message.content` is absent
      - Sets `response.content`, `response.finish_reason`, `response.usage.*`
      - Sets `metadata.inference_backend = "ollama"`
      - Sets `metadata.inference_latency_ms = floor(total_duration / 1_000_000)` when `total_duration > 0`; else `wall_clock_ms`
      - Sets `metadata.model_name = routing.selected_model` (or `null` if absent)
      - Sets **no other** `metadata` fields
      - All IMF fields outside `response`, `metadata`, `extensions` are preserved byte-identical
      - All methods are `@staticmethod`; deterministic and side-effect-free
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 6. Implement `inference_adapter/routers/health.py` — Health Router
  - [x] 6.1 Implement `GET /health` with the four-state state machine
    - Declare module-level `_startup_complete: bool = False` (set by lifespan in Task 8)
    - State machine (deterministic function of startup state + Ollama check result):
      - `_startup_complete == False` → HTTP 503 `{"status": "starting"}`
      - `_startup_complete == True`, Ollama `/api/tags` fails/times out → HTTP 503 `{"status": "unavailable", "reason": "ollama_unreachable"}`
      - `_startup_complete == True`, Ollama reachable but `DEFAULT_MODEL` absent from model list → HTTP 503 `{"status": "unavailable", "reason": "model_not_loaded", "model": "<DEFAULT_MODEL>"}`
      - `_startup_complete == True`, Ollama reachable and `DEFAULT_MODEL` present → HTTP 200 `{"status": "ok", "backend": "ollama", "model": "<DEFAULT_MODEL>"}`
    - Issues a live `GET /api/tags` on every probe call with a hard 5-second timeout; does **not** cache the result
    - Retrieve `OllamaClient` from `request.app.state.ollama_client`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 7. Implement `inference_adapter/routers/infer.py` — Infer Router
  - [x] 7.1 Implement `POST /infer` endpoint with full validation and Ollama dispatch
    - Validate `routing.selected_model` present and non-null → 422 if absent
    - Validate `request.messages` is present and non-empty → 422 `{"event": "empty_messages", "request_id": "..."}` if empty or absent
    - Check `routing.selected_model` against `request.app.state.ollama_models` → 422 `{"event": "model_not_loaded", "model": "...", "request_id": "..."}` if not present
    - If `request.stream` is `true`: emit `{"event": "streaming_not_supported", "request_id": "..."}` warning log; proceed with `stream=false`
    - Emit `inference_start` log entry before calling Ollama: `{"event": "inference_start", "request_id": ..., "model": ..., "timestamp_utc": "<ISO-8601 UTC>"}`
    - Call `IMFMapper.to_ollama_request()` → `OllamaClient.chat()` → `IMFMapper.to_imf_response()`
    - On success: emit `inference_complete` log `{"event": "inference_complete", "request_id": ..., "model": ..., "prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ..., "latency_ms": ...}`
    - On any Ollama error: emit `inference_error` log `{"event": "inference_error", "request_id": ..., "model": ..., "error_code": ..., "latency_ms": ...}`; return structured HTTP error per design error table
    - On unhandled exception: catch and return HTTP 500 `{"event": "internal_error", "request_id": "..."}`
    - Update all applicable Prometheus counters and histograms **before** returning the response
    - Return HTTP 200 with fully populated IMF document on success
    - _Requirements: 1.1, 1.6, 1.7, 1.8, 1.9, 1.10, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 10.1, 10.2, 10.3, 11.2, 11.3, 11.4, 11.5, 14.1, 14.2, 14.3, 14.4_

- [x] 8. Implement `inference_adapter/middleware/logging.py` — `LoggingMiddleware`
  - [x] 8.1 Implement `LoggingMiddleware(BaseHTTPMiddleware)` mirroring `cache_service/middleware/logging.py`
    - Read and re-inject raw request body via `request.scope["_body"]` so downstream handlers can still read it
    - `request_id` extraction priority: (1) IMF body `request_id` field → (2) `X-Request-ID` header → (3) `"unknown"`
    - Emit one JSON line per request: `timestamp` (ISO-8601 UTC + "Z"), `level` (`"INFO"` for status < 500; `"ERROR"` for status ≥ 500), `method`, `path`, `status_code`, `latency_ms` (2 dp), `request_id`
    - **PII safety** — never include in any log entry at any log level:
      - Any key whose name matches any value in `governance.pii_fields_detected`
      - The raw string value of any `request.messages[].content` field
    - Respects `LOG_LEVEL` from `get_settings()`; invalid values treated as `"INFO"`
    - Silently discards the entry if stdout write raises any exception
    - _Requirements: 10.4, 10.5, 10.6_

- [x] 9. Implement `inference_adapter/metrics.py` — Prometheus metrics registry
  - [x] 9.1 Register all three Prometheus metrics at module import time
    - `llm_inference_requests_total = Counter("llm_inference_requests_total", "...", ["status", "model", "task_type", "department"])`
      - `status` ∈ `{"success", "error"}`
    - `llm_inference_latency_seconds = Histogram("llm_inference_latency_seconds", "...", ["model", "task_type", "department"], buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0])`
    - `llm_inference_errors_total = Counter("llm_inference_errors_total", "...", ["error_code", "model", "department"])`
      - `error_code` ∈ `{"ollama_unreachable", "ollama_error_response", "ollama_unparseable_body"}`
    - Module has no I/O; safe to import anywhere
    - Infer router (Task 7) imports and updates these metrics
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

- [x] 10. Implement `inference_adapter/main.py` — Application Factory
  - [x] 10.1 Implement `lifespan` context manager, wire middleware and routers, add dev entrypoint
    - Lifespan startup sequence:
      1. Load `settings = get_settings()` — fail fast on invalid `PORT`, `OLLAMA_TIMEOUT_SECONDS`, `DEFAULT_TEMPERATURE`, `DEFAULT_MAX_TOKENS > MAX_TOKENS_LIMIT`
      2. Instantiate `OllamaClient(settings.ollama_base_url, settings.ollama_timeout_seconds)`; store on `app.state.ollama_client`
      3. Attempt `await ollama_client.list_models()` to populate `app.state.ollama_models` (list[str])
         - On failure: log structured JSON warning `{"event": "ollama_unreachable_at_startup", ...}`; set `app.state.ollama_reachable = False`; `app.state.ollama_models = []`; **do NOT exit** (degraded mode)
         - On success: set `app.state.ollama_reachable = True`
      4. Set `health._startup_complete = True` (enables health endpoint to report real state)
      5. Start Prometheus metrics server on `settings.metrics_port` (port 9090) as a background `asyncio` task using `uvicorn.Server` + `make_asgi_app()` — isolated from application port 8087
         - If port 9090 cannot be bound: emit structured JSON error log, fail to start (Requirement 11.6)
      6. `yield` — service is running
    - Lifespan shutdown: `await app.state.ollama_client.close()`; cancel metrics task; set `health._startup_complete = False`
    - Create `app = FastAPI(title="Inference Adapter", version="0.1.0", lifespan=lifespan)`
    - `app.add_middleware(LoggingMiddleware)` before including routers
    - Include `health_router` and `infer_router`
    - `if __name__ == "__main__": uvicorn.run("inference_adapter.main:app", host="0.0.0.0", port=settings.port)`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 11.6_

- [ ] 11. Checkpoint — smoke test the wired application
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Create `inference_adapter/Dockerfile`
  - [x] 12.1 Write the Dockerfile for the Inference Adapter container image
    - Base image: `python:3.12-slim`
    - `WORKDIR /app`
    - Copy `requirements.txt`; run `pip install --no-cache-dir -r requirements.txt`
    - Copy `inference_adapter/` into `/app/inference_adapter/`
    - `EXPOSE 8087 9090`
    - `ENV PORT=8087 METRICS_PORT=9090 LOG_LEVEL=INFO OLLAMA_BASE_URL=http://inference-ollama:11434 DEFAULT_MODEL=llama3.2:3b OLLAMA_TIMEOUT_SECONDS=120 DEFAULT_MAX_TOKENS=2048 MAX_TOKENS_LIMIT=4096 DEFAULT_TEMPERATURE=0.7`
    - `CMD ["uvicorn", "inference_adapter.main:app", "--host", "0.0.0.0", "--port", "8087"]`
    - _Requirements: 12.1, 12.7_

- [x] 13. Create Helm chart `llm-platform/charts/inference-ollama/`
  - [x] 13.1 Create `Chart.yaml` and `values.yaml` with POC defaults
    - **`Chart.yaml`**: `apiVersion: v2`, `name: inference-ollama`, `description: "Inference Layer (Layer 5) — Ollama engine + Inference Adapter for the LLM platform"`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`
    - **`values.yaml`** — POC defaults:
      - `replicaCount: 1`
      - `ollama.image.repository: "ollama/ollama"`, `ollama.image.tag: "latest"`, `ollama.image.pullPolicy: IfNotPresent`
      - `ollama.service.port: 11434`
      - `ollama.resources.requests.cpu: "1"`, `ollama.resources.requests.memory: "8Gi"`, `ollama.resources.limits.cpu: "4"`, `ollama.resources.limits.memory: "16Gi"`
      - `ollama.env.OLLAMA_HOST: "0.0.0.0"`, `ollama.env.OLLAMA_KEEP_ALIVE: "24h"`
      - `adapter.image.repository: "registry.internal/inference-adapter"`, `adapter.image.tag: ""`, `adapter.image.pullPolicy: IfNotPresent`
      - `adapter.service.port: 8087`, `adapter.metricsPort: 9090`
      - `adapter.env.DEFAULT_MODEL: "llama3.2:3b"`, `adapter.env.LOG_LEVEL: "INFO"`, `adapter.env.OLLAMA_BASE_URL: "http://inference-ollama:11434"`, `adapter.env.OLLAMA_TIMEOUT_SECONDS: "120"`, `adapter.env.DEFAULT_MAX_TOKENS: "2048"`, `adapter.env.MAX_TOKENS_LIMIT: "4096"`, `adapter.env.DEFAULT_TEMPERATURE: "0.7"`
      - `models.preload: ["llama3.2:3b"]`
      - `persistence.enabled: true`, `persistence.size: "20Gi"`, `persistence.storageClass: ""`, `persistence.mountPath: "/root/.ollama"`
      - `autoscaling.enabled: false`; `vault.enabled: false`
      - `initJob.enabled: true`, `initJob.image: "curlimages/curl:latest"`, `initJob.pullTimeoutSeconds: 600`
    - _Requirements: 12.1, 12.2, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 13.2 Create `templates/_helpers.tpl`, `templates/deployment.yaml` (Ollama), and `templates/adapter-deployment.yaml` (Inference Adapter)
    - **`_helpers.tpl`**: define `inference-ollama.fullname`, `inference-ollama.labels`, `inference-ollama.selectorLabels`, `inference-ollama.adapterLabels`, `inference-ollama.adapterSelectorLabels` following Helm conventions
    - **`templates/deployment.yaml`** (Ollama pod):
      - `replicaCount: 1`; container port 11434; env vars `OLLAMA_HOST`, `OLLAMA_KEEP_ALIVE` from values
      - Liveness and readiness probes: `GET /api/tags` on port 11434, `initialDelaySeconds: 30`, `periodSeconds: 15`, `timeoutSeconds: 5`, `failureThreshold: 5`
      - PVC volume mount: `persistence.mountPath` (`/root/.ollama`)
      - Resources from `ollama.resources` in values
    - **`templates/adapter-deployment.yaml`** (Inference Adapter pod):
      - Container ports: 8087 (`http`) and 9090 (`metrics`)
      - All `adapter.env.*` values injected as env vars; image tag defaults to `"latest"` when `.Values.adapter.image.tag` is empty
      - Liveness and readiness probes: `GET /health` on port 8087, `initialDelaySeconds: 20`, `periodSeconds: 15`, `timeoutSeconds: 5`, `failureThreshold: 3`
      - Resources: `requests.cpu: "200m"`, `requests.memory: "256Mi"`, `limits.cpu: "1"`, `limits.memory: "512Mi"`
    - _Requirements: 12.1, 12.5, 12.6, 5.8, 12.7_

  - [x] 13.3 Create `templates/service.yaml`, `templates/pvc.yaml`, `templates/networkpolicy.yaml`, `templates/servicemonitor.yaml`, `templates/init-job.yaml`, and `README.md`
    - **`templates/service.yaml`**: two ClusterIP Services — `inference-adapter` (port 8087 named `http`; port 9090 named `metrics`) and `inference-ollama` (port 11434 named `http`)
    - **`templates/pvc.yaml`**: `PersistentVolumeClaim` with `accessMode: ReadWriteOnce`, `storageClass` from values, `storage: persistence.size`; guarded by `persistence.enabled`
    - **`templates/networkpolicy.yaml`**: NetworkPolicy in `llm-platform` namespace — ingress to Adapter pods only from pods with `app.kubernetes.io/name: router`; egress from Adapter pods only to pods with `app.kubernetes.io/name: inference-ollama`
    - **`templates/servicemonitor.yaml`**: `ServiceMonitor` targeting Adapter port `metrics` (9090), `path: /metrics`, `interval: 30s`, `namespaceSelector` restricted to `.Release.Namespace`
    - **`templates/init-job.yaml`**: Kubernetes `Job` (guarded by `initJob.enabled`) that sequentially pulls each model in `models.preload` via `POST http://inference-ollama:11434/api/pull`; per-model timeout `initJob.pullTimeoutSeconds`; exits non-zero on any failed pull with structured JSON log `{"event": "model_pull_failed", "model": ..., "reason": ...}`; exits 0 if `models.preload` is empty
    - **`README.md`**: chart description, values reference table, deploy instructions with `helm upgrade --install`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 5.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 14. Write unit tests in `tests/inference_adapter/`
  - [x] 14.1 Create `conftest.py` with shared fixtures and hypothesis profile
    - Register `hypothesis` `"ci"` profile: `settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])`; `settings.load_profile("ci")`
    - `mock_ollama_client` fixture: returns a mock `OllamaClient` whose `chat()` returns a canned valid Ollama response dict and `list_models()` returns `["llama3.2:3b"]`
    - `app_client` async fixture: builds the real FastAPI app with lifespan stubbed to inject `mock_ollama_client` into `app.state.ollama_client`, set `app.state.ollama_models = ["llama3.2:3b"]`, set `health._startup_complete = True`; yields `httpx.AsyncClient` via `ASGITransport`
    - `valid_imf_doc` fixture: minimal valid `IMFDocument` dict with `routing.selected_model = "llama3.2:3b"` and one non-empty message
    - _Requirements: 13.3_

  - [x] 14.2 Write `tests/inference_adapter/test_config.py`
    - `test_defaults`: all default values present without env vars
    - `test_env_override`: env vars override each field correctly
    - `test_port_out_of_range_raises`: `PORT=0` and `PORT=65536` both raise `ValidationError`
    - `test_timeout_out_of_range_raises`: `OLLAMA_TIMEOUT_SECONDS=0` and `OLLAMA_TIMEOUT_SECONDS=601` both raise
    - `test_invalid_temperature_raises`: `DEFAULT_TEMPERATURE=2.1` raises
    - `test_default_max_tokens_exceeds_limit_raises`: `DEFAULT_MAX_TOKENS=5000`, `MAX_TOKENS_LIMIT=4096` raises
    - `test_invalid_log_level_falls_back_to_info`: invalid `LOG_LEVEL` does not raise at construction
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.7, 13.4_

  - [x] 14.3 Write `tests/inference_adapter/test_ollama_client.py` (using `respx`)
    - `test_chat_success_returns_parsed_json`: mock HTTP 200 response; assert dict returned
    - `test_chat_timeout_raises_ollama_timeout_error`: mock `httpx.TimeoutException`
    - `test_chat_connect_error_raises_ollama_connection_error`: mock `httpx.ConnectError`
    - `test_chat_4xx_raises_ollama_request_error`: mock HTTP 422; assert `OllamaRequestError.status_code == 422`
    - `test_chat_5xx_raises_ollama_backend_error`: mock HTTP 500; assert `OllamaBackendError.status_code == 500`
    - `test_chat_invalid_json_raises_ollama_invalid_response_error`: mock response with non-JSON body
    - `test_chat_forces_stream_false`: capture request body; assert `stream == False` regardless of input
    - `test_list_models_parses_names_correctly`: mock `/api/tags` with model list; assert name strings returned
    - `test_close_closes_client`: call `close()`, assert client is no longer usable
    - _Requirements: 13.5, 13.6, 9.1, 9.2, 9.3, 9.4_

  - [x] 14.4 Write `tests/inference_adapter/test_imf_mapper.py`
    - `test_to_ollama_request_model_from_routing_not_request`: assert `model` == `routing.selected_model`
    - `test_to_ollama_request_null_max_tokens_uses_default`: `max_tokens=None` → `options.num_predict == default_max_tokens`
    - `test_to_ollama_request_zero_max_tokens_uses_default`: `max_tokens=0` → default
    - `test_to_ollama_request_valid_max_tokens_passthrough`: `max_tokens=512` → `512`
    - `test_to_ollama_request_max_tokens_clamped_at_limit`: `max_tokens=9000` → `max_tokens_limit`
    - `test_to_ollama_request_null_temperature_uses_default`: `temperature=None` → `default_temperature`
    - `test_to_ollama_request_stream_always_false`: regardless of IMF `stream` value
    - `test_to_ollama_request_only_four_keys`: output dict has exactly `{"model", "messages", "stream", "options"}`
    - `test_to_imf_response_content_mapped_correctly`
    - `test_to_imf_response_finish_reason_stop`; `test_to_imf_response_finish_reason_length`; `test_to_imf_response_finish_reason_other_maps_null`
    - `test_to_imf_response_missing_message_raises_invalid_response_error`
    - `test_to_imf_response_total_duration_converts_to_ms`: `total_duration=1_500_000_000` → `latency_ms == 1500`
    - `test_to_imf_response_zero_total_duration_uses_wall_clock`
    - `test_to_imf_response_preserves_input_fields_unchanged`
    - `test_resolve_token_counts_nulls_default_to_zero`; `test_resolve_token_counts_total_equals_sum`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.7, 2.8, 3.2, 3.3, 7.1, 7.4, 7.5, 7.6, 7.7, 7.8_

  - [x] 14.5 Write `tests/inference_adapter/test_infer_router.py` (using `app_client` + `respx`)
    - `test_valid_imf_returns_200_with_populated_response`
    - `test_missing_selected_model_returns_422`
    - `test_empty_messages_returns_422_event_empty_messages`
    - `test_model_not_in_list_returns_422_event_model_not_loaded`
    - `test_ollama_timeout_returns_503_event_ollama_unreachable`
    - `test_ollama_connection_error_returns_503`
    - `test_ollama_4xx_returns_422_event_ollama_request_rejected`
    - `test_ollama_5xx_returns_502_event_ollama_backend_error`
    - `test_ollama_invalid_json_returns_502_event_ollama_invalid_response`
    - `test_unhandled_exception_returns_500_event_internal_error`
    - `test_stream_true_logs_warning_and_proceeds_non_streaming`
    - `test_all_error_responses_have_content_type_application_json`
    - `test_all_error_responses_contain_event_and_request_id_keys`
    - `test_error_responses_contain_no_partial_response_block`
    - _Requirements: 1.1, 1.6, 1.7, 1.8, 1.9, 1.10, 9.1, 9.2, 9.3, 9.4, 9.5, 14.2, 14.3, 14.4_

  - [ ]* 14.6 Write `tests/inference_adapter/test_health_router.py` (using `app_client`)
    - `test_starting_state_returns_503_status_starting`: `_startup_complete = False`
    - `test_ollama_reachable_model_present_returns_200_ok`
    - `test_ollama_unreachable_returns_503_ollama_unreachable`
    - `test_ollama_reachable_model_absent_returns_503_model_not_loaded`
    - `test_health_response_body_has_correct_keys_for_each_state`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 14.7 Write `tests/inference_adapter/test_logging_middleware.py` (using `app_client`)
    - `test_log_entry_contains_required_fields`: timestamp, level, method, path, status_code, latency_ms, request_id
    - `test_request_id_from_imf_body`
    - `test_request_id_from_header_fallback`
    - `test_request_id_unknown_fallback`
    - `test_pii_field_names_not_in_log`: field names in `governance.pii_fields_detected` absent from log entry
    - `test_message_content_not_in_log`: raw content strings absent from log entry
    - `test_5xx_response_emits_error_level`
    - `test_2xx_response_emits_info_level`
    - _Requirements: 10.4, 10.5, 10.6_

- [ ] 15. Checkpoint — ensure all unit tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Write property-based tests in `tests/inference_adapter/test_properties.py` [PBT]
  - Use `@given` + `@settings(max_examples=100, deadline=500)` for all properties.
  - All Ollama HTTP calls intercepted via `respx`. No live Ollama instance required.
  - Use `@settings(suppress_health_check=[HealthCheck.too_slow])` where strategies are complex.
  - Each test includes a docstring with `# Validates: Requirements X.Y`.

  - [ ]* 16.1 Write property test for Property 1: IMF Request Translation Determinism
    - **Property 1: IMF Request Translation Determinism**
    - **Validates: Requirements 1.2, 1.3, 7.1, 7.2, 7.4, 7.5, 7.6, 7.7, 7.8**
    - `@given(selected_model=st.text(min_size=1, max_size=50), messages=st.lists(st.builds(dict, role=st.sampled_from(["system","user","assistant"]), content=st.text()), min_size=1, max_size=10), max_tokens=st.one_of(st.none(), st.just(0), st.integers(min_value=1, max_value=8192)), temperature=st.one_of(st.none(), st.floats(min_value=0.0, max_value=2.0, allow_nan=False)), request_model=st.one_of(st.none(), st.text(min_size=1)))`
    - Assert: output contains exactly the four keys `{"model", "messages", "stream", "options"}`
    - Assert: `model` == `selected_model` (not `request_model`)
    - Assert: `stream` is `False`
    - Assert: `max_tokens` clamping and default logic are correct for all input branches
    - Assert: calling twice with identical inputs produces identical outputs (determinism)

  - [ ]* 16.2 Write property test for Property 2: IMF Response Mapping Round-Trip Integrity
    - **Property 2: IMF Response Mapping Round-Trip Integrity**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.6, 8.1, 8.3, 8.4, 8.5**
    - `@given(message_content=st.text(min_size=1), done_reason=st.one_of(st.just("stop"), st.just("length"), st.text(min_size=1), st.none()), prompt_eval_count=st.one_of(st.none(), st.integers(min_value=0, max_value=100_000)), eval_count=st.one_of(st.none(), st.integers(min_value=0, max_value=100_000)), total_duration=st.integers(min_value=0), imf_passthrough_fields=imf_passthrough_strategy())`
    - Assert: `response.content == message_content`
    - Assert: `finish_reason` is `"stop"`, `"length"`, or `None` (no other values)
    - Assert: `total_tokens == prompt_tokens + completion_tokens`
    - Assert: all IMF fields outside `response`, `metadata`, `extensions` are byte-identical to input
    - Assert: calling twice with identical inputs produces identical outputs

  - [ ]* 16.3 Write property test for Property 3: Token Count Arithmetic Invariant
    - **Property 3: Token Count Arithmetic Invariant**
    - **Validates: Requirements 2.3, 2.4**
    - `@given(prompt_eval_count=st.one_of(st.none(), st.integers(min_value=0, max_value=1_000_000)), eval_count=st.one_of(st.none(), st.integers(min_value=0, max_value=1_000_000)))`
    - Call `IMFMapper.resolve_token_counts(prompt_eval_count, eval_count)`
    - Assert: null inputs replaced by `0`; all three values are non-negative integers
    - Assert: `total_tokens == prompt_tokens + completion_tokens` (no rounding, no off-by-one)

  - [ ]* 16.4 Write property test for Property 4: Error Response Structural Invariant
    - **Property 4: Error Response Structural Invariant**
    - **Validates: Requirements 1.7, 1.8, 1.9, 1.10, 9.1, 9.2, 9.3, 9.4, 9.5**
    - `@given(error_scenario=st.sampled_from(["timeout", "connect_error", "ollama_4xx", "ollama_5xx", "invalid_json", "missing_message_content", "missing_selected_model", "empty_messages", "model_not_loaded"]), request_id=st.one_of(st.none(), st.uuids().map(str)), imf=imf_lookup_strategy())`
    - For each scenario, configure `respx` mock or inject appropriate app state; POST `/infer`
    - Assert: `Content-Type: application/json`
    - Assert: response body is valid JSON containing keys `"event"` and `"request_id"`
    - Assert: HTTP status code matches the expected code for each scenario (`503`, `422`, `502`, `500`)
    - Assert: no partial `response` block in the body for any error path

  - [ ]* 16.5 Write property test for Property 5: Health Endpoint State Machine
    - **Property 5: Health Endpoint State Machine**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6**
    - `@given(starting=st.booleans(), ollama_reachable=st.booleans(), model_present=st.booleans())`
    - Directly set `health._startup_complete` and configure `respx` mock for `/api/tags` based on inputs
    - Assert: exactly one of the four defined response shapes is returned; no other shapes valid
    - Assert: the mapping is deterministic (same inputs → same HTTP status and body shape)

  - [ ]* 16.6 Write property test for Property 6: Metadata Completeness Invariant
    - **Property 6: Metadata Completeness Invariant**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
    - `@given(total_duration=st.one_of(st.just(0), st.just(-1), st.integers(min_value=1, max_value=10**12)), selected_model=st.one_of(st.none(), st.text(min_size=1, max_size=50)), wall_clock_ms=st.integers(min_value=0, max_value=600_000))`
    - Call `IMFMapper.to_imf_response()` directly with synthesised Ollama response
    - Assert: `metadata` contains exactly the keys `{"inference_backend", "inference_latency_ms", "model_name"}`
    - Assert: `inference_backend == "ollama"` always
    - Assert: latency is `floor(total_duration / 1_000_000)` when `total_duration > 0`; else `wall_clock_ms`
    - Assert: `model_name` equals `selected_model` or `null`

  - [ ]* 16.7 Write property test for Property 7: PII Exclusion from Logs
    - **Property 7: PII Exclusion from Logs**
    - **Validates: Requirements 10.6**
    - `@given(pii_fields=st.lists(st.text(min_size=1, max_size=30), min_size=1, max_size=10), message_contents=st.lists(st.text(min_size=1), min_size=1, max_size=5), log_level=st.sampled_from(["DEBUG","INFO","WARNING","ERROR"]))`
    - Build `IMFDocument` with `governance.pii_fields_detected = pii_fields` and matching message contents; POST `/infer`; capture stdout
    - Assert: no emitted JSON log entry contains any key whose name is in `pii_fields`
    - Assert: no emitted entry contains the raw string value of any message content

  - [ ]* 16.8 Write property test for Property 8: Non-Streaming Enforcement
    - **Property 8: Non-Streaming Enforcement**
    - **Validates: Requirements 7.2, 14.1, 14.2, 14.3, 14.4**
    - `@given(stream_value=st.one_of(st.booleans(), st.none()), messages=st.lists(st.builds(dict, role=st.just("user"), content=st.text(min_size=1)), min_size=1), selected_model=st.text(min_size=1))`
    - Call `IMFMapper.to_ollama_request()` directly; assert `stream == False` always
    - For `stream_value=True`: POST full request via `app_client`; assert HTTP 200 (not error); assert warning log entry `streaming_not_supported` emitted; assert response is a single complete JSON object

  - [ ]* 16.9 Write property test for Property 9: Prometheus Metrics Consistency
    - **Property 9: Prometheus Metrics Consistency**
    - **Validates: Requirements 11.2, 11.3, 11.4, 11.5**
    - `@given(operations=st.lists(st.builds(dict, outcome=st.sampled_from(["success","timeout","ollama_4xx","ollama_5xx"]), model=st.sampled_from(["llama3.2:3b"]), task_type=st.sampled_from(["chat","code","summarization"]), department=st.text(min_size=1, max_size=20)), min_size=1, max_size=20))`
    - Execute each operation via `app_client` with `respx` mocks configured per outcome
    - Query `prometheus_client` registry directly via `REGISTRY.get_sample_value()` after all operations
    - Assert: `llm_inference_requests_total{status="success"}` equals observed success count
    - Assert: `llm_inference_errors_total` equals observed error count per error_code label
    - Assert: `llm_inference_latency_seconds` received one observation per completed or failed call
    - Assert: all metric updates occurred before the HTTP response was returned (no lost counts)

- [ ] 17. Write integration test `tests/inference_adapter/test_integration.py`
  - [ ]* 17.1 Write integration test requiring a live Ollama instance (skipped in CI without `OLLAMA_BASE_URL`)
    - Decorate module to skip when `OLLAMA_BASE_URL` env var is not set:
      ```python
      pytestmark = pytest.mark.skipif(
          not os.getenv("OLLAMA_BASE_URL"), reason="requires live Ollama (set OLLAMA_BASE_URL)"
      )
      ```
    - Uses a real `OllamaClient` connecting to `OLLAMA_BASE_URL`
    - **`test_post_infer_valid_imf_returns_200_with_response_block`**: POST `/infer` with valid IMF `llama3.2:3b` → HTTP 200; `response.content` non-empty; `metadata.inference_backend == "ollama"`
    - **`test_get_health_returns_ok`**: GET `/health` → HTTP 200 `{"status": "ok", "backend": "ollama", "model": "llama3.2:3b"}`
    - **`test_model_not_loaded_returns_422`**: POST `/infer` with `selected_model = "nonexistent:model"` → HTTP 422 `{"event": "model_not_loaded"}`
    - **`test_metrics_endpoint_returns_prometheus_text`**: GET port 9090 `/metrics` → `text/plain` with `llm_inference_requests_total` present
    - _Requirements: 1.1, 4.1, 4.3, 11.1_

- [ ] 18. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- **POC constraints apply throughout**: `replicaCount: 1`, `autoscaling.enabled: false`, `vault.enabled: false`, plain HTTP between services, no Istio mTLS, no OTel tracing, no HPA. All deferred to Phase 2 per the platform master contract.
- **Two deployment units, one Helm chart**: Ollama and the Inference Adapter are packaged in a single chart at `llm-platform/charts/inference-ollama/`. They use separate Kubernetes Deployments and Services but share a namespace and are configured together via `values.yaml`.
- **Degraded startup semantics (Tasks 8 and 10)**: Ollama being unreachable at startup does NOT crash the Inference Adapter process. The adapter starts in degraded mode (`ollama_reachable = False`, `ollama_models = []`), and the health endpoint immediately returns `503 ollama_unreachable`. Configuration errors (`PORT`, `OLLAMA_TIMEOUT_SECONDS`, `DEFAULT_TEMPERATURE` out of range) DO crash the process — they indicate a misconfigured deployment.
- **Metrics port isolation (Tasks 9 and 10)**: Prometheus `/metrics` runs on port 9090, isolated from the application port 8087. Use a background `asyncio` task with a secondary `uvicorn.Server` serving `make_asgi_app()` — same pattern as `cache_service/main.py`. If port 9090 cannot be bound, the adapter fails to start (Requirement 11.6).
- **stream always False (Tasks 5 and 7)**: `OllamaClient.chat()` forces `payload["stream"] = False` before sending, and `IMFMapper.to_ollama_request()` always outputs `"stream": false`. Both layers enforce this independently. A `stream=true` IMF request triggers a warning log and proceeds as non-streaming.
- **Middleware ordering (Task 10)**: `LoggingMiddleware` is added before routers so it wraps all endpoint calls and captures the true final status code including 422/503 error responses.
- **Body re-injection in LoggingMiddleware (Task 8)**: Use `request.scope["_body"]` pattern (same as `cache_service/middleware/logging.py`) to re-inject the consumed body bytes so the downstream endpoint handler can still read it.
- **`respx` for async httpx mocking**: All Ollama HTTP calls in unit and property tests are intercepted via `respx`. No live Ollama instance is required except for the optional integration test in Task 17.
- **Property test isolation (Task 16)**: Property tests that exercise app state must reset `app.state.ollama_models` and Prometheus counters between Hypothesis examples to avoid state bleed. Use fresh `prometheus_client.CollectorRegistry()` instances or call `REGISTRY.unregister()` in teardown where needed.
- **Init Job vs Init Container**: The design specifies a Kubernetes `Job` (not an init container) for model pre-pull. The Job runs after the Ollama pod is scheduled and calls `POST /api/pull` for each model sequentially. The Adapter deployment's `initContainers` can include a wait-for-ollama sidecar if needed.
- **IMF passthrough guarantee (Task 5)**: `IMFMapper.to_imf_response()` must not touch `request_id`, `trace_id`, `span_id`, `timestamp_utc`, `user`, `governance`, `routing`, `cache`, or `extensions`. These fields are copied by reference from the input IMF; the output IMF is a new object with only `response`, `metadata`, and (optionally) `extensions` modified.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "3.1"] },
    { "id": 2, "tasks": ["4.1", "9.1"] },
    { "id": 3, "tasks": ["5.1", "5.2"] },
    { "id": 4, "tasks": ["6.1", "7.1"] },
    { "id": 5, "tasks": ["8.1"] },
    { "id": 6, "tasks": ["10.1"] },
    { "id": 7, "tasks": ["12.1", "13.1"] },
    { "id": 8, "tasks": ["13.2", "13.3"] },
    { "id": 9, "tasks": ["14.1"] },
    { "id": 10, "tasks": ["14.2", "14.3", "14.4", "14.5", "14.6", "14.7"] },
    { "id": 11, "tasks": ["16.1", "16.2", "16.3", "16.4", "16.5", "16.6", "16.7", "16.8", "16.9"] },
    { "id": 12, "tasks": ["17.1"] }
  ]
}
```
