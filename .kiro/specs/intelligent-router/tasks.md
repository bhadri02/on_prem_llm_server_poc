# Implementation Plan: Intelligent Router

## Overview

Implementation tasks for the Intelligent Router (Layer 3): a standalone FastAPI microservice that classifies incoming requests by task type, selects the correct inference model, health-checks backends, consults the Cache Layer, dispatches to the Inference Adapter, and writes audit events. The service runs on port 8082, exposes Prometheus metrics on port 9090, emits structured JSON logs to stdout, and integrates with the Cache Layer (8086), Inference Adapter (8087), and Audit Store (9200) using plain HTTP with a shared `httpx.AsyncClient`. All downstream calls are fire-and-forget where specified. Production-deferred features (ML classifiers, OPA, circuit breakers, gRPC, mTLS) are out of scope for the POC.

---

## Tasks

- [x] 1. Project scaffolding and package structure
  - Create the `intelligent_router/` Python package directory with `__init__.py`
  - Create the `intelligent_router/routers/` sub-package directory with `__init__.py`
  - Create the `tests/` directory tree: `tests/conftest.py`, `tests/unit/`, `tests/property/`, `tests/integration/`, `tests/smoke/` (all with `__init__.py`)
  - Create `requirements.txt` with pinned versions for: `fastapi==0.115.5`, `uvicorn[standard]==0.32.1`, `pydantic==2.10.3`, `pydantic-settings==2.6.1`, `prometheus-client==0.21.1`, `httpx==0.27.2`, `pyyaml==6.0.2`, `pytest==8.3.5`, `pytest-asyncio==0.24.0`, `hypothesis==6.131.18`, `pytest-httpx==0.30.0`
  - _Requirements: 15.1, 15.3_


- [x] 2. `config.py` — environment-driven settings
  - [x] 2.1 Implement `Settings` class using `pydantic_settings.BaseSettings` with required fields: `model_matrix_path: str` (`MODEL_MATRIX_PATH`), `task_rules_path: str` (`TASK_RULES_PATH`), `audit_store_url: str` (`AUDIT_STORE_URL`); and optional fields: `cache_url: str = "http://cache:8086"`, `inference_adapter_url: str = "http://inference-adapter:8087"`, `log_level: str = "INFO"`, `inference_timeout_seconds: int = 120`, `health_check_timeout_seconds: int = 5`, `port: int = 8082`
  - [x] 2.2 Instantiate a module-level `settings = Settings()` singleton so other modules can import it directly
  - Write unit tests in `tests/unit/test_config.py` verifying: `MODEL_MATRIX_PATH` absent raises, `TASK_RULES_PATH` absent raises, `AUDIT_STORE_URL` absent raises, defaults for `CACHE_URL` and `INFERENCE_ADAPTER_URL` are applied when unset, `LOG_LEVEL` unset defaults to `"INFO"` without error
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

- [x] 3. `logging_config.py` — structured JSON logger
  - [x] 3.1 Implement `JSONFormatter` class (subclass of `logging.Formatter`) whose `format()` method returns a single-line JSON string containing `timestamp` (ISO-8601 UTC ending in `Z`), `level`, and `message` fields, plus any extra fields passed via `extra={"extra_fields": {...}}`
  - [x] 3.2 Implement `get_logger(name: str) -> logging.Logger` factory that attaches a `StreamHandler(sys.stdout)` with `JSONFormatter` and sets the level from `settings.log_level`, defaulting to `INFO` for unrecognised values
  - Write unit tests in `tests/unit/test_logging.py` verifying: output is valid single-line JSON, mandatory fields present, extra fields merged at top level, unrecognised `LOG_LEVEL` falls back to `INFO`
  - _Requirements: 13.1, 13.5, 13.6, 13.7_


