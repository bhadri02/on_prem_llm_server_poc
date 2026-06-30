# Requirements Document

## Introduction

This document specifies the requirements for the **Cache Layer** (Layer 4) of the Enterprise On-Premises LLM Platform. The Cache Layer sits between the Intelligent Router and the Inference Layer in the canonical request pipeline. Its purpose is to eliminate redundant inference calls by returning previously computed responses for identical or semantically similar prompts.

The POC implementation demonstrates two caching strategies:
1. **Exact-match caching** — SHA256-keyed Redis entries for byte-identical requests.
2. **Semantic caching** — Cosine-similarity search over sentence-transformer embeddings stored as JSON lists in Redis.

The service is a FastAPI application deployed as a Kubernetes-native Helm chart under `llm-platform/charts/cache/`, running on port **8086**, and following the same conventions as the `model_registry` reference service.

---

## Glossary

- **Cache_Service**: The FastAPI application implementing the Cache Layer (Layer 4). Runs on port 8086.
- **Exact_Cache**: The Redis-backed exact-match cache. Key is `exact:{sha256}`. Value is a serialized IMF response JSON with a TTL.
- **Semantic_Cache**: The per-task-type Redis list (`semantic_cache:{task_type}`) storing embedding–response pairs for semantic similarity lookup.
- **Embedding_Generator**: The in-process sentence-transformers component using the `all-MiniLM-L6-v2` model on CPU that produces 384-dimensional float vectors.
- **Cache_Key**: The SHA256 hex digest of the normalized concatenation of `messages` text, `routing.selected_model`, and `request.task_type`.
- **IMF**: Internal Message Format — the canonical JSON envelope shared by all platform layers (defined in the Master Integration Contract).
- **Similarity_Threshold**: The minimum cosine similarity (0.90) at which a semantic cache entry is considered a match.
- **TTL**: Time-To-Live — the Redis key expiry duration, configured per `task_type`.
- **Router**: The Intelligent Router (Layer 3) that calls the Cache_Service for lookup before dispatching to inference, and for write after receiving an inference response.
- **task_type**: One of `chat`, `code`, `summarization` — the classification label carried in `request.task_type` of the IMF.

---

## Requirements

---

### Requirement 1: Cache Lookup Endpoint

**User Story:** As the Intelligent Router, I want to query the Cache_Service for an existing response before dispatching to inference, so that identical or similar prompts are served from cache without consuming GPU resources.

#### Acceptance Criteria

1. THE Cache_Service SHALL expose a `POST /cache/lookup` endpoint that accepts a valid IMF document and returns a JSON object with fields `hit` (boolean), `cache_key` (string), `cache_type` (string or null), and `response` (IMF response object or null).
2. WHEN a `POST /cache/lookup` request is received, THE Cache_Service SHALL compute the Cache_Key as the SHA256 hex digest of the lowercased, whitespace-stripped concatenation of all `request.messages[].content` values joined by a single space, followed by `|`, `routing.selected_model`, `|`, and `request.task_type`.
3. WHEN the Cache_Key is present in Redis under the key `exact:{cache_key}`, THE Cache_Service SHALL return `{ "hit": true, "cache_key": "<hash>", "cache_type": "exact", "response": <stored IMF response> }`.
4. WHEN the Cache_Key is absent from the Exact_Cache, THE Cache_Service SHALL generate an embedding for the same concatenated content string defined in Criterion 2 using the Embedding_Generator and perform a linear cosine-similarity scan of all entries in the Redis list `semantic_cache:{task_type}`.
5. WHEN the highest cosine similarity score from the semantic scan is greater than or equal to the configured `SIMILARITY_THRESHOLD` and is a positive value, THE Cache_Service SHALL return `{ "hit": true, "cache_key": "<matched_key>", "cache_type": "semantic", "response": <matched IMF response> }` and include the similarity score (a positive float) in the log entry.
6. WHEN no exact match and no semantic match with similarity ≥ `SIMILARITY_THRESHOLD` exists, THE Cache_Service SHALL return `{ "hit": false, "cache_key": "<computed_key>", "cache_type": null, "response": null }`.
7. IF a cache lookup has already returned a successful hit result before a Redis connection failure is detected, THEN THE Cache_Service SHALL return the previously found cache hit response rather than a miss, and SHALL only return a miss for fresh lookups where Redis is unavailable from the start.
8. IF the Redis connection is unavailable at the start of a lookup (before any hit has been found), THEN THE Cache_Service SHALL return `{ "hit": false, "cache_key": "<computed_key>", "cache_type": null, "response": null }` with HTTP 200 and log the connection error as a structured JSON error event.
9. IF the Embedding_Generator fails during the semantic scan phase of a lookup, THEN THE Cache_Service SHALL return `{ "hit": false, "cache_key": "<computed_key>", "cache_type": null, "response": null }` with HTTP 200 and log the embedding failure as a structured JSON error event.
10. IF the incoming IMF document is missing any of `request.messages`, `routing.selected_model`, or `request.task_type`, THEN THE Cache_Service SHALL return HTTP 422 with a structured error body listing the missing fields.

