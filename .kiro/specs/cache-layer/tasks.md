# Implementation Plan: Cache Layer

## Overview

This plan covers all implementation tasks for the Cache Layer (Layer 4) — a FastAPI microservice (port 8086) that provides exact-match and semantic caching for the LLM platform's Intelligent Router. All durable state lives in Redis; the service is stateless at the application tier. Two caching strategies are implemented: SHA-256 keyed exact-match caching and cosine-similarity semantic caching over sentence-transformer embeddings stored as Redis Lists.

Tasks follow the module structure defined in design.md. Foundation pieces (dependencies, config, schemas, exceptions) are sequenced first. Service implementations (EmbeddingGenerator, ExactCacheService, SemanticCacheService, cache key helper) come next. Routers and middleware depend on the services. The application factory wires everything together. Prometheus metrics, the Dockerfile, and the Helm chart are parallel deliverables once the factory is complete. Tests are last and can be written in parallel with each other once the application is fully wired.

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2"] },
    { "wave": 3, "tasks": ["3", "4"] },
    { "wave": 4, "tasks": ["5", "6", "7"] },
    { "wave": 5, "tasks": ["8"] },
    { "wave": 6, "tasks": ["9", "10", "11"] },
    { "wave": 7, "tasks": ["12"] },
    { "wave": 8, "tasks": ["13", "14", "15"] },
    { "wave": 9, "tasks": ["16", "17", "18"] }
  ]
}
```

> Wave 3 tasks (Schemas, Exceptions) both depend on Task 2 (Config) and can be implemented in parallel.
> Wave 4 tasks (EmbeddingGenerator, ExactCacheService, SemanticCacheService) all depend on Wave 3 and can be implemented in parallel.
> Wave 6 tasks (Health Router, Cache Router, LoggingMiddleware) all depend on Wave 5 (cache key helper) and can be implemented in parallel.
> Wave 8 tasks (Prometheus Metrics, Dockerfile, Helm Chart) all depend on Task 12 (App Factory) and can be implemented in parallel.
> Wave 9 tasks (Unit Tests, Property-Based Tests, Integration Test) all depend on Task 12 and can be implemented in parallel.

---

## Tasks

- [x] 1. Add cache_service dependencies to requirements.txt
  - Append the following pinned packages to the root `requirements.txt` (do not remove existing entries):
    - `sentence-transformers==3.3.1`
    - `redis[asyncio]==5.2.1`
    - `fakeredis[aioredis]==2.26.2`
    - `prometheus-client==0.21.1`
    - `pytest-asyncio==0.24.0`
  - Confirm `hypothesis==6.111.2` is already present; add it if missing.
  - Create `cache_service/__init__.py` (empty) to establish the package.
  - Create `cache_service/services/__init__.py`, `cache_service/routers/__init__.py`,
    `cache_service/schemas/__init__.py`, `cache_service/middleware/__init__.py` (all empty).
  - Verify: `pip install -r requirements.txt --dry-run` exits without error.
  - **Validates: Requirements 6.7** (establishes the package structure for all modules)

- [x] 2. Implement `cache_service/config.py` — Pydantic BaseSettings
  - Create `cache_service/config.py`.
  - Define `Settings(BaseSettings)` reading all values from environment variables with no hardcoded values. All fields and their env vars, defaults, and validation constraints:
    - `redis_url: str = "redis://redis:6379"` — env `REDIS_URL`; non-empty string
    - `similarity_threshold: float = Field(0.90, ge=0.0, le=1.0)` — env `SIMILARITY_THRESHOLD`
    - `max_semantic_entries: int = Field(500, gt=0)` — env `MAX_SEMANTIC_ENTRIES`
    - `embedding_model: str = "all-MiniLM-L6-v2"` — env `EMBEDDING_MODEL`
    - `log_level: str = "INFO"` — env `LOG_LEVEL`; invalid values normalised to `"INFO"` at runtime in consuming code
    - `port: int = Field(8086, ge=1, le=65535)` — env `PORT`; Pydantic raises `ValidationError` for out-of-range values, which causes startup failure
    - `ttl_chat: int = Field(3600, gt=0)` — env `TTL_CHAT`
    - `ttl_code: int = Field(7200, gt=0)` — env `TTL_CODE`
    - `ttl_summarization: int = Field(86400, gt=0)` — env `TTL_SUMMARIZATION`
  - Set `model_config = {"env_prefix": "", "case_sensitive": False}`.
  - Expose `@lru_cache` factory `get_settings() -> Settings`.
  - **Validates: Requirements 6.9, 4.2, 4.4, 5.5, 5.6, 6.8**

- [x] 3. Implement `cache_service/schemas/` — IMF and cache Pydantic models
  - Create `cache_service/schemas/imf.py` with:
    - `IMFMessage(BaseModel)`: `role: str`, `content: str`
    - `IMFUsage(BaseModel)`: `prompt_tokens: int`, `completion_tokens: int`, `total_tokens: int`
    - `IMFResponse(BaseModel)`: `content: str | None = None`, `finish_reason: str | None = None`, `usage: IMFUsage | None = None`
    - `IMFGovernance(BaseModel)`: `pii_fields_detected: list[str] = []`; other governance fields optional
    - `IMFRouting(BaseModel)`: `selected_model: str`, `routing_mode: str | None = None`, `fallback_level: int = 0`
    - `IMFRequest(BaseModel)`: `messages: list[IMFMessage]`, `task_type: str`, `model: str | None = None`, `stream: bool = False`, `max_tokens: int | None = None`, `temperature: float | None = None`
    - `IMFDocument(BaseModel)`: `request_id: str | None = None`, `request: IMFRequest`, `routing: IMFRouting`, `response: IMFResponse | None = None`, `governance: IMFGovernance = Field(default_factory=IMFGovernance)`, `cache: dict | None = None`
  - Create `cache_service/schemas/cache.py` with:
    - `CacheBlock(BaseModel)`: `lookup_hit: bool`, `cache_key: str`, `cache_type: Literal["exact", "semantic"] | None`, `similarity_score: float | None = None`
    - `LookupResponse(BaseModel)`: `hit: bool`, `cache_key: str`, `cache_type: Literal["exact", "semantic"] | None`, `response: IMFResponse | None`, `similarity_score: float | None = None`
    - `WriteResponse(BaseModel)`: `written: bool`, `cache_key: str`
  - **Validates: Requirements 1.1, 2.2, 3.1–3.6, 10.1–10.4**

- [x] 4. Implement `cache_service/exceptions.py` — custom exception hierarchy
  - Create `cache_service/exceptions.py` defining:
    - `CacheServiceError(Exception)` — base exception; accepts optional `message: str`
    - `RedisUnavailableError(CacheServiceError)` — raised on `redis.RedisError` in service calls; accepts optional `operation: str` kwarg (e.g. `"read"` or `"write"`)
    - `EmbeddingLoadError(CacheServiceError)` — raised when `SentenceTransformer` fails to load
    - `EmbeddingEncodeError(CacheServiceError)` — raised when `encode()` raises any exception
  - All exceptions should store their kwargs as instance attributes for use in structured log entries.
  - **Validates: Requirements 1.8, 1.9, 2.8, 2.9, 4.7, 6.1**


- [x] 5. Implement `cache_service/services/embedding.py` — EmbeddingGenerator
  - Create `cache_service/services/embedding.py`.
  - Implement `EmbeddingGenerator`:
    - `__init__(self, model_name: str)` — stores `model_name`; does NOT load the model; sets `self._model = None`
    - `load(self) -> None` — instantiates `SentenceTransformer(model_name, device="cpu")`; stores on `self._model`; wraps any exception in `EmbeddingLoadError`
    - `encode(self, text: str) -> list[float]` — calls `self._model.encode(text).tolist()`; returns 384-element float list; wraps any exception in `EmbeddingEncodeError`
    - `is_loaded(self) -> bool` — returns `self._model is not None`
  - CPU-only inference; no GPU dependency. Model loaded once at startup via `load()`.
  - **Validates: Requirements 5.1, 5.2, 6.1, 1.4, 2.5**

- [x] 6. Implement `cache_service/services/exact_cache.py` — ExactCacheService
  - Create `cache_service/services/exact_cache.py`.
  - Implement `ExactCacheService`:
    - `__init__(self, redis_client)` — stores the async Redis client as `self._redis`
    - `async get(self, cache_key: str) -> dict | None`:
      - Executes `await self._redis.get(f"exact:{cache_key}")`.
      - Returns `json.loads(raw)` if the key exists, or `None` on cache miss.
      - Catches `redis.RedisError` and raises `RedisUnavailableError(operation="read")`.
    - `async set(self, cache_key: str, response: dict, ttl: int) -> None`:
      - Executes `await self._redis.set(f"exact:{cache_key}", json.dumps(response), ex=ttl)`.
      - Catches `redis.RedisError` and raises `RedisUnavailableError(operation="write")`.
  - All serialisation is UTF-8 JSON. Deserialization must not coerce types.
  - **Validates: Requirements 4.1, 4.3, 4.7, 10.2, 10.4, 1.3, 2.4**

- [x] 7. Implement `cache_service/services/semantic_cache.py` — SemanticCacheService
  - Create `cache_service/services/semantic_cache.py`.
  - Implement `SemanticCacheService`:
    - `__init__(self, redis_client, settings: Settings)`
    - `@staticmethod _cosine_similarity(a: list[float], b: list[float]) -> float`:
      - Pure computation; no I/O. Returns `dot(a, b) / (norm(a) * norm(b))`.
      - Returns `0.0` if either vector has zero norm.
    - `async lookup(self, task_type: str, query_embedding: list[float]) -> tuple[dict, float] | None`:
      - Executes `LRANGE semantic_cache:{task_type} 0 -1`.
      - On empty list: returns `None` immediately.
      - Deserialises each JSON element `{"key", "embedding", "response"}`.
      - Computes `_cosine_similarity` for each entry vs `query_embedding`.
      - Returns `(response_dict, best_score)` for the highest-scoring entry where `score >= settings.similarity_threshold`, or `None` if none qualify.
      - When multiple entries tie at the highest score, returns any one of them.
      - Catches `redis.RedisError` and raises `RedisUnavailableError(operation="read")`.
    - `async write(self, task_type: str, cache_key: str, embedding: list[float], response: dict) -> bool`:
      - Calls `get_entry_count(task_type)` first.
      - If count >= `settings.max_semantic_entries`: returns `False` (caller logs `semantic_cache_full`).
      - Otherwise: `RPUSH semantic_cache:{task_type} json.dumps({"key": cache_key, "embedding": embedding, "response": response})`; returns `True`.
      - Catches `redis.RedisError` and raises `RedisUnavailableError(operation="write")`.
    - `async get_entry_count(self, task_type: str) -> int`:
      - Executes `LLEN semantic_cache:{task_type}`; returns integer result.
      - Catches `redis.RedisError` and raises `RedisUnavailableError(operation="read")`.
  - **Validates: Requirements 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 2.5, 2.6, 1.4, 1.5**

- [x] 8. Implement `make_cache_key` helper in `cache_service/routers/cache.py`
  - Create `cache_service/routers/cache.py` beginning with the standalone helper function:
    ```python
    import hashlib

    def make_cache_key(messages: list[dict], model: str, task_type: str) -> str:
        content = " ".join(m["content"].strip() for m in messages).lower().strip()
        raw = f"{content}|{model}|{task_type}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    ```
  - This function is the single source of truth for cache key derivation; all other code imports from here.
  - Leave space for the router implementation (Task 10) to be added to this same file.
  - **Validates: Requirements 1.2, 2.3, 10.1**