- [x] 4. `models.py` — Pydantic IMF models and request/response schemas
  - [x] 4.1 Define `UUID4_RE` compiled regex pattern: `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (case-insensitive)
  - [x] 4.2 Implement `Message(BaseModel)` with `role: str` and `content: str`; implement `UserBlock`, `RequestBlock` (with `messages: list[Message] = Field(min_length=1)` and optional fields), `GovernanceBlock` (with all governance fields and correct POC defaults), `RoutingBlock`, `CacheBlock`, `UsageBlock`, and `ResponseBlock`
  - [x] 4.3 Implement `IMFRequest(BaseModel)` with `request_id: str` (validated UUID-v4 via `@field_validator`), all required blocks, and a `@field_validator("request_id")` that rejects non-UUID values with `ValueError("request_id must be a valid UUID-v4")`
  - [x] 4.4 Implement `OpenAIChatRequest(BaseModel)` with `messages: list[Message] = Field(min_length=1)`, optional `model`, `max_tokens`, `temperature`, and `stream: bool = False`
  - Write unit tests in `tests/unit/test_models.py` verifying: valid UUID-v4 passes, non-UUID string fails with `ValueError`, absent or empty `messages` raises `ValidationError`, optional fields accept `None`, `GovernanceBlock` defaults are all correct
  - _Requirements: 1.3, 1.4, 1.5, 9.3, 11.1, 11.2_

- [x] 5. `task_classifier.py` — keyword-based task classifier
  - [x] 5.1 Define `PRIORITY_ORDER = ["code", "reasoning", "summarization", "translation", "chat"]` and implement `ClassifierRules` dataclass with `rules: dict[str, list[str]]`, `default: str = "chat"`, and a `total_keyword_count` property that sums keyword counts across all task types
  - [x] 5.2 Implement `load_classifier_rules(path: str) -> Optional[ClassifierRules]` that reads the YAML at `path`, extracts `rules` and `default` keys, and returns `None` on `FileNotFoundError`, `yaml.YAMLError`, or any other read error — logging a specific ERROR in each case
  - [x] 5.3 Implement `classify_task(messages: list[dict], rules: ClassifierRules) -> str` that concatenates `content` fields with a single space separator, converts to lowercase, and applies each keyword rule in `PRIORITY_ORDER` as a case-insensitive substring search — returning the first matching `task_type` or `rules.default` if none match
  - Write unit tests in `tests/unit/test_task_classifier.py` verifying: YAML not found returns `None` and logs ERROR, malformed YAML returns `None` and logs ERROR, empty `rules` map returns `ClassifierRules` (not `None`), priority order `code` beats `reasoning`, `chat` default on no match, classification is case-insensitive, multi-message concatenation works correctly
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_


- [x] 6. `model_selector.py` — model matrix loading and selection logic
  - [x] 6.1 Implement `ModelEntry` dataclass with fields: `name: str`, `backend: str`, `endpoint: str`, `tasks: list[str]`, `health_url: str`, `fallback: Optional[str]`; implement `ModelMatrix` dataclass with `models: dict[str, ModelEntry]` and `task_defaults: dict[str, str]`
  - [x] 6.2 Implement `load_model_matrix(path: str) -> Optional[ModelMatrix]` that reads the YAML, validates `models` and `task_defaults` are non-empty, builds the `ModelEntry` objects, and returns `None` on any failure — logging a specific ERROR in each case
  - [x] 6.3 Implement `select_model(task_type, routing_mode, pinned_model, matrix) -> tuple[str, str]` that returns `(selected_model_name, effective_routing_mode)`, raising `InvalidPinnedModelError` for invalid pinned models and `NoModelForTaskError` if task_type has no mapping and `chat` default is also missing
  - [x] 6.4 Implement `get_fallback_chain(model_name: str, matrix: ModelMatrix) -> list[str]` that follows `fallback` links starting from `model_name`, stopping on `None` or a cycle (via visited set), and returns the ordered chain
  - Write unit tests in `tests/unit/test_model_selector.py` verifying: matrix file not found returns `None` and logs ERROR, malformed YAML returns `None`, empty `models` map returns `None`, empty `task_defaults` returns `None`, auto-mode selects correct primary model, pinned mode with valid model succeeds, pinned mode with unknown model raises `InvalidPinnedModelError`, missing task_type falls back to `chat` default, missing `chat` default raises `NoModelForTaskError`, fallback chain follows links and stops on `None`
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 7. `health_checker.py` — model backend health check
  - [x] 7.1 Implement `async def check_model_health(health_url: str, http_client: httpx.AsyncClient, timeout_seconds: float) -> bool` that issues `GET` to `health_url` with `follow_redirects=False` and the given timeout; returns `True` only for HTTP 200; returns `False` for any non-200 status (including 3xx), `httpx.TimeoutException`, or `httpx.ConnectError`
  - Write unit tests in `tests/unit/test_health_checker.py` verifying: HTTP 200 returns `True`, HTTP 503 returns `False`, HTTP 301 redirect returns `False` (follow_redirects=False), timeout returns `False`, connection refused returns `False`
  - _Requirements: 4.1, 4.2, 4.3, 4.6_


- [x] 8. `fallback_manager.py` — fallback chain traversal
  - [x] 8.1 Implement `FallbackState` dataclass with `chain: list[str]`, `current_index: int`, `fallback_level: int`, and properties `selected_model` (returns `chain[current_index]`) and `has_next` (returns `current_index + 1 < len(chain)`); implement `advance() -> Optional[str]` that increments both `current_index` and `fallback_level` by exactly 1, returning the new model name or `None` if exhausted
  - [x] 8.2 Implement `create_fallback_state(primary_model: str, matrix: ModelMatrix) -> FallbackState` that builds the chain via `get_fallback_chain` and initialises `current_index=0`, `fallback_level=0`
  - Write unit tests in `tests/unit/test_fallback_manager.py` verifying: `advance()` on a single-model chain returns `None`, `advance()` increments `fallback_level` by exactly 1, `fallback_level` never decreases, chain with 3 models advances correctly through all levels, `has_next` is `False` after last advance
  - _Requirements: 3.7, 3.8, 4.3, 4.4, 4.7_

- [x] 9. `cache_client.py` — cache lookup and async write
  - [x] 9.1 Implement `async def cache_lookup(messages, model, task_type, request_id, cache_url, http_client) -> dict` that POSTs to `{cache_url}/cache/lookup` with a 3-second timeout; returns the parsed response dict on HTTP 200; returns `{"hit": False}` on non-200, `httpx.TimeoutException`, or any other exception — logging a WARNING with `request_id` and failure reason in each case; never raises
  - [x] 9.2 Implement `async def cache_write(messages, model, task_type, response_imf, cache_url, http_client) -> None` that POSTs to `{cache_url}/cache/write` with a 3-second timeout; logs a WARNING on non-200, timeout, or connection failure; never raises; is always called via `BackgroundTask`
  - Write unit tests in `tests/unit/test_cache_client.py` verifying: HTTP 200 returns parsed dict, non-200 returns `{"hit": False}` and logs WARNING, timeout returns `{"hit": False}` and logs WARNING, cache_write timeout logs WARNING and does not raise, cache_write non-200 logs WARNING and does not raise
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 7.1, 7.2, 7.3, 7.4, 7.5_


- [x] 10. `inference_client.py` — inference adapter HTTP client
  - [x] 10.1 Define `InferenceError(Exception)` with `reason: str` and optional `status_code: int` attributes
  - [x] 10.2 Implement `async def call_inference(imf, inference_url, request_id, timeout_seconds, http_client) -> dict` that POSTs to `{inference_url}/infer` with `Content-Type: application/json` and `X-Request-Id: <request_id>` headers; raises `InferenceError(reason="non_200")` on non-200, `InferenceError(reason="parse_error")` on invalid JSON, `InferenceError(reason="missing_content")` if `response.content` is null or absent, `InferenceError(reason="timeout")` on `httpx.TimeoutException`, `InferenceError(reason="connect_error")` on `httpx.ConnectError`
  - Write unit tests in `tests/unit/test_inference_client.py` verifying: HTTP 200 with valid IMF returns parsed dict, HTTP 500 raises `InferenceError(reason="non_200")`, empty body raises `InferenceError(reason="parse_error")`, valid JSON with null `response.content` raises `InferenceError(reason="missing_content")`, timeout raises `InferenceError(reason="timeout")`, `X-Request-Id` header is set on every POST, `Content-Type: application/json` is set on every POST
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

- [x] 11. `audit_client.py` — fire-and-forget audit writer
  - [x] 11.1 Implement `async def post_audit_event(event: dict, audit_store_url: str, http_client: httpx.AsyncClient) -> None` that POSTs to `{audit_store_url}/audit/events` with a 2-second timeout; logs WARNING on `httpx.TimeoutException` (with `"timeout"` in the message), on non-2xx response (with `request_id` and status code), or on any other exception; never raises; always called via `BackgroundTask`
  - Write unit tests in `tests/unit/test_audit_client.py` verifying: HTTP 500 from Audit Store logs WARNING and does not raise, timeout logs WARNING with `"timeout"` keyword and does not raise, connection refused logs WARNING and does not raise, successful 201 response produces no WARNING
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_


- [x] 12. `metrics.py` — Prometheus metric definitions
  - [x] 12.1 Define `requests_total = Counter("llm_router_requests_total", ..., labelnames=["outcome", "task_type", "routing_mode"])` where `outcome` ∈ `{"cache_hit", "inference_success", "fallback_success", "error"}`
  - [x] 12.2 Define `latency = Histogram("llm_router_latency_seconds", ..., labelnames=["task_type", "routing_mode"], buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 120.0])`
  - [x] 12.3 Define `cache_hits_total = Counter("llm_router_cache_hits_total", ..., labelnames=["task_type", "model"])`
  - [x] 12.4 Define `fallbacks_total = Counter("llm_router_fallbacks_total", ..., labelnames=["task_type", "reason"])` where `reason` ∈ `{"health_check_failed", "inference_error"}`
  - [x] 12.5 Define `errors_total = Counter("llm_router_errors_total", ..., labelnames=["error_code"])` where `error_code` ∈ `{"governance_check_failed", "all_backends_exhausted", "invalid_pinned_model", "internal_error"}`
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

- [x] 13. `metrics_app.py` — separate ASGI metrics application on port 9090
  - [x] 13.1 Create a lightweight ASGI application (Starlette `Router` or bare Starlette app) that serves `GET /metrics` using `prometheus_client.make_asgi_app()` on port 9090
  - [x] 13.2 Import `intelligent_router.metrics` at the top of `metrics_app.py` to ensure all five counters/histograms are registered in the default Prometheus registry before `make_asgi_app()` is called
  - [x] 13.3 Ensure this app is completely independent of the main FastAPI app — no auth, no shared lifespan, no `app.state` dependency
  - _Requirements: 12.1, 12.7_


- [x] 14. `pipeline.py` — six-stage routing pipeline orchestrator
  - [x] 14.1 Define `PipelineResult` dataclass with fields: `success: bool`, `status_code: int`, `imf: dict`, `error_code: str | None`, `latency_ms: int`
  - [x] 14.2 Implement `async def run_routing_pipeline(imf, state, background_tasks) -> PipelineResult` with the governance gate check first (returns HTTP 400 `governance_check_failed` if `content_safety_passed` is false or absent), then Stage 1 (task classification — always overwrites inbound `task_type`), then Stage 2 (model selection — raises `InvalidPinnedModelError`/`NoModelForTaskError` returning 422/503), then Stages 3–6 in a `while True` fallback loop
  - [x] 14.3 Implement the fallback loop: Stage 3 (health check — on failure: increment `fallbacks_total`, call `fallback.advance()`, update `routing.fallback_level`, log `routing_fallback` JSON entry, dispatch audit background task, `continue`; on chain exhaustion: dispatch audit, return 503 `all_backends_exhausted`), Stage 4 (cache lookup — on HIT with valid content: set `cache.*` and `response.*` fields, increment `cache_hits_total`, dispatch cache_hit audit, return 200; on HIT with missing content: reset to MISS and fall through), Stage 5 (inference dispatch — on `InferenceError`: increment `fallbacks_total`, advance fallback, log warning, dispatch audit, `continue`; on success: proceed to Stage 6)
  - [x] 14.4 Implement Stage 6: dispatch `cache_write` background task (only when `cache.lookup_hit=False`), dispatch routing-decision success audit background task, return 200 `PipelineResult`; implement `_ms(t0)` helper for wall-clock latency; implement `_build_routing_audit`, `_build_fallback_audit`, `_build_cache_hit_audit` helper functions building the correct audit event dicts per design
  - [x] 14.5 Add unhandled-exception guard: wrap the entire pipeline body in `try/except Exception` returning 500 `internal_error` and emitting a structured ERROR log with `request_id`, `error`, and `timestamp_utc`
  - Write unit tests in `tests/unit/test_pipeline.py` verifying: governance gate with `content_safety_passed=False` returns 400 and makes no downstream calls, invalid pinned model returns 422, `task_type` in inbound IMF is always overwritten, cache HIT with missing `response.content` is treated as MISS, `fallback_level` is 0 when primary model succeeds, cache write is NOT dispatched when `cache.lookup_hit=True`
  - _Requirements: 1.1, 1.2, 1.6, 1.7, 1.8, 3.7, 3.8, 4.4, 5.2, 5.3, 5.4, 6.1, 7.1, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 11.1, 11.2, 11.3, 11.4, 11.5_


- [x] 15. `routers/route.py` — POST /route (primary IMF endpoint)
  - [x] 15.1 Implement `POST /route` handler accepting `body: IMFRequest`, `request: Request`, `background_tasks: BackgroundTasks`; call `run_routing_pipeline(imf, request.app.state, background_tasks)`
  - [x] 15.2 On success: increment `metrics.requests_total` with correct `outcome`/`task_type`/`routing_mode` labels, observe `metrics.latency`, emit INFO-level `routing_decision` log entry with all required fields (`request_id`, `task_type`, `selected_model`, `routing_mode`, `cache_hit`, `fallback_level`, `outcome`, `latency_ms`), return `JSONResponse(200, content=result.imf)`
  - [x] 15.3 On error: increment `metrics.errors_total` with `error_code` label, construct error body (`{"error": error_code, "request_id": ...}`) with `fallback_level` appended for `all_backends_exhausted` and `model` appended for `invalid_pinned_model`, return `JSONResponse(result.status_code, content=error_body)`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 12.2, 12.3, 12.6, 13.2_

- [x] 16. `routers/openai_compat.py` — POST /v1/chat/completions
  - [x] 16.1 Implement `POST /v1/chat/completions` handler accepting `body: OpenAIChatRequest`, `request: Request`, `background_tasks: BackgroundTasks`; validate `body.messages` is non-empty (return 422 with OpenAI error schema if empty); construct a complete IMF dict with `request_id = str(uuid.uuid4())`, POC user defaults (`user_id: "poc-user"`, `department: "poc"`, `roles: ["developer"]`, `auth_method: "api_key"`), `governance.content_safety_passed=True`, and `routing_mode = "pinned"` if `body.model` is non-null else `"auto"`
  - [x] 16.2 Pass the constructed IMF through `run_routing_pipeline`; on success: construct and return the OpenAI-compatible response body with `id`, `object: "chat.completion"`, `created` (Unix epoch int), `model`, `choices` (single entry with `message.role="assistant"`, `message.content`, and `finish_reason` defaulting to `"stop"` if null), and `usage`
  - [x] 16.3 On pipeline error: return `JSONResponse(result.status_code, content={"error": {"code": result.status_code, "message": result.error_code, "type": "service_unavailable"}})` — no `X-API-Key` required on this endpoint
  - _Requirements: 2.8, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [x] 17. `routers/health.py` — GET /health
  - [x] 17.1 Implement `GET /health` handler reading `state.classifier_rules` and `state.model_matrix` from `request.app.state`; return HTTP 200 `{"status": "ok", "rules_loaded": rules.total_keyword_count, "models_loaded": len(matrix.models)}` when both are loaded; return HTTP 503 `{"status": "degraded", "reason": "rules_load_failed"}` or `"matrix_load_failed"` when either is `None`
  - [x] 17.2 Ensure no authentication is required and no downstream calls (Cache, Inference, Audit) are made
  - _Requirements: 10.1, 10.2, 10.3, 10.4_


- [x] 18. `main.py` — FastAPI app factory and lifespan handler
  - [x] 18.1 Define `lifespan` async context manager performing in order before `yield`: (1) validate required env vars `MODEL_MATRIX_PATH`, `TASK_RULES_PATH`, `AUDIT_STORE_URL` are non-empty — log ERROR and `sys.exit(1)` if any absent; (2) validate `INFERENCE_TIMEOUT_SECONDS` ∈ `[1, 600]` and `HEALTH_CHECK_TIMEOUT_SECONDS` ∈ `[1, 30]` — log ERROR and `sys.exit(1)` if out of range; (3) call `load_classifier_rules` — `sys.exit(1)` if returns `None`; log WARNING if `rules` map is empty; (4) call `load_model_matrix` — `sys.exit(1)` if returns `None`; (5) create `httpx.AsyncClient()`; (6) store `settings`, `classifier_rules`, `model_matrix`, `http_client` on `app.state`; emit INFO startup log with `rules_loaded` and `models_loaded` counts; after `yield`: `await http_client.aclose()`, emit INFO shutdown log
  - [x] 18.2 Create the FastAPI `app` with `lifespan=lifespan`, `title="Intelligent Router"`, `version="0.1.0"`; add custom exception handler for `RequestValidationError` returning HTTP 400 for JSON parse errors and HTTP 422 for other validation errors
  - [x] 18.3 Include `route_router`, `openai_router`, and `health_router` on `app`; define `create_app() -> FastAPI` factory function for tests and the entrypoint
  - _Requirements: 1.7, 1.8, 2.5, 2.6, 3.4, 3.5, 14.2, 14.3, 14.4, 14.7, 14.8, 15.1, 15.2_

- [x] 19. Checkpoint — core modules complete
  - Ensure all tests pass, ask the user if questions arise.


- [x] 20. Config YAML files — `task_classifier_rules.yaml` and `model_matrix.yaml`
  - [x] 20.1 Create `task_classifier_rules.yaml` at repo root (or `intelligent_router/`) with `rules` map covering task types `code`, `reasoning`, `summarization`, `translation` with representative keyword lists per design (e.g., `code`: `["code", "function", "python", "javascript", "debug", "write a script", "implement"]`), and `default: chat`
  - [x] 20.2 Create `model_matrix.yaml` at repo root (or `intelligent_router/`) with a `models` map entry for `llama3.2-3b` (backend: ollama, endpoint: `http://inference-ollama:11434`, tasks, health_url: `http://inference-ollama:11434/api/tags`, fallback: null) and `task_defaults` mapping all five task types to `llama3.2-3b`
  - _Requirements: 2.1, 2.5, 3.1, 3.4_

  - [x] 21. `Dockerfile` and entrypoint
  - [x] 21.1 Write a multi-stage `Dockerfile`: base stage `python:3.12-slim`; install `requirements.txt`; copy `intelligent_router/` package and both config YAML files; set `CMD ["sh", "-c", "uvicorn intelligent_router.main:app --host 0.0.0.0 --port 8082 & uvicorn intelligent_router.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & wait"]`
  - [x] 21.2 Add a `.dockerignore` (or extend existing) excluding `.git`, `__pycache__`, `tests/`, `*.pyc`, `.kiro/`
  - _Requirements: 15.4_