---

### Requirement 2: Cache Write Endpoint

**User Story:** As the Intelligent Router, I want to store a new inference response in the cache after receiving it from the Inference Layer, so that future identical or similar requests can be served without invoking inference again.

#### Acceptance Criteria

1. THE Cache_Service SHALL expose a `POST /cache/write` endpoint that accepts a valid IMF document containing a non-null `response` object.
2. WHEN a `POST /cache/write` request is received with a valid IMF document, THE Cache_Service SHALL return HTTP 200 with a JSON body `{ "written": true, "cache_key": "<hash>" }` upon successful write.
3. WHEN a `POST /cache/write` request is received, THE Cache_Service SHALL compute the Cache_Key using the same algorithm as Requirement 1, Criterion 2.
4. WHEN writing to the Exact_Cache, THE Cache_Service SHALL store the `response` object as serialized JSON under the Redis key `exact:{cache_key}` with a TTL of 3600 seconds for `task_type` `"chat"`, 7200 seconds for `task_type` `"code"`, 86400 seconds for `task_type` `"summarization"`, and 3600 seconds for any unrecognized `task_type`.
5. WHEN the Semantic_Cache list `semantic_cache:{task_type}` contains fewer than 500 entries, THE Cache_Service SHALL generate an embedding for the prompt text using the Embedding_Generator and append the JSON object `{ "key": "<cache_key>", "embedding": [<float array>], "response": <IMF response> }` to the Redis list `semantic_cache:{task_type}`.
6. WHEN the Semantic_Cache list `semantic_cache:{task_type}` already contains 500 or more entries, THE Cache_Service SHALL skip the semantic write and log a structured JSON warning event with `event: "semantic_cache_full"` and the `task_type`.
7. IF the incoming IMF document has a null or absent `response` field, THEN THE Cache_Service SHALL return HTTP 422 with a structured error body containing `{ "event": "cache_write_invalid", "request_id": "<id>", "reason": "response field null or absent" }` and emit the same fields as a structured JSON log entry.
8. IF the Redis connection is unavailable during a write, THEN THE Cache_Service SHALL return HTTP 503 with a structured JSON error body containing `{ "event": "redis_unavailable", "request_id": "<id>", "operation": "write" }` and log the failure as a structured error event.
9. IF the Embedding_Generator fails during the semantic write phase, THEN THE Cache_Service SHALL log a structured JSON error event with `event: "embedding_error"` and `operation: "write"`, skip the semantic write, and still return HTTP 200 with `{ "written": true, "cache_key": "<hash>" }` indicating the exact write succeeded.
10. IF the incoming IMF document is missing any of `request.messages`, `routing.selected_model`, or `request.task_type`, THEN THE Cache_Service SHALL return HTTP 422 with a structured error body listing the missing fields.

---

### Requirement 3: IMF Integration

**User Story:** As a platform engineer, I want the Cache_Service to read from and write to standardized IMF fields, so that all layers remain interoperable without schema drift.

#### Acceptance Criteria

1. THE Cache_Service SHALL read exclusively from `request.messages`, `routing.selected_model`, and `request.task_type` when computing cache keys and prompt text for embedding.
2. THE Cache_Service SHALL read the following sub-fields from `response` on cache write operations: `response.content`, `response.finish_reason`, `response.usage.prompt_tokens`, `response.usage.completion_tokens`, and `response.usage.total_tokens`.
3. WHEN a cache hit occurs, THE Cache_Service SHALL overwrite the entire IMF `cache` block in its response payload with: `{ "lookup_hit": true, "cache_key": "<sha256>", "cache_type": "exact" | "semantic", "similarity_score": <float in [0.0, 1.0]> | null }` where `similarity_score` is null for exact hits and a float in [0.0, 1.0] for semantic hits.
4. WHEN a cache miss occurs, THE Cache_Service SHALL overwrite the entire IMF `cache` block in its response payload with: `{ "lookup_hit": false, "cache_key": "<computed_sha256>", "cache_type": null, "similarity_score": null }`.
5. THE Cache_Service SHALL NOT add any fields outside the `cache` block, `metadata`, and `extensions` envelopes of the IMF on any response.
6. WHEN constructing the response payload for a lookup, THE Cache_Service SHALL replace the incoming `cache` block wholesale (not merge) with the values defined in Criteria 3 or 4, preserving all other IMF fields unchanged.