- [x] 9. Implement `cache_service/routers/health.py` — Health Router
  - Create `cache_service/routers/health.py`.
  - Declare module-level `_ready: bool = False` and `_startup_failure_reason: str | None = None`.
  - Implement `GET /health` with the following state machine:
    - `_ready == False` → HTTP 503 `{"status": "starting"}`
    - `_startup_failure_reason == "embedding_model_load_failed"` → HTTP 503 `{"status": "unavailable", "reason": "embedding_model_load_failed"}`
    - Otherwise: attempt `await request.app.state.redis.ping()`:
      - Succeeds → HTTP 200 `{"status": "ok"}`
      - Raises any exception OR `app.state.redis` is `None` → HTTP 503 `{"status": "unavailable", "reason": "redis_unreachable"}`
  - No authentication required on this endpoint.
  - **Validates: Requirements 6.2, 6.3, 6.4, 6.5**

- [x] 10. Complete `cache_service/routers/cache.py` — Cache Router with lookup and write endpoints
  - Extend the file from Task 8 to add a full `APIRouter(prefix="/cache")`.
  - **`POST /cache/lookup`**:
    - Accepts `IMFDocument`; FastAPI/Pydantic returns 422 automatically for missing `request.messages`, `routing.selected_model`, or `request.task_type`.
    - Derives `cache_key = make_cache_key(request.messages, routing.selected_model, request.task_type)`.
    - Derives `prompt_text` as the same normalised content string used for key derivation.
    - Calls `ExactCacheService.get(cache_key)`:
      - HIT → build `LookupResponse(hit=True, cache_type="exact", similarity_score=None, ...)` and return immediately; log `cache_hit` with `cache_type="exact"`.
      - `RedisUnavailableError` before any hit → return miss response (HTTP 200, `hit=False`, `cache_type=None`); log `redis_unavailable`; increment `llm_cache_errors_total{error_code="redis_unavailable", operation="lookup"}`.
    - On exact MISS: call `EmbeddingGenerator.encode(prompt_text)`:
      - `EmbeddingEncodeError` → return miss response (HTTP 200); log `embedding_error`; increment `llm_cache_errors_total{error_code="embedding_error", operation="lookup"}`.
    - On successful encode: call `SemanticCacheService.lookup(task_type, embedding)`:
      - HIT (not None) → return `LookupResponse(hit=True, cache_type="semantic", similarity_score=score, ...)`.
      - MISS (None) → return miss response.
    - On all outcomes: update `llm_cache_requests_total` and observe `llm_cache_latency_seconds`.
    - Build `CacheBlock` wholesale replacement in the response payload (`lookup_hit`, `cache_key`, `cache_type`, `similarity_score`).
    - Log `cache_hit` or `cache_miss` structured event as required by Requirement 7.
  - **`POST /cache/write`**:
    - Accepts `IMFDocument`. Returns HTTP 422 with `{"event": "cache_write_invalid", "request_id": ..., "reason": "response field null or absent"}` if `imf.response` is null/absent.
    - Validates required fields; 422 if missing.
    - Derives `cache_key`, `prompt_text`, and TTL from task_type (`ttl_chat`, `ttl_code`, `ttl_summarization`; default 3600).
    - Calls `ExactCacheService.set(cache_key, response_dict, ttl)`:
      - `RedisUnavailableError` → HTTP 503 `{"event": "redis_unavailable", "request_id": ..., "operation": "write"}`; log and increment `llm_cache_errors_total`.
    - Calls `EmbeddingGenerator.encode(prompt_text)`:
      - `EmbeddingEncodeError` → log `embedding_error`; skip semantic write; still return HTTP 200 `WriteResponse(written=True, cache_key=...)`.
    - On successful encode: calls `SemanticCacheService.write(task_type, cache_key, embedding, response_dict)`:
      - Returns `False` → log `semantic_cache_full` with `task_type` and `request_id`.
    - After any semantic write attempt: update `llm_cache_semantic_entries{task_type}` gauge via `get_entry_count(task_type)`.
    - On success: return HTTP 200 `WriteResponse(written=True, cache_key=cache_key)`.
    - Observe `llm_cache_latency_seconds` and log `cache_write` event.
  - Retrieve services from `request.app.state` (set during lifespan in Task 12).
  - **Validates: Requirements 1.1–1.10, 2.1–2.10, 3.1–3.6, 4.1–4.7, 5.3–5.8, 7.1–7.4, 9.2–9.5**