- [x] 22. Property-based tests — task classification (Properties 1 and 2)
  - [x] 22.1 Create `tests/conftest.py` with: a `test_app` fixture using `create_app()` with mocked `app.state` (pre-loaded `ClassifierRules`, `ModelMatrix`, and a mock `httpx.AsyncClient`), an `httpx.AsyncClient` fixture using `ASGITransport`, and a Prometheus registry reset fixture to prevent counter bleed between tests
  - [x] 22.2 Create `tests/property/test_classification_properties.py` with Hypothesis `settings` profile `ci` (`max_examples=100`)
  - [x] 22.3 **[PBT]** Property 1 — `test_keyword_match_selects_highest_priority_task_type`: `@given(prefix=st.text(max_size=50), suffix=st.text(max_size=50), task_type=st.sampled_from(["code","reasoning","summarization","translation"]), keyword=st.sampled_from(rules_for_task), case_variant=st.sampled_from(["lower","upper","title"]))`; build messages embedding the keyword; assert `classify_task(messages, RULES) == task_type` (when no higher-priority keyword co-present)
    - **Property 1: Task Classification — Keyword Match Invariant**
    - **Validates: Requirements 2.1, 2.3, 2.4**
  - [x] 22.4 **[PBT]** Property 2 — `test_no_keyword_match_always_returns_chat`: `@given(messages=st.lists(st.fixed_dictionaries({"role": st.sampled_from(["user","assistant","system"]), "content": st.text().filter(lambda t: not any(kw.lower() in t.lower() for kw in ALL_KEYWORDS))}), min_size=0, max_size=5))`; assert `classify_task(messages, RULES) == "chat"` for all keyword-free inputs including empty list and None-content messages
    - **Property 2: Task Classification — Default Invariant**
    - **Validates: Requirements 2.2**