---

### Requirement 4: Exact-Match Caching (Redis)

**User Story:** As a platform operator, I want exact cache entries to expire automatically based on task type, so that stale responses are not served indefinitely and Redis memory is bounded.

#### Acceptance Criteria

1. THE Exact_Cache SHALL use Redis `SET` with `EX` (TTL in seconds) for every cache write so that keys expire automatically.
2. THE Cache_Service SHALL apply TTLs of 3600 s for `task_type` `"chat"`, 7200 s for `task_type` `"code"`, and 86400 s for `task_type` `"summarization"` as configured via the environment variables `TTL_CHAT`, `TTL_CODE`, and `TTL_SUMMARIZATION`, with a default fallback of 3600 s for any absent or unrecognized `task_type`.
3. WHEN an exact cache key is retrieved via `GET exact:{cache_key}`, THE Cache_Service SHALL return the stored value without mutating it or resetting its TTL.
4. THE Cache_Service SHALL connect to Redis using the URL provided by the `REDIS_URL` environment variable (default: `redis://redis:6379`).
5. THE Cache_Service SHALL use no Redis authentication for the POC, consistent with the Redis sub-chart configuration `auth.enabled: false`. IF Redis responds with an authentication error, THEN THE Cache_Service SHALL block cache retrieval, log a structured warning with `event: "redis_auth_unexpected"`, and treat the request as a cache miss.
6. WHEN an exact cache key lookup returns nil from Redis (key not found or expired), THE Cache_Service SHALL treat the result as a cache miss and return `lookup_hit: false` with a null response payload.
7. IF the Redis connection is unavailable or times out during an exact cache read or write, THEN THE Cache_Service SHALL log a structured error event with `event: "redis_unavailable"` and `operation: "read"` or `"write"` respectively, gracefully degrade to a cache miss on read or skip the write operation, and not propagate an unhandled exception to the caller.

---

### Requirement 5: Semantic Caching (Embeddings + Cosine Similarity)

**User Story:** As a platform engineer, I want the Cache_Service to recognize semantically equivalent prompts even when they differ in exact wording, so that paraphrased queries benefit from cached responses and inference load is reduced.

#### Acceptance Criteria

1. THE Embedding_Generator SHALL use the `all-MiniLM-L6-v2` sentence-transformers model loaded on CPU to produce 384-dimensional float embeddings.
2. THE Cache_Service SHALL load the `all-MiniLM-L6-v2` model once at application startup and reuse the same instance for all embedding operations.
3. WHEN performing a semantic lookup and the Exact_Cache returns a miss, THE Cache_Service SHALL load all entries from the Redis list `semantic_cache:{task_type}` and compute the cosine similarity between the query embedding and each stored embedding in a single in-process linear scan.
4. WHEN the Redis list `semantic_cache:{task_type}` is empty or does not exist, THE Cache_Service SHALL treat the semantic scan result as a miss and return `hit: false`.
5. THE Cache_Service SHALL configure the cosine similarity threshold via the environment variable `SIMILARITY_THRESHOLD` (default: `0.90`; valid range: `0.0` to `1.0` inclusive), and treat any entry with similarity strictly below this value as a non-match.
6. THE Cache_Service SHALL configure the maximum number of semantic entries per task_type via the environment variable `MAX_SEMANTIC_ENTRIES` (default: `500`).
7. WHEN multiple stored entries have cosine similarity ≥ `SIMILARITY_THRESHOLD`, THE Cache_Service SHALL return the entry with the highest cosine similarity score.
8. WHEN the `MAX_SEMANTIC_ENTRIES` limit is reached for a given `task_type` during a write, THE Cache_Service SHALL skip the semantic write for that entry, emit a `semantic_cache_full` log event, and still complete the exact cache write successfully.

---

### Requirement 6: FastAPI Service Structure

**User Story:** As a developer, I want the Cache_Service to follow the same FastAPI conventions as the model_registry reference service, so that the codebase is consistent and onboarding is straightforward.

#### Acceptance Criteria