- [x] 11. Implement `cache_service/middleware/logging.py` — LoggingMiddleware
  - Create `cache_service/middleware/logging.py`.
  - Implement `LoggingMiddleware(BaseHTTPMiddleware)` mirroring the `model_registry` reference implementation, with these cache-specific additions:
    - `request_id` extraction priority (before calling `call_next`):
      1. Read the raw request body; attempt JSON parse and read `body.get("request_id")`.
      2. Fall back to `request.headers.get("X-Request-ID")`.
      3. Fall back to `"unknown"`.
      - Re-inject the body bytes into `request.scope["_body"]` (or equivalent Starlette pattern) so downstream handlers can still read it.
    - Emit one JSON line to stdout per request containing: `timestamp` (ISO-8601 UTC + "Z"), `level` (`"INFO"` < 500, `"ERROR"` ≥ 500), `method`, `path`, `status_code`, `latency_ms` (rounded to 2 dp), `request_id`.
    - **Never** include in any log entry:
      - Any key whose name appears in `governance.pii_fields_detected` (if body is parseable).
      - The raw string value of any `request.messages[].content` field.
    - Respect `LOG_LEVEL` from `get_settings()`; invalid values treated as `"INFO"` priority.
    - If stdout write raises any exception: silently discard the entry and continue.
  - **Validates: Requirements 6.6, 7.5, 7.6, 7.7**