- [x] 23. Property-based tests — model selection and fallback (Properties 3 and 5)
  - [x] 23.1 Create `tests/property/test_model_selection_properties.py` with Hypothesis `ci` profile
  - [x] 23.2 **[PBT]** Property 3 — `test_auto_select_always_returns_matrix_model`: `@given(task_type=st.sampled_from(["code","reasoning","summarization","translation","chat","unknown_task"]), matrix=generated_model_matrix_strategy())`; call `select_model(task_type, "auto", None, matrix)` and assert the returned model name is always a key in `matrix.models` and returned `routing_mode` is always `"auto"`; also test pinned mode with valid model name always returns that exact name
    - **Property 3: Model Selection — Selected Model Always in Matrix**
    - **Validates: Requirements 3.1, 3.2, 3.6**
  - [x] 23.3 Create `tests/property/test_fallback_properties.py` with Hypothesis `ci` profile
  - [x] 23.4 **[PBT]** Property 5 — `test_fallback_level_monotonicity`: `@given(chain_length=st.integers(min_value=1, max_value=5), failures=st.integers(min_value=0, max_value=5))`; build a model matrix with `chain_length` chained fallback models; advance `FallbackState` for `min(failures, chain_length)` steps; assert `fallback_level == steps_advanced`, `fallback_level` never decreases, and when all models exhausted (failures >= chain_length) the final `fallback_level == chain_length - 1` and `has_next == False`
    - **Property 5: Fallback Level Monotonicity**
    - **Validates: Requirements 3.7, 3.8, 4.3, 4.4, 4.5, 4.7, 6.3, 6.4**