1. THE Cache_Service SHALL be implemented as a FastAPI application with an async lifespan context manager that initializes the Redis connection and loads the Embedding_Generator at startup, storing any failure reason on `app.state` without exiting the process, and closes the Redis connection at shutdown.
2. WHEN the service is ready (Redis reachable and Embedding_Generator loaded), THE `GET /health` endpoint SHALL return HTTP 200 with `{ "status": "ok" }`.
3. IF Redis is unreachable at the time of a health check, THEN THE `GET /health` endpoint SHALL return HTTP 503 with `{ "status": "unavailable", "reason": "redis_unreachable" }`.
4. IF the Embedding_Generator failed to load at startup, THEN THE `GET /health` endpoint SHALL return HTTP 503 with `{ "status": "unavailable", "reason": "embedding_model_load_failed" }`.
5. WHILE the service is still completing startup initialization, THE `GET /health` endpoint SHALL return HTTP 503 with `{ "status": "starting" }`.
6. THE Cache_Service SHALL use a `LoggingMiddleware` that emits one structured JSON log entry per request to stdout, including `request_id` (extracted first from the IMF body field, then from the `X-Request-ID` header, then defaulting to `"unknown"`), `method`, `path`, `status_code`, and `latency_ms`, filtered by the configured `LOG_LEVEL`.
7. THE Cache_Service SHALL be organized into the module structure: `cache_service/main.py`, `cache_service/config.py`, `cache_service/routers/cache.py`, `cache_service/routers/health.py`, `cache_service/schemas/`, `cache_service/services/exact_cache.py`, `cache_service/services/semantic_cache.py`, `cache_service/services/embedding.py`, and `cache_service/middleware/logging.py`.
8. THE Cache_Service SHALL run on the port specified by the `PORT` environment variable (default: `8086`; valid range: 1–65535). IF `PORT` is set to a value outside this range, THEN the service SHALL fail to start and emit a structured JSON error log indicating an invalid port configuration.
9. THE Cache_Service SHALL read all configuration from environment variables via a Pydantic `BaseSettings` class in `cache_service/config.py` with no hardcoded values.

---

### Requirement 7: Structured Logging and Audit Events

**User Story:** As a platform operator, I want every cache operation to emit a structured JSON log entry to stdout, so that cache hit rates, latency, and errors can be monitored without additional instrumentation.

#### Acceptance Criteria