- [x] 12. Implement `cache_service/main.py` — Application Factory
  - Create `cache_service/main.py`.
  - Define `async lifespan(app: FastAPI)` context manager:
    1. `settings = get_settings()`.
    2. Connect to Redis: `redis_client = redis.asyncio.from_url(settings.redis_url)`; store on `app.state.redis`. On any connection error: log structured error `{"event": "redis_connection_failed"}`, set `health._startup_failure_reason = "redis_unreachable"`, set `app.state.redis = None`; continue — do NOT exit.
    3. Instantiate `EmbeddingGenerator(settings.embedding_model)` and call `.load()`; store on `app.state.embedding_generator`. On `EmbeddingLoadError`: log structured error `{"event": "embedding_load_failed"}`, set `health._startup_failure_reason = "embedding_model_load_failed"`; continue.
    4. Instantiate `ExactCacheService(app.state.redis)` and store on `app.state.exact_cache`.
    5. Instantiate `SemanticCacheService(app.state.redis, settings)` and store on `app.state.semantic_cache`.
    6. Set `health._ready = True`.
    7. `yield` (service is running).
    8. Shutdown: if `app.state.redis` is not None, `await app.state.redis.aclose()`.
  - Create `app = FastAPI(title="Cache Service", version="0.1.0", lifespan=lifespan)`.
  - Add `app.add_middleware(LoggingMiddleware)`.
  - Include `health_router` and `cache_router`.
  - Mount Prometheus metrics: start a secondary `uvicorn` server in the lifespan that serves `make_asgi_app()` on `settings.port + 1` (i.e., 9090 when port is 8086), OR mount `prometheus_client.make_asgi_app()` as a sub-application accessible via a dedicated port configuration.
  - `if __name__ == "__main__": uvicorn.run("cache_service.main:app", host="0.0.0.0", port=settings.port)`.
  - **Validates: Requirements 6.1, 6.2–6.5, 6.8, 9.1**