- [x] 24. Property-based tests — IMF field preservation (Property 4)
  - [x] 24.1 Create `tests/property/test_imf_preservation_properties.py` with Hypothesis `ci` profile
  - [x] 24.2 **[PBT]** Property 4 — `test_imf_field_preservation_invariant`: `@given(imf=valid_imf_strategy())` generating IMFs with random non-null values in `request_id`, `trace_id`, `span_id`, `user.*`, `governance.*`, `request.messages`, `request.max_tokens`, `request.temperature`, `metadata`, `extensions`; run pipeline with mocked Cache (MISS) and mocked Inference (echoes IMF with populated `response` block); assert every field NOT in `WRITE_SET = {request.task_type, routing.*, cache.*}` is byte-identical to inbound value; assert `governance` and `user` blocks are completely unchanged
    - **Property 4: IMF Field Preservation Invariant**
    - **Validates: Requirements 11.1, 11.2, 11.6**


- [x] 25. Property-based tests — OpenAI compatibility and cache consistency (Properties 6 and 7)
  - [x] 25.1 Create `tests/property/test_openai_compat_properties.py` with Hypothesis `ci` profile
  - [x] 25.2 **[PBT]** Property 6 — `test_openai_response_shape_invariant`: `@given(messages=st.lists(valid_message_strategy(), min_size=1, max_size=10), model=st.one_of(st.none(), st.text(min_size=1, max_size=30)))`; POST to `/v1/chat/completions` with mocked Inference returning a valid IMF; assert response has `id` (non-empty string), `object == "chat.completion"`, `model` (non-null string), `choices[0].message.role == "assistant"`, `choices[0].message.content` is non-null non-empty string, `choices[0].finish_reason` is non-null string, `usage.prompt_tokens >= 0`, `usage.completion_tokens >= 0`, `usage.total_tokens >= 0`; for 503 error response assert body has `error.code`, `error.message`, `error.type`
    - **Property 6: OpenAI Compatibility — Response Shape Invariant**
    - **Validates: Requirements 9.2, 9.5**
  - [x] 25.3 Create `tests/property/test_cache_consistency_properties.py` with Hypothesis `ci` profile
  - [x] 25.4 **[PBT]** Property 7 — `test_cache_lookup_result_consistency`: `@given(imf=valid_imf_strategy(), cache_outcome=st.sampled_from(["hit", "miss", "timeout", "error"]))`; configure mock Cache to return HIT (with non-null `response.content`), MISS `{"hit": false}`, timeout, or non-200; use a call-counting mock Inference; assert: on HIT → `imf_out.cache.lookup_hit=True`, inference NOT called; on MISS/timeout/error → `imf_out.cache.lookup_hit=False`, inference called exactly once; invariants hold for both `auto` and `pinned` routing modes
    - **Property 7: Cache Lookup Result Consistency**
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.6, 6.1, 7.1, 7.4, 11.3, 11.5**