1. WHEN a cache hit occurs (exact or semantic), THE Cache_Service SHALL emit a structured JSON log entry with at minimum: `event: "cache_hit"`, `request_id`, `cache_type` (`"exact"` or `"semantic"`), `similarity_score` (a float in (0.0, 1.0] for semantic hits, null for exact hits), and `latency_ms` (a non-negative integer measured from request receipt to log entry emission in milliseconds). Exact hits MUST have `cache_type: "exact"` and `similarity_score: null`; semantic hits MUST have `cache_type: "semantic"` and a positive `similarity_score`.
2. WHEN a cache miss occurs, THE Cache_Service SHALL emit a structured JSON log entry with at minimum: `event: "cache_miss"`, `request_id`, and `latency_ms` (a non-negative integer in milliseconds).
3. WHEN a cache write completes successfully, THE Cache_Service SHALL emit a structured JSON log entry with at minimum: `event: "cache_write"`, `request_id`, `cache_key`, `task_type` (one of `"chat"`, `"code"`, `"summarization"`, or the actual value received), and `latency_ms` (a non-negative integer in milliseconds).
4. WHEN a semantic cache write is skipped due to the 500-entry limit, THE Cache_Service SHALL emit a structured JSON log entry with `event: "semantic_cache_full"`, `task_type`, and `request_id`.
5. THE Cache_Service SHALL emit all log entries to stdout as newline-delimited JSON at the log level configured by the `LOG_LEVEL` environment variable (valid values: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`; default: `"INFO"`; invalid values SHALL fall back to `"INFO"`).
6. THE Cache_Service SHALL NOT include any fields listed in the IMF `governance.pii_fields_detected` array, or raw `request.messages[].content` values, in any log entry at any log level.
7. IF emitting a log entry to stdout fails, THEN THE Cache_Service SHALL silently discard the log entry and continue processing the request without propagating the failure to the caller.

---

### Requirement 8: Helm Chart and Kubernetes Deployment

**User Story:** As a platform engineer, I want the Cache_Service to be packaged as a Helm chart under `llm-platform/charts/cache/`, so that it can be deployed and configured consistently with all other platform layers.

#### Acceptance Criteria

1. THE Cache_Service Helm chart SHALL be located at `llm-platform/charts/cache/` and SHALL contain `Chart.yaml`, `values.yaml`, `README.md`, and a `templates/` directory with `deployment.yaml`, `service.yaml`, `networkpolicy.yaml`, `servicemonitor.yaml`, and `_helpers.tpl`.
2. THE Cache_Service `values.yaml` SHALL include the following POC defaults: `replicaCount: 1`, `service.port: 8086`, `env.REDIS_URL: "redis://redis:6379"`, `env.SIMILARITY_THRESHOLD: "0.90"`, `env.EMBEDDING_MODEL: "all-MiniLM-L6-v2"`, `env.MAX_SEMANTIC_ENTRIES: "500"`, `env.LOG_LEVEL: "INFO"`, `autoscaling.enabled: false`, and `vault.enabled: false`.
3. THE Cache_Service Helm chart SHALL declare a `bitnami/redis` sub-chart dependency (chart version `19.x`) configured for standalone mode (`architecture: standalone`) with `auth.enabled: false` and `master.persistence.size: 5Gi`.
4. THE Cache_Service Helm chart SHALL define a `NetworkPolicy` in the `llm-platform` namespace that permits ingress only from pods matching `app.kubernetes.io/name: router` and permits egress only to pods matching `app.kubernetes.io/name: redis`.
5. THE Cache_Service Helm chart SHALL define a `ServiceMonitor` that configures Prometheus scraping of the `/metrics` endpoint on port `9090` with a scrape `interval: 30s`.
6. THE Cache_Service deployment manifest SHALL define liveness and readiness probes pointing to `GET /health` with `initialDelaySeconds: 15`, `periodSeconds: 15`, `timeoutSeconds: 2`, and `failureThreshold: 3`.
7. IF the Helm chart image tag is not explicitly overridden at deploy time via `--set image.tag=<sha>`, THEN THE Cache_Service deployment SHALL use the tag value `"latest"` as a fallback. THE Cache_Service deployment `pullPolicy` SHALL always be `IfNotPresent` regardless of the tag value.

---

### Requirement 9: Prometheus Metrics

**User Story:** As a platform operator, I want the Cache_Service to expose standardized Prometheus metrics, so that cache performance can be observed and alerted on through the shared observability stack.

#### Acceptance Criteria

1. THE Cache_Service SHALL expose a `/metrics` endpoint on a dedicated port `9090` (separate from the application port `8086`) that returns Prometheus text-format exposition data compatible with standard Prometheus scraping.
2. THE Cache_Service SHALL emit the counter metric `llm_cache_requests_total` with labels `{status="hit|miss", cache_type="exact|semantic|none", task_type}`, where `cache_type="none"` is used when `status="miss"`.
3. THE Cache_Service SHALL emit the histogram metric `llm_cache_latency_seconds` with labels `{operation="lookup|write", task_type}` covering end-to-end handler latency, using bucket boundaries `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]`.
4. THE Cache_Service SHALL emit the counter metric `llm_cache_errors_total` with labels `{error_code, operation}` incremented on any Redis connection failure or embedding error.
5. WHEN a cache write operation completes, THE Cache_Service SHALL update the gauge metric `llm_cache_semantic_entries` with label `{task_type}` by issuing a Redis `LLEN semantic_cache:{task_type}` command and setting the gauge to the returned length.

---

### Requirement 10: Cache Key Determinism and Round-Trip Integrity

**User Story:** As a platform engineer, I want the Cache_Key derivation to be deterministic and the serialization/deserialization of cached responses to be lossless, so that cache lookups reliably match their corresponding writes and no data corruption occurs.

#### Acceptance Criteria

1. THE Cache_Service SHALL produce the same Cache_Key for any two IMF inputs that have identical `routing.selected_model` and `request.task_type`, and whose `request.messages[].content` values produce identical strings after per-message leading/trailing whitespace stripping, lowercasing, and single-space joining — regardless of surrounding or inter-object whitespace in the JSON payload.
2. WHEN an IMF response is serialized to JSON for storage in Redis and then deserialized on retrieval, THE Cache_Service SHALL return a response object where every field present in the original matches the retrieved value by type and value, with no fields added, removed, or type-coerced.
3. WHEN a valid IMF response is written via `POST /cache/write`, a subsequent `POST /cache/lookup` with the same `request.messages`, `routing.selected_model`, and `request.task_type` values SHALL return `hit: true` and the stored response object, provided the lookup occurs before the TTL for the given `task_type` has elapsed.
4. THE Cache_Service SHALL serialize all Redis values as UTF-8 encoded JSON strings, preserving string types as strings, integer types as integers, float types as floats, boolean types as booleans, null as null, and array/object structures intact — with no implicit type coercion on deserialization.