- [x] 13. Implement Prometheus metrics registry in `cache_service/metrics.py`
  - Create `cache_service/metrics.py`.
  - Register all four metrics at module import time using `prometheus_client`:
    - `cache_requests_total = Counter("llm_cache_requests_total", "Total cache lookup requests", ["status", "cache_type", "task_type"])`
    - `cache_latency_seconds = Histogram("llm_cache_latency_seconds", "End-to-end handler latency", ["operation", "task_type"], buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5])`
    - `cache_errors_total = Counter("llm_cache_errors_total", "Redis and embedding failures", ["error_code", "operation"])`
    - `cache_semantic_entries = Gauge("llm_cache_semantic_entries", "Current semantic cache list length", ["task_type"])`
  - Label conventions:
    - `llm_cache_requests_total`: `status` ∈ `{hit, miss}`; `cache_type` ∈ `{exact, semantic, none}` (use `none` for misses); `task_type` = raw IMF value.
    - `llm_cache_errors_total`: `error_code` ∈ `{redis_unavailable, embedding_error}`; `operation` ∈ `{lookup, write}`.
  - This module has no I/O; safe to import anywhere.
  - The cache router (Task 10) imports and updates these metrics.
  - **Validates: Requirements 9.1–9.5**

- [x] 14. Create `cache_service/Dockerfile`
  - Create `cache_service/Dockerfile`:
    - Base image: `python:3.12-slim`
    - `WORKDIR /app`
    - Copy `requirements.txt`; run `pip install --no-cache-dir -r requirements.txt`
    - Copy `cache_service/` into `/app/cache_service/`
    - `EXPOSE 8086 9090`
    - `ENV PORT=8086 LOG_LEVEL=INFO REDIS_URL=redis://redis:6379 EMBEDDING_MODEL=all-MiniLM-L6-v2`
    - `CMD ["uvicorn", "cache_service.main:app", "--host", "0.0.0.0", "--port", "8086"]`
  - **Validates: Requirement 8** (containerisation prerequisite for Helm deployment)


- [x] 15. Create Helm chart `llm-platform/charts/cache/`
  - Create all files mirroring the `model-registry` chart structure.
  - **`Chart.yaml`**: `apiVersion: v2`, `name: cache`, `description: "Cache Layer (Layer 4) — exact and semantic caching for the LLM platform"`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`. Add `dependencies` for `bitnami/redis` version `19.x` from `https://charts.bitnami.com/bitnami`.
  - **`values.yaml`** — POC defaults:
    - `replicaCount: 1`; `image.repository: registry.local/cache-service`; `image.tag: ""`; `image.pullPolicy: IfNotPresent`
    - `service.type: ClusterIP`; `service.port: 8086`; `metricsPort: 9090`
    - `env.LOG_LEVEL: "INFO"`, `env.REDIS_URL: "redis://{{ .Release.Name }}-redis-master:6379"`, `env.SIMILARITY_THRESHOLD: "0.90"`, `env.EMBEDDING_MODEL: "all-MiniLM-L6-v2"`, `env.MAX_SEMANTIC_ENTRIES: "500"`, `env.TTL_CHAT: "3600"`, `env.TTL_CODE: "7200"`, `env.TTL_SUMMARIZATION: "86400"`
    - `redis.enabled: true`, `redis.architecture: standalone`, `redis.auth.enabled: false`, `redis.master.persistence.enabled: true`, `redis.master.persistence.size: 5Gi`
    - `resources.requests.cpu: "200m"`, `resources.requests.memory: "512Mi"`, `resources.limits.cpu: "1"`, `resources.limits.memory: "1Gi"`
    - `autoscaling.enabled: false`; `vault.enabled: false`
    - `livenessProbe` and `readinessProbe` both: `httpGet.path: /health`, `httpGet.port: 8086`, `initialDelaySeconds: 15`, `periodSeconds: 15`, `timeoutSeconds: 2`, `failureThreshold: 3`
  - **`templates/_helpers.tpl`**: define `cache.fullname`, `cache.labels`, `cache.selectorLabels` following standard Helm conventions.
  - **`templates/deployment.yaml`**: single container; ports 8086 (name `http`) and 9090 (name `metrics`); all `env.*` values injected as env vars; image tag defaults to `"latest"` when `.Values.image.tag` is empty; liveness/readiness from values; resources from values.
  - **`templates/service.yaml`**: ClusterIP; port 8086 named `http`; port 9090 named `metrics`.
  - **`templates/networkpolicy.yaml`**: `podSelector` matching cache pods; Ingress: allow only from pods with `app.kubernetes.io/name: router` in namespace `llm-platform`; Egress: allow only to pods with `app.kubernetes.io/name: redis` in namespace `llm-platform`.
  - **`templates/servicemonitor.yaml`**: `ServiceMonitor` targeting port `metrics` (9090), `path: /metrics`, `interval: 30s`, `namespaceSelector` restricted to `.Release.Namespace`.
  - **`README.md`**: brief description, values reference table, `helm dependency update` command.
  - **Validates: Requirements 8.1–8.7, 9.1, 9.5**