- [x] 26. Property-based tests — audit isolation, health state, metrics, and logging (Properties 8–11)
  - [x] 26.1 Create `tests/property/test_audit_isolation_properties.py` with Hypothesis `ci` profile
  - [x] 26.2 **[PBT]** Property 8 — `test_audit_failure_does_not_surface_to_caller`: `@given(imf=valid_imf_strategy(), audit_failure=st.sampled_from([500, 503, "timeout", "refused"]))`; configure mock Audit Store to fail per `audit_failure`; call `POST /route` and `POST /v1/chat/completions`; assert endpoint returns the correct HTTP status code (200 or error) and body unaffected by audit failure; assert a WARNING log was emitted (not an ERROR to caller)
    - **Property 8: Audit Failure Isolation**
    - **Validates: Requirements 8.5, 8.6**
  - [x] 26.3 Create `tests/property/test_health_properties.py` with Hypothesis `ci` profile
  - [x] 26.4 **[PBT]** Property 9 — `test_health_state_reflects_loaded_config`: `@given(rules_loaded=st.booleans(), matrix_loaded=st.booleans(), keyword_count=st.integers(min_value=0, max_value=50), model_count=st.integers(min_value=1, max_value=10))`; set `app.state.classifier_rules` to `ClassifierRules` with `keyword_count` total keywords or `None`; set `app.state.model_matrix` to a matrix with `model_count` models or `None`; call `GET /health` without auth; assert HTTP 200 + `{"status":"ok","rules_loaded": keyword_count,"models_loaded": model_count}` when both loaded; assert HTTP 503 + `{"status":"degraded","reason": ...}` when either is `None`
    - **Property 9: Health State Accurately Reflects Loaded Configuration**
    - **Validates: Requirements 10.1, 10.2**
  - [x] 26.5 Create `tests/property/test_metrics_properties.py` with Hypothesis `ci` profile
  - [x] 26.6 **[PBT]** Property 10 — `test_metrics_counters_monotonically_nondecreasing`: `@given(n=st.integers(min_value=1, max_value=10), outcomes=st.lists(st.sampled_from(["cache_hit","inference_success","fallback_success","error"]), min_size=1, max_size=10))`; record counter values before; process N requests with configured outcomes; assert `llm_router_requests_total` increased by exactly N, `llm_router_cache_hits_total` increased by cache_hit count, `llm_router_fallbacks_total` increased by fallback_success count, `llm_router_errors_total` increased by error count, `llm_router_latency_seconds` has exactly N new observations; no counter ever decreases
    - **Property 10: Metrics Counters Are Monotonically Non-Decreasing**
    - **Validates: Requirements 12.2, 12.3, 12.4, 12.5, 12.6**
  - [x] 26.7 Create `tests/property/test_logging_properties.py` with Hypothesis `ci` profile
  - [x] 26.8 **[PBT]** Property 11 — `test_every_log_entry_is_single_line_json`: `@given(operation=st.sampled_from(["route_success","route_cache_hit","route_fallback","route_error","health","openai_success"]))`; capture stdout during operation via `io.StringIO`; for each captured log line assert: `json.loads(line)` succeeds, `"timestamp"` is present and parses as ISO-8601 ending in `Z`, `"level"` is one of `DEBUG/INFO/WARNING/ERROR`, line contains no embedded newlines; for routing_decision log entries assert all eight required fields are present
    - **Property 11: Every Log Entry Is a Single-Line JSON Object With Mandatory Fields**
    - **Validates: Requirements 13.1, 13.2, 13.5**


- [x] 27. Checkpoint — property-based tests complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 28. Integration tests — route endpoint flows
  - [x] 28.1 Create `tests/integration/test_route_endpoint.py`; implement happy path test: valid IMF with `content_safety_passed=True`, mocked cache MISS, mocked inference returning valid IMF → assert HTTP 200, all WRITE_SET fields populated, `cache.lookup_hit=False`, `fallback_level=0`
  - [x] 28.2 Implement cache HIT test: mocked cache returns HIT with valid `response.content` → assert HTTP 200, `cache.lookup_hit=True`, `response.content` matches cache response, inference mock NOT called
  - [x] 28.3 Implement governance gate test: IMF with `content_safety_passed=False` → assert HTTP 400 `{"error": "governance_check_failed"}`, no cache/inference/audit calls made
  - [x] 28.4 Implement health-check-failure-then-fallback-success test: primary model health check mock returns 503, fallback model mock returns 200; inference succeeds → HTTP 200, `fallback_level=1`, `selected_model` = fallback model name
  - [x] 28.5 Implement all-backends-exhausted test: entire fallback chain health checks return 503 → HTTP 503 `{"error":"all_backends_exhausted","fallback_level": chain_length}`
  - [x] 28.6 Implement inference-failure-then-fallback test: primary inference mock returns HTTP 500 (triggering `InferenceError`), fallback inference mock returns 200 → HTTP 200, `fallback_level=1`
  - [x] 28.7 Implement IMF field preservation integration test: inbound IMF with non-null values in `trace_id`, `metadata`, `extensions`, `governance.*`, `user.*` → output IMF preserves all non-WRITE_SET fields unchanged
  - _Requirements: 1.1, 1.2, 1.6, 4.3, 4.5, 5.2, 6.3, 11.1, 11.2_


