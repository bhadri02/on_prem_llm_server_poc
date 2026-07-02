# Implementation Plan: API Gateway (Layer 1 — POC)

## Overview

Implement the `api_gateway` Python package as a FastAPI service that accepts OpenAI-compatible HTTP requests, authenticates via static API key, enforces an in-memory sliding-window rate limit, normalizes payloads into the IMF, forwards them to the Security Layer, and serializes responses back to the OpenAI schema. Streaming (`stream: true`) is proxied via `StreamingResponse` + `httpx` async streaming. All activity is logged as structured JSON and emitted as typed audit events to stdout. A Helm chart packages the service for Kubernetes deployment.

Each task below builds incrementally on prior tasks. No orphaned code is left unwired.

---

## Tasks

- [x] 1. Set up project skeleton, configuration, and metrics definitions
  - Create the `api_gateway/` package tree: `middleware/`, `routers/`, `schemas/`, `services/`, plus top-level `__init__.py` files, `Dockerfile`, and `requirements.txt`
  - Write `api_gateway/config.py`: `Settings(BaseSettings)` with fields `gateway_api_key`, `downstream_security_url`, `log_level` (default `"INFO"`), `port` (default `8080`), `metrics_port` (default `9090`), `rate_limit_requests` (default `60`), `rate_limit_window_seconds` (default `60`), `downstream_timeout_seconds` (default `10.0`); add a `@field_validator` that raises `ValueError` when `gateway_api_key` is empty
  - Write `api_gateway/metrics.py`: define `REQUESTS_TOTAL` (Counter, labels `status_code`, `path`), `ERRORS_TOTAL` (Counter, label `error_code`), and `LATENCY_SECONDS` (Histogram, label `path`) using `prometheus_client`
  - Write `requirements.txt` pinning `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, `pydantic-settings`, `prometheus-client`, `hypothesis` (for tests)
  - _Requirements: 2.1, 10.1, 10.2, 10.3, 10.4, 12.1_

- [x] 2. Implement Pydantic schemas
  - [x] 2.1 Implement IMF schemas in `api_gateway/schemas/imf.py`
    - Define `IMFMessage`, `IMFUsage`, `IMFResponse`, `IMFGovernance`, `IMFRouting`, `IMFCache`, `IMFUser`, `IMFRequest`, and `IMFDocument` exactly as specified in the design; all fields must carry the schema-defined default values
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [ ]* 2.2 Write property test for IMF round-trip (Property 4)
    - **Property 4: IMF serialization round-trip preserves all field values**
    - Use `hypothesis` `st.builds(IMFDocument, ...)` to generate arbitrary valid `IMFDocument` instances; call `model_dump()` then `IMFDocument.model_validate()`; assert every field value equals the original
    - File: `tests/unit/test_normalizer.py`
    - **Validates: Requirements 11.6**

  - [x] 2.3 Implement OpenAI request/response schemas in `api_gateway/schemas/openai.py`
    - Define `OpenAIMessage`, `OpenAIChatRequest` (with `@field_validator` enforcing non-empty `messages`), `OpenAIModelsResponse`, `OpenAIChatResponse`
    - _Requirements: 1.1, 1.5, 1.6_

  - [x] 2.4 Implement audit event schema in `api_gateway/schemas/audit.py`
    - Define `AuditEvent` with all fields from the design: `audit_id`, `request_id`, `timestamp_utc`, `user_id`, `department`, `layer` (literal `"api_gateway"`), `event_type`, `method`, `path`, `status_code`, `latency_ms`, `outcome`, `reason`, `error_code`
    - _Requirements: 9.1–9.7_

- [x] 3. Implement core services
  - [x] 3.1 Implement `api_gateway/services/normalizer.py` — `build_imf()`
    - Generate UUID v4 for `request_id`; set `trace_id = request_id`, `span_id = ""`, `timestamp_utc` as ISO-8601 UTC with `Z` suffix
    - Populate `user` block with `user_id="poc-user"`, `department="poc"`, `roles=["developer"]`, `auth_method="api_key"`
    - Map `model`, `messages` (preserve order, role, content), `stream` (default `False`), `max_tokens` (default `2048`), `temperature` (default `0.7`) from `OpenAIChatRequest`
    - Initialize `governance`, `routing`, `cache`, `response`, `metadata`, `extensions` to schema defaults
    - _Requirements: 4.1–4.12, 11.1–11.5_

  - [ ]* 3.2 Write property test for IMF normalization (Property 3)
    - **Property 3: IMF normalization is a total function with correct field mapping**
    - Use `hypothesis` `st.builds(OpenAIChatRequest, ...)` with strategies for arbitrary `model` (optional string), non-empty `messages`, optional `stream`, `max_tokens`, `temperature`; call `build_imf(payload)`; assert UUID-v4 format, `trace_id == request_id`, ISO-8601 timestamp, all field mappings and defaults
    - File: `tests/unit/test_normalizer.py`
    - **Validates: Requirements 4.1–4.11, 11.1–11.5**

  - [x] 3.3 Implement `api_gateway/services/serializer.py` — `serialize_response()`
    - Build OpenAI-compatible dict: `id = f"chatcmpl-{imf.request_id}"`, `object = "chat.completion"`, `created` as current Unix epoch int, `model`, `choices[0]` with `role="assistant"`, `content`, `finish_reason`, `index=0`; populate `usage` from `imf.response.usage`
    - _Requirements: 6.1–6.4_

  - [ ]* 3.4 Write property test for response serialization (Property 6)
    - **Property 6: Response serialization maps all IMF fields to OpenAI schema**
    - Use `hypothesis` `st.builds(IMFDocument, ...)` with varied `response` block (arbitrary `content`, `finish_reason`, `usage`); call `serialize_response(imf)`; assert all output fields match the property postcondition from the design
    - File: `tests/unit/test_serializer.py`
    - **Validates: Requirements 6.1–6.4**

  - [x] 3.5 Implement `api_gateway/services/audit.py` — `emit_audit_event()`
    - Accept an `AuditEvent` instance (or kwargs to build one); serialize with `model.model_dump_json()`; write single JSON line to stdout via `print()`
    - Generate `audit_id` as UUID v4 per call; always set `layer = "api_gateway"`; `outcome` must be `"pass"`, `"block"`, or `"error"`
    - _Requirements: 9.1–9.7_

  - [ ]* 3.6 Write property test for audit event invariant fields (Property 8)
    - **Property 8: Every audit event contains the mandatory invariant fields**
    - Use `hypothesis` to generate arbitrary `AuditEvent` instances covering all five `event_type` values; call `emit_audit_event()` and capture stdout; parse JSON; assert `audit_id` matches UUID v4 regex, `layer == "api_gateway"`, `outcome in {"pass","block","error"}`
    - File: `tests/unit/test_audit.py`
    - **Validates: Requirements 9.7**

  - [x] 3.7 Implement `api_gateway/services/downstream.py` — `forward_to_security()`
    - POST to `{downstream_security_url}/process` with `imf.model_dump()` as JSON body, `Content-Type: application/json`, timeout from settings
    - Catch `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RequestError` → raise `DownstreamError(502)`
    - Non-200 response → raise `DownstreamError(502)`
    - Empty or non-JSON 200 response → raise `DownstreamError(502)`
    - Return `IMFDocument.model_validate(resp.json())` on success
    - _Requirements: 5.1–5.5_

  - [ ]* 3.8 Write property test for downstream error mapping (Property 7)
    - **Property 7: Downstream error mapping always produces 502**
    - Use `hypothesis` `st.integers(min_value=400, max_value=599)` for non-200 status codes; mock `httpx.AsyncClient.post` to return each; also parametrize with `httpx.TimeoutException` and `httpx.ConnectError`; assert `DownstreamError(502)` is raised in all cases
    - File: `tests/unit/test_downstream.py`
    - **Validates: Requirements 5.3, 5.4, 5.5**

- [x] 4. Checkpoint — unit services baseline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement middleware stack
  - [x] 5.1 Implement `api_gateway/middleware/auth.py` — `AuthMiddleware`
    - Extend `BaseHTTPMiddleware`; exempt `/health` and `/metrics` paths
    - Extract `X-Api-Key` header; if absent or empty, emit `auth_fail` audit event with `reason="missing_header"` and return JSON 401
    - If header value does not match `settings.gateway_api_key`, emit `auth_fail` with `reason="key_mismatch"` and return JSON 401
    - On match, emit `auth_pass` audit event and call `call_next`
    - _Requirements: 2.2–2.8, 9.2, 9.3_

  - [ ]* 5.2 Write property test for auth middleware (Property 2)
    - **Property 2: Missing or wrong API key always returns 401**
    - Use `hypothesis` `st.text()` to generate arbitrary strings not equal to `GATEWAY_API_KEY`; include the absent-header case; use `TestClient`; assert HTTP 401 response for every generated key string
    - File: `tests/unit/test_auth_middleware.py`
    - **Validates: Requirements 2.4, 2.5**

  - [x] 5.3 Implement `api_gateway/middleware/rate_limit.py` — `RateLimitMiddleware`
    - Maintain `_store: dict[str, list[float]]` as class variable; exempt `/health` and `/metrics`
    - On each request: evict timestamps older than `now - window_seconds`; if `len(timestamps) >= rate_limit_requests` emit `rate_limited` audit event and return JSON 429 with `Retry-After: 60` header; otherwise append `now` and call `call_next`
    - _Requirements: 3.1–3.7, 9.4_

  - [ ]* 5.4 Write property test for sliding-window eviction (Property 5)
    - **Property 5: Sliding-window eviction leaves only in-window timestamps**
    - Use `hypothesis` `st.lists(st.floats(...))` to generate arbitrary timestamp lists; apply the eviction logic with a given `now`; assert result contains exactly those entries `t` where `t > now - 60`
    - File: `tests/unit/test_rate_limiter.py`
    - **Validates: Requirements 3.1, 3.2, 3.5**

  - [x] 5.5 Implement `api_gateway/middleware/logging.py` — `LoggingMiddleware`
    - Emit one structured JSON line per request to stdout containing `request_id`, `timestamp` (ISO-8601 UTC), `method`, `path`, `status_code`, `latency_ms`
    - Derive `request_id` from request state (set by the route handler) or generate a fallback UUID if not yet set
    - Respect `LOG_LEVEL` env var; log unhandled exceptions at `ERROR` level with `exception_type`, `exception_message`, `traceback`, `latency_ms`
    - _Requirements: 8.1–8.5_

  - [ ]* 5.6 Write property test for log record mandatory fields (Property 9)
    - **Property 9: Structured log record contains all mandatory fields for every request**
    - Use `hypothesis` to generate request scenarios across all outcome classes (200, 400, 401, 429, 502); capture stdout via `capsys`; parse each JSON line; assert presence of `request_id`, `timestamp`, `method`, `path`, `status_code`, `latency_ms`
    - File: `tests/unit/test_auth_middleware.py` or `tests/integration/test_chat_endpoint.py`
    - **Validates: Requirements 8.1, 8.2**

  - [x] 5.7 Implement `api_gateway/middleware/prometheus.py` — `PrometheusMiddleware`
    - Wrap each request: record start time; after `call_next`, increment `REQUESTS_TOTAL` (excluding `/metrics` and `/health`), increment `ERRORS_TOTAL` for 4xx/5xx, observe `LATENCY_SECONDS`
    - Use route template (e.g., `/v1/chat/completions`) not raw URL as `path` label
    - _Requirements: 10.1–10.4_

- [x] 6. Checkpoint — middleware baseline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement routers
  - [x] 7.1 Implement `api_gateway/routers/health.py` — `GET /health`
    - Return `{"status": "ok"}` with HTTP 200; no auth required (exempt in middleware)
    - _Requirements: 1.3_

  - [x] 7.2 Implement `api_gateway/routers/models.py` — `GET /v1/models`
    - Return HTTP 200 with OpenAI models-list JSON: `{"object": "list", "data": [{"id": "<model>", "object": "model"}, ...]}`; static model list configurable or hardcoded for POC
    - Requires valid `X-Api-Key` (enforced by `AuthMiddleware`)
    - _Requirements: 1.2, 1.8_

  - [x] 7.3 Implement `api_gateway/routers/chat.py` — `POST /v1/chat/completions` (non-streaming path)
    - Accept `OpenAIChatRequest`; on Pydantic validation failure return JSON 400 via exception handler
    - Call `build_imf(payload)` → emit `request_received` audit event → call `forward_to_security(imf, client)`
    - On `DownstreamError` return JSON 502; on success call `serialize_response(imf_response)` and return JSON 200
    - Emit `response_sent` audit event with `outcome="pass"` after last byte; emit with `outcome="error"` on 502
    - _Requirements: 1.1, 1.5, 1.6, 1.7, 4.1–4.13, 5.1–5.5, 6.1–6.4, 9.1, 9.5_

  - [x] 7.4 Extend `api_gateway/routers/chat.py` — streaming path (`stream: true`)
    - When `payload.stream` is `True`, use `client.stream("POST", url, json=imf.model_dump())` to open a streaming connection
    - Return `StreamingResponse(content=stream_generator(resp), media_type="text/event-stream")`
    - `stream_generator` yields each chunk from `resp.aiter_bytes()`; on downstream error mid-stream, close and emit `response_sent` with `outcome="error"`; on completion emit `response_sent` with `outcome="pass"`
    - _Requirements: 6.5, 7.1–7.5_

  - [ ]* 7.5 Write property test for invalid messages field (Property 1)
    - **Property 1: Invalid messages field always returns 400**
    - Use `hypothesis` `st.one_of(st.none(), st.just([]), st.text(), st.integers(), st.lists(st.nothing()))` for invalid `messages` shapes; send each via `TestClient` to `POST /v1/chat/completions`; assert HTTP 400 with `{"error": {"code": "400", "message": "Bad request"}}`
    - File: `tests/unit/test_chat_endpoint.py`
    - **Validates: Requirements 1.5, 1.6, 4.7, 11.7**

- [x] 8. Implement `api_gateway/main.py` — app factory and wiring
  - Create `create_app()` factory: register middleware in reverse order (`RateLimitMiddleware` first, `PrometheusMiddleware` last) to achieve execution order `Prometheus → Logging → Auth → RateLimit → Router`
  - Register all routers with appropriate prefixes; mount `/metrics` route using `prometheus_client.make_asgi_app()`
  - Add lifespan context manager: create `httpx.AsyncClient` on startup and store on `app.state.http_client`; close on shutdown
  - Register global `Exception` handler that emits a structured ERROR log and returns JSON 500
  - Validate `Settings` at startup — missing `GATEWAY_API_KEY` must cause immediate process exit with error log
  - _Requirements: 1.4, 1.7, 2.1, 8.5, 10.1_

- [x] 9. Write integration and smoke tests
  - [x] 9.1 Write `tests/integration/test_chat_endpoint.py`
    - Mock `forward_to_security` to return a valid `IMFDocument` response; verify full OpenAI JSON response shape for non-streaming path
    - Mock downstream to return streaming SSE chunks; verify `StreamingResponse` proxies each chunk and terminates with `data: [DONE]\n\n`
    - Mock downstream timeout → assert HTTP 502
    - Verify full middleware pipeline with valid request emits all 5 audit events in correct order to stdout
    - _Requirements: 5.1–5.5, 6.1–6.5, 7.1–7.5, 9.1–9.7_

  - [x] 9.2 Write `tests/smoke/test_health.py` and `tests/smoke/test_startup.py`
    - `GET /health` without `X-Api-Key` returns 200 with `{"status": "ok"}`
    - `GET /v1/chat/completions` (wrong method) returns 405
    - Request to undefined path returns 404
    - 61 sequential requests with same API key — 61st returns 429 with `Retry-After: 60` header
    - Startup with empty `GATEWAY_API_KEY` raises `ValidationError`
    - `LOG_LEVEL=ERROR` suppresses INFO log entries
    - _Requirements: 1.3, 1.4, 1.7, 1.9, 2.1, 3.4_

  - [x] 9.3 Write `tests/integration/test_metrics.py`
    - After one completed 200 request, assert `llm_api_gateway_requests_total` incremented for correct `path` and `status_code` labels
    - After one 401 response, assert `llm_api_gateway_errors_total` incremented with `error_code="401"`
    - Assert `GET /metrics` returns HTTP 200 with `Content-Type: text/plain; version=0.0.4`
    - _Requirements: 10.1–10.4_

- [ ] 10. Checkpoint — full test suite
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement Helm chart
  - [x] 11.1 Create `llm-platform/charts/api-gateway/Chart.yaml`
    - Set `apiVersion: v2`, `name: api-gateway`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`, description per design
    - _Requirements: 12.1_

  - [x] 11.2 Create `llm-platform/charts/api-gateway/values.yaml`
    - Set `replicaCount: 1`; `image.repository: registry.local/api-gateway`, `image.tag: ""`, `image.pullPolicy: IfNotPresent`
    - Set `service.type: ClusterIP`, `service.port: 8080`
    - Configure `ingress.enabled: true`, `ingress.className: nginx`, host `llm-poc.local` with paths `/v1` (Prefix) and `/health` (Exact)
    - Populate `env` block with `GATEWAY_API_KEY: "poc-secret-key"`, `DOWNSTREAM_SECURITY_URL`, `LOG_LEVEL`, `PORT`, `METRICS_PORT`, `DOWNSTREAM_TIMEOUT`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`
    - Set `resources.requests: {cpu: "100m", memory: "256Mi"}`, `resources.limits: {cpu: "500m", memory: "512Mi"}`
    - Set `autoscaling.enabled: false`, `vault.enabled: false`, `observability.metrics.enabled: true`
    - _Requirements: 12.2–12.10_

  - [x] 11.3 Create Helm templates: `deployment.yaml`, `service.yaml`, `ingress.yaml`, `networkpolicy.yaml`, `servicemonitor.yaml`, `hpa.yaml` (disabled), `_helpers.tpl`
    - `deployment.yaml`: inject all `env` values from values, set resource requests/limits, liveness probe at `GET /health`
    - `service.yaml`: ClusterIP on port 8080
    - `ingress.yaml`: nginx ingress class, host `llm-poc.local`, paths `/v1` and `/health`
    - `networkpolicy.yaml`: restrict ingress to NGINX ingress controller, egress to security-layer:8081 and DNS:53
    - `servicemonitor.yaml`: scrape `/metrics` on port 9090, interval 30s
    - `hpa.yaml`: present but `autoscaling.enabled: false` guard
    - _Requirements: 12.1, 12.3, 12.4_

  - [x] 11.4 Create `llm-platform/charts/api-gateway/README.md`
    - Document that `image.tag` must be explicitly set at deploy time; deploying with empty tag is invalid
    - Document that `GATEWAY_API_KEY` default `poc-secret-key` must be replaced before any non-local-dev deployment
    - _Requirements: 12.9, 12.10_

- [ ] 12. Final checkpoint — wire and verify
  - Ensure all tests pass (unit, integration, smoke), ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP, but each maps directly to one of the 9 Hypothesis correctness properties defined in the design.
- Middleware registration order in `main.py` is critical: register `RateLimitMiddleware` first and `PrometheusMiddleware` last so the execution order is `Prometheus → Logging → Auth → RateLimit → Router`.
- The `httpx.AsyncClient` instance MUST be created once in the lifespan and shared across requests via `app.state.http_client` to enable connection pooling.
- All audit events are written to stdout as single-line JSON; no external audit store dependency for POC.
- Property tests use `@settings(max_examples=100)` per the design's Hypothesis configuration.
- The `_store` dict in `RateLimitMiddleware` is safe without a lock in a single-instance asyncio deployment; Phase 2 replaces it with Redis ZSET.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.3", "2.4"] },
    { "id": 2, "tasks": ["2.2", "3.1", "3.3", "3.5"] },
    { "id": 3, "tasks": ["3.2", "3.4", "3.6", "3.7"] },
    { "id": 4, "tasks": ["3.8", "5.1", "5.3", "5.5", "5.7"] },
    { "id": 5, "tasks": ["5.2", "5.4", "5.6", "7.1", "7.2", "7.3"] },
    { "id": 6, "tasks": ["7.4"] },
    { "id": 7, "tasks": ["7.5", "8"] },
    { "id": 8, "tasks": ["9.1", "9.2", "9.3"] },
    { "id": 9, "tasks": ["11.1", "11.2"] },
    { "id": 10, "tasks": ["11.3", "11.4"] }
  ]
}
```