- [x] 16. Write unit tests in `tests/cache_service/`
  - Create `tests/cache_service/__init__.py` (empty).
  - Create `tests/cache_service/conftest.py`:
    - Register the `hypothesis` `"ci"` profile: `settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])`; `settings.load_profile("ci")`.
    - `fake_redis` async fixture using `fakeredis.aioredis.FakeRedis(decode_responses=False)`.
    - `mock_embedding_generator` fixture: returns an `EmbeddingGenerator`-like instance whose `encode()` method returns a deterministic 384-dimensional float list (all values `1.0 / math.sqrt(384)` for unit normalisation) without loading a real model.
    - `app_client` async fixture: builds the real FastAPI app with lifespan stubbed to inject `fake_redis` and `mock_embedding_generator` into `app.state`; yields `httpx.AsyncClient` via `ASGITransport`.
  - **`tests/cache_service/test_config.py`**: `test_defaults`, `test_env_override`, `test_invalid_log_level_treated_as_info`, `test_port_out_of_range_raises_validation_error`.
  - **`tests/cache_service/test_embedding.py`**: `test_encode_returns_384_dims` (mocked transformer), `test_encode_failure_raises_embedding_encode_error`, `test_load_failure_raises_embedding_load_error`, `test_is_loaded_false_before_load`, `test_is_loaded_true_after_load`.
  - **`tests/cache_service/test_exact_cache.py`** (uses `fake_redis`): `test_get_hit`, `test_get_miss`, `test_set_and_get_roundtrip_preserves_types`, `test_set_ttl_chat`, `test_set_ttl_code`, `test_set_ttl_summarization`, `test_get_redis_error_raises_redis_unavailable`, `test_set_redis_error_raises_redis_unavailable`.
  - **`tests/cache_service/test_semantic_cache.py`** (uses `fake_redis`): `test_lookup_empty_list_returns_none`, `test_lookup_above_threshold_returns_hit`, `test_lookup_below_threshold_returns_none`, `test_lookup_returns_highest_scoring_entry`, `test_write_below_capacity_returns_true`, `test_write_at_capacity_returns_false`, `test_write_increments_llen`, `test_cosine_similarity_identical_vectors`, `test_cosine_similarity_zero_vector`.
  - **`tests/cache_service/test_cache_key.py`**: `test_same_inputs_same_key`, `test_whitespace_normalised`, `test_different_model_different_key`, `test_different_task_type_different_key`, `test_output_is_64_char_hex_string`.
  - **`tests/cache_service/test_cache_router.py`** (uses `app_client`): `test_lookup_exact_hit`, `test_lookup_semantic_hit`, `test_lookup_miss`, `test_lookup_missing_messages_returns_422`, `test_lookup_missing_selected_model_returns_422`, `test_lookup_missing_task_type_returns_422`, `test_write_success`, `test_write_null_response_returns_422`, `test_write_redis_unavailable_returns_503`, `test_cache_block_has_exactly_four_keys`, `test_cache_block_wholesale_replacement`.
  - **`tests/cache_service/test_health.py`** (uses `app_client`): `test_starting_returns_503`, `test_ready_redis_ok_returns_200`, `test_ready_redis_unreachable_returns_503`, `test_embedding_load_failed_returns_503`.
  - **`tests/cache_service/test_logging.py`** (uses `app_client`): `test_log_entry_contains_required_fields`, `test_request_id_from_imf_body`, `test_request_id_from_header_fallback`, `test_request_id_unknown_fallback`, `test_pii_field_names_not_in_log`, `test_message_content_not_in_log`.
  - **Validates: All requirements (unit-level coverage)**