- [x] 29. Integration tests — OpenAI endpoint, startup validation, and downstream failures
  - [x] 29.1 Create `tests/integration/test_openai_endpoint.py`; implement happy path: valid `messages`, no `model` field → `routing_mode=auto` → pipeline succeeds → assert HTTP 200 OpenAI response shape (`id`, `object`, `model`, `choices[0].message.role="assistant"`, `choices[0].message.content` non-null, `usage` fields non-negative integers)
  - [x] 29.2 Implement pinned mode test: `model` field present → `routing_mode=pinned` → correct model selected; also test empty `messages` array → HTTP 422 with OpenAI error schema
  - [x] 29.3 Implement OpenAI pipeline error test: all backends exhausted → HTTP 503 with `{"error": {"code": 503, "message": ..., "type": "service_unavailable"}}`
  - [x] 29.4 Create `tests/integration/test_startup.py`: test lifespan with `MODEL_MATRIX_PATH` unset → `sys.exit(1)` with ERROR log; test `INFERENCE_TIMEOUT_SECONDS=0` → `sys.exit(1)`; test `INFERENCE_TIMEOUT_SECONDS=601` → `sys.exit(1)`; test `HEALTH_CHECK_TIMEOUT_SECONDS=31` → `sys.exit(1)`; test valid config → `app.state` has `classifier_rules`, `model_matrix`, `http_client` set; test YAML file not found → `sys.exit(1)`
  - [x] 29.5 Create `tests/integration/test_downstream_failures.py`: Audit Store unavailable (all audit POSTs return 503) → caller still gets correct response, WARNING logged, no ERROR to caller; Cache Layer lookup times out → `cache.lookup_hit=False`, inference proceeds, correct HTTP 200 returned; Cache write failure → WARNING logged, caller response unaffected
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 8.5, 8.6, 5.4, 7.3, 14.2, 14.3, 14.4, 14.7, 14.8_

- [x] 30. Integration tests — health endpoint and metrics
  - [x] 30.1 Create `tests/integration/test_health.py`: test `GET /health` returns 200 with correct `rules_loaded` and `models_loaded` counts when both configs loaded; test `GET /health` returns 503 with `"matrix_load_failed"` when model matrix is `None`; test `GET /health` requires no `X-API-Key` header; test `GET /health` makes no downstream calls (mock transport records zero calls)
  - [x] 30.2 Create `tests/integration/test_metrics.py`: after one successful route invocation, call `GET /metrics` on the metrics ASGI app directly via test client; assert `Content-Type: text/plain; version=0.0.4`; assert all five metric names present in response body: `llm_router_requests_total`, `llm_router_latency_seconds`, `llm_router_cache_hits_total`, `llm_router_fallbacks_total`, `llm_router_errors_total`
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 12.1_