- [ ] 17. Write property-based tests in `tests/cache_service/test_properties.py` [PBT]
  - Use `@given` + `@settings(max_examples=100, deadline=500)` for all properties.
  - Use `fakeredis` and deterministic or Hypothesis-generated embeddings throughout. No live Redis or real sentence-transformer model.
  - Annotate each test with `# Validates: Requirements X.Y` in the docstring.

  - **Property 1 — Cache Key Determinism** (`test_cache_key_determinism`):
    - `@given(messages=st.lists(st.builds(dict, role=st.just("user"), content=st.text()), min_size=1), model=st.text(min_size=1), task_type=st.sampled_from(["chat","code","summarization"]))`
    - Generate a second `messages` list with padded whitespace on each content; assert `make_cache_key` returns the same hex for both. Also assert unrelated IMF fields do not change the key.
    - `# Validates: Requirements 1.2, 3.1, 10.1`

  - **Property 2 — Exact Cache Round-Trip** (`test_exact_cache_round_trip`):
    - Strategy generates random `IMFResponse`-like dicts with `content` (text), `finish_reason` (one of `stop|length|null`), `usage` (random non-negative ints for each token field).
    - Write via `ExactCacheService.set` then read via `ExactCacheService.get` with `fakeredis`.
    - Assert every field matches by type and value; no fields added, removed, or coerced.
    - `# Validates: Requirements 1.3, 2.3, 10.2, 10.3, 10.4`

  - **Property 3 — IMF Cache Block Completeness and Wholesale Replacement** (`test_cache_block_completeness_and_replacement`):
    - Strategy generates `lookup_result` ∈ `{exact_hit, semantic_hit, miss}` and an arbitrary `incoming_cache_block` dict with random keys/values.
    - Construct the `CacheBlock` via the pure logic in the router (extracted helper or via test client).
    - Assert output has exactly the keys `{lookup_hit, cache_key, cache_type, similarity_score}`.
    - Assert no values from `incoming_cache_block` survive.
    - Assert `similarity_score` is `None` for exact hits/misses; a float in `(0.0, 1.0]` for semantic hits.
    - `# Validates: Requirements 3.3, 3.4, 3.5, 3.6`

  - **Property 4 — Semantic Hit Threshold Consistency** (`test_semantic_threshold_consistency`):
    - `@given(entries=st.lists(semantic_entry_strategy(), min_size=0, max_size=20), query=st.lists(st.floats(min_value=-1, max_value=1, allow_nan=False), min_size=384, max_size=384), threshold=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))`
    - Pre-populate `fakeredis` with `entries`; call `SemanticCacheService.lookup`.
    - Independently compute cosine similarity for all entries and find the max.
    - Assert: result is a hit iff `max_similarity >= threshold AND max_similarity > 0`.
    - Assert: the returned entry has the strictly highest cosine similarity when multiple entries exceed the threshold.
    - `# Validates: Requirements 1.5, 5.3, 5.5, 5.7`

  - **Property 5 — Semantic Capacity Guard** (`test_semantic_capacity_guard`):
    - `@given(write_count=st.integers(min_value=495, max_value=510))`
    - Execute `write_count` writes via `SemanticCacheService.write` plus corresponding `ExactCacheService.set` calls on `fakeredis`.
    - Assert: `LLEN semantic_cache:{task_type}` ≤ 500 after all writes.
    - Assert: every write beyond index 500 returned `False` from `semantic_cache.write`.
    - Assert: `ExactCacheService.set` succeeded for every write (including those where semantic was skipped).
    - `# Validates: Requirements 2.5, 2.6, 2.9, 5.6, 5.8`

  - **Property 6 — Graceful Degradation on Infrastructure Failure** (`test_graceful_degradation_redis_unavailable`, `test_graceful_degradation_embedding_failure`):
    - `@given(imf=imf_lookup_strategy())` for both tests.
    - Test A: patch `fakeredis.get` to raise `redis.ConnectionError` before any result; POST `/cache/lookup`; assert HTTP 200, `hit=False`, `cache_type=None`.
    - Test B: patch `EmbeddingGenerator.encode` to raise `EmbeddingEncodeError`; POST `/cache/lookup`; assert HTTP 200, `hit=False`.
    - `# Validates: Requirements 1.7, 1.8, 1.9, 4.7`

  - **Property 7 — Log Entry Field Invariant** (`test_log_entry_field_invariant`):
    - `@given(event_type=st.sampled_from(["hit_exact", "hit_semantic", "miss", "write"]))`
    - Capture stdout during a request that produces the declared event type.
    - Parse emitted JSON log line(s).
    - Assert: exact hits have `cache_type: "exact"` and `similarity_score: null`; semantic hits have `cache_type: "semantic"` and `similarity_score > 0`; miss entries have `event: "cache_miss"`, `request_id`, `latency_ms >= 0`; write entries have `event: "cache_write"`, `request_id`, `cache_key`, `task_type`, `latency_ms >= 0`.
    - `# Validates: Requirements 7.1, 7.2, 7.3`

  - **Property 8 — PII Exclusion from All Log Entries** (`test_pii_exclusion_from_logs`):
    - `@given(pii_fields=st.lists(st.text(min_size=1), min_size=1, max_size=5), messages=st.lists(st.text(min_size=1), min_size=1))`
    - Build `IMFDocument` with `governance.pii_fields_detected = pii_fields` and `request.messages[i].content = messages[i]`.
    - POST `/cache/lookup`; capture stdout; parse all emitted JSON lines.
    - Assert: no emitted entry contains any key whose name is in `pii_fields`.
    - Assert: no emitted entry contains the raw string of any message content value.
    - `# Validates: Requirements 7.6`

  - **Property 9 — Input Validation Rejection** (`test_validation_rejection_missing_fields`):
    - `@given(missing_fields=st.frozensets(st.sampled_from(["messages", "selected_model", "task_type"]), min_size=1))`
    - Build an `IMFDocument` with the specified fields removed/set to None.
    - POST to both `/cache/lookup` and `/cache/write`.
    - Assert: HTTP 422 returned in both cases.
    - Assert: `fakeredis` has no keys written.
    - Assert: no `cache_hit`, `cache_miss`, or `cache_write` log event emitted.
    - `# Validates: Requirements 1.10, 2.10`

  - **Property 10 — Metrics Correctness Invariant** (`test_metrics_correctness`):
    - `@given(operations=st.lists(cache_operation_strategy(), min_size=1, max_size=20))`
    - `cache_operation_strategy()` generates dicts with `op_type ∈ {lookup, write}`, `task_type ∈ {chat, code, summarization}`, `will_hit: bool`.
    - Execute each operation via `app_client`, pre-populating or emptying `fakeredis` to produce the declared hit/miss outcome.
    - Query `prometheus_client`'s registry directly via `REGISTRY.get_sample_value()` after all operations.
    - Assert: `llm_cache_requests_total` for each `(status, cache_type, task_type)` combo equals the observed count.
    - Assert: `llm_cache_semantic_entries{task_type}` equals `LLEN semantic_cache:{task_type}` in `fakeredis`.
    - `# Validates: Requirements 9.2, 9.4, 9.5`

  - **Validates: Design document correctness properties 1–10**

- [ ] 18. Write integration test `tests/cache_service/test_integration.py`
  - Decorate the entire module to skip when `REDIS_URL` env var is not set:
    ```python
    pytestmark = pytest.mark.skipif(
        not os.getenv("REDIS_URL"), reason="requires live Redis (set REDIS_URL)"
    )
    ```
  - Uses a real `redis.asyncio` client connecting to `REDIS_URL`.
  - Uses a real `EmbeddingGenerator` loaded with `all-MiniLM-L6-v2`.
  - **`test_full_lookup_miss_write_hit_roundtrip`**: flush Redis (FLUSHDB); POST `/cache/lookup` → assert `hit=False`; POST `/cache/write` with same IMF + valid response; POST `/cache/lookup` again → assert `hit=True, cache_type="exact"`; assert returned response matches written response field-by-field.
  - **`test_semantic_hit_after_write`**: flush Redis; write one entry; POST `/cache/lookup` with semantically similar but not byte-identical messages; assert `hit=True, cache_type="semantic"`.
  - **`test_ttl_applied_for_chat`**: write a `chat` entry; inspect Redis TTL on `exact:{key}`; assert TTL ∈ `[3599, 3600]`.
  - **`test_health_returns_ok_with_live_redis`**: GET `/health`; assert HTTP 200 `{"status": "ok"}`.
  - **Validates: Requirements 1.1–1.6, 2.1–2.5, 4.1–4.4, 10.1–10.4 (end-to-end)**


## Notes

- **POC constraints apply throughout**: no Milvus, no Redis Sentinel/Cluster, no Vault, no HPA, no Istio mTLS, no gRPC. All deferred to Phase 2 per the platform master contract.
- **Single source of truth for cache key**: `make_cache_key` is defined once in `cache_service/routers/cache.py` (Task 8). Both the lookup and write handlers import from that same location. No other code re-implements the key derivation.
- **Startup failure semantics (Tasks 9 and 12)**: Redis connection failure and embedding model load failure at startup do NOT terminate the process. The service continues running with `_ready = True` but with `_startup_failure_reason` set. The health endpoint exposes the reason, keeping the pod in `NotReady` state in Kubernetes rather than crash-looping.
- **Middleware ordering (Task 12)**: `LoggingMiddleware` is added before the routers so it wraps all endpoint calls and can capture the true final status code including 422/503 error responses.
- **Body re-injection in LoggingMiddleware (Task 11)**: The middleware must read the request body to extract `request_id` before passing control to the endpoint. Use Starlette's `receive` override pattern or cache the body bytes on `request.state` to avoid consuming the body stream before the endpoint handler reads it.
- **Prometheus port separation (Task 12 and 13)**: The Prometheus `/metrics` endpoint runs on port 9090, separate from the application port 8086. The preferred approach is to launch a secondary `uvicorn` server in the lifespan context or use `prometheus_client`'s built-in HTTP server. Whichever approach is chosen, the application must not expose `/metrics` on port 8086.
- **Cosine similarity with zero-norm vectors (Task 7)**: `_cosine_similarity` must guard against division by zero when either vector is all-zeros. It returns `0.0` in that case, which will never exceed any positive similarity threshold.
- **`fakeredis` decode_responses (Task 16 conftest)**: Use `decode_responses=False` to match real Redis binary behaviour, since the service stores and retrieves raw JSON bytes.
- **Hypothesis test isolation (Task 17)**: Each property test that uses `fakeredis` must flush the fake Redis store between examples using `@settings(... )` combined with a fresh `FakeRedis()` instance per example, or rely on unique keys per generated input, to avoid state bleed between examples.
- **Integration test skipping (Task 18)**: The `pytest.mark.skipif` on the integration test module must evaluate at collection time (using `os.getenv` directly), not at import time from a cached settings instance.
- **Helm `REDIS_URL` template (Task 15)**: The default `REDIS_URL` in `values.yaml` references `{{ .Release.Name }}-redis-master:6379`. This is a Helm template expression and must be rendered correctly — store it as a plain string default and let the deployment template evaluate it, or hard-code as `redis://redis-master:6379` with a comment noting it requires override when release name differs.