- [x] 31. Helm chart — `llm-platform/charts/router/`
  - [x] 31.1 Create `llm-platform/charts/router/Chart.yaml` with `apiVersion: v2`, `name: router`, `description: Intelligent Router Layer 3 for the Enterprise LLM Platform (POC)`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`
  - [x] 31.2 Create `llm-platform/charts/router/values.yaml` with all required POC defaults per Requirements 16.3: `replicaCount: 1`, image fields, `service.type: ClusterIP`, `service.port: 8082`, all eight env vars, resources block, `observability.metrics.enabled: true`, `observability.metrics.port: 9090`, `autoscaling.enabled: false`, `vault.enabled: false`, `networkPolicy.enabled: false`
  - [x] 31.3 Create `llm-platform/charts/router/templates/_helpers.tpl` defining `router.fullname`, `router.name`, `router.chart`, `router.selectorLabels`, and `router.labels` template helpers following standard Helm conventions
  - [x] 31.4 Create `llm-platform/charts/router/templates/configmap.yaml` containing both `model_matrix.yaml` and `task_classifier_rules.yaml` as data keys, with the content from design section, named `{{ include "router.fullname" . }}-config`
  - [x] 31.5 Create `llm-platform/charts/router/templates/deployment.yaml`: single container with ports 8082 (`http`) and 9090 (`metrics`); all eight env vars from `values.yaml`; volume mount of ConfigMap at `/config` (readOnly); liveness and readiness probes pointing to `GET /health:8082` with `initialDelaySeconds: 15`, `periodSeconds: 15`, `timeoutSeconds: 5`, `failureThreshold: 3`; no HPA (POC)
  - [x] 31.6 Create `llm-platform/charts/router/templates/service.yaml`: ClusterIP Service exposing port 8082 (named `http`) and port 9090 (named `metrics`), with selector from `_helpers.tpl`
  - [x] 31.7 Create `llm-platform/charts/router/templates/networkpolicy.yaml`: conditional on `networkPolicy.enabled`; when enabled: ingress to port 8082 from pods with `app.kubernetes.io/name: security-layer` only; ingress to port 9090 from namespace `monitoring`; egress to ports 8086, 8087, 9200, 53
  - [x] 31.8 Create `llm-platform/charts/router/templates/servicemonitor.yaml`: `ServiceMonitor` targeting port `metrics`, path `/metrics`, `interval: 30s`, selector using `router.selectorLabels`
  - [x] 31.9 Create `llm-platform/charts/router/templates/hpa.yaml`: conditional on `autoscaling.enabled` (defaults to `false` for POC); when enabled exposes `minReplicas`, `maxReplicas`, `targetCPUUtilizationPercentage`
  - [x] 31.10 Create `llm-platform/charts/router/README.md` documenting: purpose, port layout (8082 API / 9090 metrics), all configurable values with types and defaults, ConfigMap config files, example `helm install` command
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8_


- [x] 32. Smoke tests and Helm lint
  - [x] 32.1 Create `tests/smoke/test_startup_smoke.py`: instantiate `create_app()` with valid config YAMLs; run through the lifespan; assert `app.state.classifier_rules` is not `None`, `app.state.model_matrix` is not `None`, `app.state.http_client` is not `None`; assert `GET /health` returns 200 with `{"status":"ok"}`, `rules_loaded > 0`, `models_loaded > 0`
  - [x] 32.2 Add startup-refusal smoke test: with `MODEL_MATRIX_PATH` unset or empty, assert lifespan raises `SystemExit`; with `AUDIT_STORE_URL` unset, assert `SystemExit`; with `INFERENCE_TIMEOUT_SECONDS=0`, assert `SystemExit`
  - [x] 32.3 Create `tests/smoke/test_helm.py`: run `helm lint llm-platform/charts/router/` via `subprocess.run` and assert exit code 0; run `helm template llm-platform/charts/router/ --set image.tag=test` and assert rendered output contains a `Deployment`, `Service`, `ConfigMap`, and `ServiceMonitor` resource; assert ConfigMap data contains `model_matrix.yaml` and `task_classifier_rules.yaml` keys
  - [x] 32.4 Add metrics endpoint smoke test: after one route request via test client, call the metrics ASGI app directly; assert `Content-Type: text/plain; version=0.0.4` and the five `llm_router_*` metric names are present in the body
  - _Requirements: 10.1, 12.1, 15.1, 16.1_

- [x] 33. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.


---

## Notes

- **POC constraints in effect:** No HPA, no Vault, no mTLS, no ML classifiers, no circuit breakers, no gRPC — all deferred to Phase 2. `autoscaling.enabled: false`, `vault.enabled: false`, `networkPolicy.enabled: false` in `values.yaml`.
- **Testing framework:** `pytest` + `hypothesis` (minimum 100 examples per PBT). HTTP test client is `httpx.AsyncClient` with `ASGITransport` — no real network required. All downstream services (Cache, Inference Adapter, Audit Store, Health endpoints) are mocked via `pytest-httpx` or `unittest.mock`.
- **Metrics isolation in tests:** Reset the Prometheus registry between test runs using a session-scoped fixture to prevent counter bleed across tests.
- **PBT tasks** are marked `[PBT]` in the task list. Each must have its status updated via `update_pbt_status` after the test run.
- **Startup validation** is enforced inside the FastAPI `lifespan` context manager (before `yield`) so the service refuses to start with a non-zero exit code if any required env var is missing, any config file is unreadable/malformed, or any numeric env var is out of range.
- **Separate ASGI apps:** The metrics app (`metrics_app.py`) runs on port 9090 independently of the main app on port 8082. Both are started in the `Dockerfile` CMD using `&` + `wait`.
- **Fire-and-forget pattern:** Both cache writes and all audit events are dispatched via FastAPI `BackgroundTask`. The caller response is returned before either completes. Failures are logged as WARNING and never re-raised.
- **Property numbering** maps directly to the 11 correctness properties defined in `design.md`. All 11 are covered by PBT sub-tasks in tasks 22–26.
- **Config YAMLs are mounted via ConfigMap** in the Helm chart at `/config/`. The `MODEL_MATRIX_PATH` and `TASK_RULES_PATH` env vars must match the mount path (`/config/model_matrix.yaml` and `/config/task_classifier_rules.yaml`).
- **Module naming:** The design specifies `intelligent_router/` as the package name. All imports follow `intelligent_router.<module>` conventions (e.g., `intelligent_router.task_classifier`, `intelligent_router.pipeline`).
- **Tasks marked with `*`** are optional and can be skipped for faster MVP iteration.


## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "3.1", "3.2"] },
    { "id": 2, "tasks": ["4.1", "4.2", "4.3", "4.4"] },
    { "id": 3, "tasks": ["5.1", "5.2", "5.3", "6.1", "6.2", "6.3", "6.4", "12.1", "12.2", "12.3", "12.4", "12.5"] },
    { "id": 4, "tasks": ["7.1", "8.1", "8.2", "9.1", "9.2", "10.1", "10.2", "11.1", "13.1", "13.2", "13.3"] },
    { "id": 5, "tasks": ["14.1", "14.2", "14.3", "14.4", "14.5", "20.1", "20.2"] },
    { "id": 6, "tasks": ["15.1", "15.2", "15.3", "16.1", "16.2", "16.3", "17.1", "17.2"] },
    { "id": 7, "tasks": ["18.1", "18.2", "18.3", "21.1", "21.2"] },
    { "id": 8, "tasks": ["22.1", "22.2", "22.3", "22.4", "23.1", "23.2", "23.3", "23.4"] },
    { "id": 9, "tasks": ["24.1", "24.2", "25.1", "25.2", "25.3", "25.4"] },
    { "id": 10, "tasks": ["26.1", "26.2", "26.3", "26.4", "26.5", "26.6", "26.7", "26.8"] },
    { "id": 11, "tasks": ["28.1", "28.2", "28.3", "28.4", "28.5", "28.6", "28.7"] },
    { "id": 12, "tasks": ["29.1", "29.2", "29.3", "29.4", "29.5", "30.1", "30.2"] },
    { "id": 13, "tasks": ["31.1", "31.2", "31.3", "31.4"] },
    { "id": 14, "tasks": ["31.5", "31.6", "31.7", "31.8", "31.9", "31.10"] },
    { "id": 15, "tasks": ["32.1", "32.2", "32.3", "32.4"] }
  ]
}
```
