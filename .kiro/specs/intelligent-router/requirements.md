# Requirements Document

## Introduction

The Intelligent Router (Layer 3) is a standalone FastAPI microservice that sits between the Security & Governance Layer (Layer 2) and the downstream inference backends in the Enterprise On-Premises LLM Platform. It runs on port 8082.

Every platform request passes through the Router exactly once, after all pre-generation security checks have passed. The Router is responsible for six sequential operations: classifying the incoming request by task type, selecting the correct inference model, checking that model's health, consulting the Cache Layer for an existing response, dispatching to the Inference Adapter if no cached response exists, and writing the result back to the cache before returning the completed IMF to its caller.

The POC implementation is rule-based and statically configured. All production-deferred features — ML-based classification, OPA policy queries, circuit breakers, A/B routing, GPU probing, MLflow integration, gRPC, and mTLS — are explicitly out of scope. The POC demonstrates correct routing decisions, cache integration, fallback behaviour, and a complete audit trail using lightweight alternatives.

---

## Glossary

- **Router**: The FastAPI microservice implementing Layer 3 of the platform; runs on port 8082.
- **IMF**: Internal Message Format — the canonical JSON envelope defined in the platform master contract that all inter-layer messages must use.
- **Task_Classifier**: The component that scans `request.messages` content against keyword rules loaded from `task_classifier_rules.yaml` and assigns a `task_type` value to the IMF.
- **Model_Matrix**: The static YAML configuration file (`model_matrix.yaml`) that maps task types to primary and fallback model entries, including each model's inference endpoint and health URL.
- **Routing_Mode**: The field `routing.routing_mode` in the IMF; valid POC values are `auto` (Router selects model) and `pinned` (caller specifies model via `request.model`).
- **Health_Checker**: The component that issues an HTTP GET to a model's configured `health_url` to determine whether the backend is ready to serve requests.
- **Cache_Layer**: The downstream Cache service at `http://cache:8086` that the Router calls before and after inference.
- **Cache_Lookup**: An HTTP POST to `http://cache:8086/cache/lookup` that returns `{"hit": true/false, ...}`.
- **Cache_Write**: An async HTTP POST to `http://cache:8086/cache/write` dispatched after a successful inference response.
- **Inference_Adapter**: The downstream inference translation service at `http://inference-adapter:8087`; the Router forwards the full IMF to `POST /infer` and receives back the IMF with the `response` block populated.
- **Audit_Store**: The append-only audit trail service at `http://audit-store:9200`; the Router writes fire-and-forget audit events to `POST /audit/events`.
- **Audit_Logger**: The Router component responsible for fire-and-forget HTTP POSTs to the Audit_Store.
- **Fallback_Manager**: The component that iterates the model fallback chain defined in Model_Matrix when the primary model is unhealthy or returns an inference error.
- **selected_model**: The value written to `routing.selected_model` in the IMF, identifying the model tag that was used (or will be used) for inference.
- **fallback_level**: The integer value of `routing.fallback_level` in the IMF; `0` means the primary model was used; values `≥ 1` indicate a fallback was applied.
- **cache_key**: The deterministic hash string stored in `cache.cache_key` in the IMF, computed by the Cache Layer on lookup.
- **Metrics_Endpoint**: The `/metrics` endpoint on port 9090 that exposes Prometheus metrics.

---

## Requirements

---

### Requirement 1: Route Endpoint — Primary Request Flow

**User Story:** As the Security & Governance Layer, I want to POST a governance-enriched IMF to the Router so that the request is classified, routed to the correct model, and returned with the `response` block populated, without the Security Layer needing to know about inference backend topology.

#### Acceptance Criteria

1. WHEN a POST request is received at `/route` with a valid IMF body containing a non-empty `request.messages` array and `governance.content_safety_passed` equal to `true`, THE Router SHALL execute the full routing pipeline (task classification → model selection → health check → cache lookup → inference dispatch → cache write → audit log) and return HTTP 200 with `Content-Type: application/json` and the completed IMF body.
2. WHEN the routing pipeline completes successfully, THE Router SHALL return the IMF where: `request.task_type` is a non-null string, `routing.selected_model` is a non-null string, `routing.routing_mode` is one of `"auto"` or `"pinned"`, `routing.fallback_level` is a non-negative integer, `cache.lookup_hit` is a boolean, `cache.cache_key` is a non-null string, and `response.content` is a non-null non-empty string.
3. IF the request body is not parseable as valid JSON, THEN THE Router SHALL return HTTP 400 with `{"error": "invalid_json", "request_id": null}`; no downstream calls SHALL be made.
4. IF the `request_id` field is absent or not a valid UUID-v4 in the inbound IMF, THEN THE Router SHALL return HTTP 422 with `{"error": "validation_error", "field": "request_id", "request_id": null}`.
5. IF `request.messages` is absent or is an empty array in the inbound IMF, THEN THE Router SHALL return HTTP 422 with `{"error": "validation_error", "field": "request.messages", "request_id": "<request_id>"}`.
6. IF `governance.content_safety_passed` is `false` or absent in the inbound IMF, THEN THE Router SHALL return HTTP 400 with `{"error": "governance_check_failed", "request_id": "<request_id>"}` and SHALL NOT initiate any downstream call.
7. IF all models in the fallback chain are exhausted without a successful response, THEN THE Router SHALL return HTTP 503 with `{"error": "all_backends_exhausted", "request_id": "<request_id>", "fallback_level": <n>}` where `<n>` is the final integer value of `routing.fallback_level`.
8. IF an unhandled exception occurs during the routing pipeline, THEN THE Router SHALL return HTTP 500 with `{"error": "internal_error", "request_id": "<request_id>"}` and SHALL emit a structured JSON error log to stdout containing at minimum `request_id`, `error`, and `timestamp_utc`.

---

### Requirement 2: Task Classification

**User Story:** As a platform engineer, I want the Router to automatically determine the task type of every incoming request from the message content so that the model capability matrix can route each request to a model suited for that task type.

#### Acceptance Criteria

1. WHEN the Task_Classifier processes `request.messages` and finds a case-insensitive substring or keyword match for any entry listed under a task type in `task_classifier_rules.yaml`, THE Router SHALL set `request.task_type` in the outbound IMF to the task type whose rule list produced the first match, evaluated in the order: `code` → `reasoning` → `summarization` → `translation` → `chat`.
2. WHEN the Task_Classifier finds no match for any configured task type across all messages, or when all `content` fields in `request.messages` are null or empty strings producing an empty concatenation, THE Router SHALL set `request.task_type` to `"chat"` (the default task type).
3. THE Task_Classifier SHALL concatenate the `content` fields of all messages in `request.messages` using a single space as separator, convert the result to lowercase, and apply each keyword rule as a case-insensitive substring search against the concatenated string.
4. WHEN `request.task_type` is already set to a non-null value in the inbound IMF, THE Router SHALL overwrite it with the result of fresh Task_Classifier evaluation, ensuring the Router is always authoritative for task type.
5. WHEN the Router starts and `TASK_RULES_PATH` resolves to a readable, parseable YAML file containing a valid `rules` map and a `default` key, THE Task_Classifier SHALL load all rules into memory before the Router's HTTP listener begins accepting connections.
6. IF `TASK_RULES_PATH` is not set, the file does not exist, the file cannot be read, or the YAML is malformed at startup, THEN THE Router SHALL log an ERROR message identifying the specific failure and refuse to start, exiting with a non-zero exit code.
7. IF the `rules` map in `task_classifier_rules.yaml` is empty at startup, THEN THE Router SHALL log a WARNING and classify all requests as `"chat"` without refusing to start.
8. WHEN a request arrives via `POST /v1/chat/completions` with a valid `messages` array, THE Task_Classifier SHALL apply the same keyword-based classification logic to the messages field of the OpenAI-format request body before constructing the IMF.
9. IF a request arrives via `POST /v1/chat/completions` and `messages` is absent, null, or an empty array, THEN THE Router SHALL return HTTP 422 with `{"error": {"code": 422, "message": "messages array is required and must be non-empty"}}` without invoking the Task_Classifier.


---

### Requirement 3: Model Selection

**User Story:** As a platform engineer, I want the Router to select the correct inference model based on the task type and routing mode so that each request is served by a model with the appropriate capability.

#### Acceptance Criteria

1. WHEN `routing.routing_mode` is `"auto"` or absent in the inbound IMF, THE Router SHALL look up the primary model for `request.task_type` in the Model_Matrix and set `routing.selected_model` to that model's name, and set `routing.routing_mode` to `"auto"` in the outbound IMF.
2. WHEN `routing.routing_mode` is `"pinned"` in the inbound IMF and `request.model` is a non-null, non-empty string present in the Model_Matrix, THE Router SHALL set `routing.selected_model` to the value of `request.model`, set `routing.routing_mode` to `"pinned"` in the outbound IMF, and proceed without applying task-type-based model selection.
3. IF `routing.routing_mode` is `"pinned"` and `request.model` is absent, null, an empty string, or names a model not defined in the Model_Matrix, THEN THE Router SHALL return HTTP 422 with `{"error": "invalid_pinned_model", "model": "<request.model or null>", "request_id": "<request_id>"}`.
4. WHEN the Router starts and `MODEL_MATRIX_PATH` resolves to a readable, parseable YAML file containing a valid `models` map (non-empty, each entry containing at minimum a `name` and an `endpoint` field) and a `task_defaults` map (non-empty), THE Router SHALL load the Model_Matrix into memory before the Router's HTTP listener begins accepting connections.
5. IF `MODEL_MATRIX_PATH` is not set, the file does not exist, the file cannot be read, or the YAML is malformed at startup, THEN THE Router SHALL log an ERROR message identifying the specific failure and refuse to start, exiting with a non-zero exit code.
6. IF the `task_defaults` map in `model_matrix.yaml` does not contain an entry for the classified `task_type`, THEN THE Router SHALL fall back to the `task_defaults.chat` entry; IF `task_defaults.chat` is also absent, THEN THE Router SHALL return HTTP 503 with `{"error": "no_model_for_task", "task_type": "<task_type>", "request_id": "<request_id>"}`.
7. THE Router SHALL initialise `routing.fallback_level` to `0` at the start of every new routing attempt before any model is selected.
8. THE Router SHALL increment `routing.fallback_level` by exactly `1` each time the Fallback_Manager advances to the next model in the fallback chain.


---

### Requirement 4: Health Checking and Fallback

**User Story:** As a platform engineer, I want the Router to verify that the selected model's backend is reachable before dispatching inference so that unhealthy backends are bypassed and requests are served by the next available model.

#### Acceptance Criteria

1. WHEN a model has been selected (primary or fallback), THE Health_Checker SHALL issue an HTTP GET to that model's configured `health_url` with a timeout of 5 seconds and `follow_redirects=False` before dispatching the inference request.
2. WHEN the health check returns HTTP 200, THE Router SHALL proceed to the cache lookup stage with the currently selected model.
3. IF the health check returns a non-200 HTTP status code, times out, or the connection is refused, THEN THE Fallback_Manager SHALL advance to the next model entry in the fallback chain for the current task type and repeat the health check for that model.
4. WHEN the Fallback_Manager advances to a fallback model, THE Router SHALL: (a) increment `routing.fallback_level` by 1, (b) update `routing.selected_model` to the new model name, and (c) emit a structured JSON `routing_fallback` log entry to stdout containing `request_id`, `failed_model`, and `fallback_level` — in that order, before issuing the next health check.
5. IF no model in the fallback chain passes the health check, THEN THE Router SHALL emit a structured JSON `routing_fallback` event with `outcome: "all_exhausted"` and return HTTP 503 with `{"error": "all_backends_exhausted", "request_id": "<request_id>", "fallback_level": <n>}` where `<n>` is the final integer value of `routing.fallback_level` at the point all models are exhausted.
6. IF the health check returns any 3xx redirect response, THEN THE Health_Checker SHALL treat that response as a failure and the Fallback_Manager SHALL advance to the next model.
7. IF the Model_Matrix defines `fallback: null` for the primary model (i.e., no fallback entry exists for the task type), THEN THE Fallback_Manager SHALL treat the primary model as the only option; a health check failure in this case SHALL result in HTTP 503 immediately.


---

### Requirement 5: Cache Lookup

**User Story:** As a platform engineer, I want the Router to check the Cache Layer before every inference call so that repeated identical or semantically equivalent requests are served from cache without incurring inference latency.

#### Acceptance Criteria

1. WHEN a model has been selected and the health check has passed, THE Router SHALL issue an HTTP POST to `http://cache:8086/cache/lookup` with `Content-Type: application/json` and a JSON body containing `messages` (from `request.messages`), `model` (the selected model name), `task_type` (the classified task type), and `request_id`, before calling the Inference_Adapter.
2. WHEN the Cache_Layer returns `{"hit": true, ...}` with a non-null `response` field, THE Router SHALL set `cache.lookup_hit` to `true` in the IMF, set `cache.cache_key` to the value of the `cache_key` field returned by the Cache_Layer, copy `response.content`, `response.finish_reason`, and `response.usage` from the Cache_Layer response into the IMF `response` block, and return the completed IMF to the caller without calling the Inference_Adapter.
3. WHEN the Cache_Layer returns `{"hit": false, ...}`, THE Router SHALL set `cache.lookup_hit` to `false` in the IMF, set `cache.cache_key` to the value of the `cache_key` field returned by the Cache_Layer (or `null` if `cache_key` is absent or null in the response), and proceed to the inference dispatch stage.
4. IF the Cache_Layer is unreachable, returns a non-200 HTTP status code, or does not respond within 3 seconds, THEN THE Router SHALL set `cache.lookup_hit` to `false`, leave `cache.cache_key` as `null`, log a WARNING containing `request_id` and the failure reason, and proceed to the inference dispatch stage rather than returning an error.
5. WHEN the Cache_Layer returns a cache HIT, THE Router SHALL emit a structured JSON `cache_hit` audit event to stdout before returning the IMF to the caller.
6. THE Router SHALL NOT skip cache lookup for `pinned` routing mode; the cache check SHALL apply regardless of routing mode.


---

### Requirement 6: Inference Dispatch

**User Story:** As the platform's routing layer, I want the Router to forward the enriched IMF to the Inference Adapter and receive the completed IMF with the response block populated so that the inference concern is fully encapsulated behind the Adapter interface.

#### Acceptance Criteria

1. WHEN the cache lookup returns a miss, THE Router SHALL issue an HTTP POST to `http://inference-adapter:8087/infer` with the full IMF as the request body, `routing.selected_model` set to the selected model name, `Content-Type: application/json`, and `X-Request-Id: <request_id>` headers.
2. WHEN the Inference_Adapter returns HTTP 200 with a valid IMF body containing a non-null `response.content`, THE Router SHALL use that IMF (with the populated `response` block) as the return value from the routing pipeline.
3. IF the Inference_Adapter returns a non-200 HTTP status code, THE Fallback_Manager SHALL advance to the next model in the fallback chain (repeating health check and inference dispatch), increment `routing.fallback_level`, and log a WARNING at WARNING level containing `request_id`, `selected_model`, and the received HTTP status code.
4. IF the Inference_Adapter does not respond within the duration specified by `INFERENCE_TIMEOUT_SECONDS`, THE Fallback_Manager SHALL treat the timeout as an inference failure, log a WARNING with `request_id` and `selected_model`, and advance to the next fallback model. The valid range for `INFERENCE_TIMEOUT_SECONDS` is `[1, 600]`; values outside this range SHALL cause the Router to refuse to start.
5. IF the Inference_Adapter returns HTTP 200 but the response body is empty or not parseable as valid JSON, THEN THE Router SHALL treat the response as an inference failure, advance the Fallback_Manager, and log a WARNING with `request_id` and the parse error.
6. THE Router SHALL set the `Content-Type: application/json` header on all HTTP POST requests to the Inference_Adapter.
7. THE Router SHALL include a `X-Request-Id` header with the value of `request_id` on all requests to the Inference_Adapter to preserve trace correlation.
8. IF the Inference_Adapter returns HTTP 200 with parseable JSON that is not a valid IMF (i.e., the `response` block is absent or `response.content` is null), THEN THE Router SHALL treat the response as an inference failure, advance the Fallback_Manager, and log a WARNING with `request_id` and the specific field that is missing or null.


---

### Requirement 7: Cache Write (Async)

**User Story:** As a platform engineer, I want the Router to asynchronously store every successful inference response in the Cache Layer so that future identical or semantically equivalent requests are served from cache.

#### Acceptance Criteria

1. WHEN the Inference_Adapter returns HTTP 200 with a non-null, non-empty `response.content` and `cache.lookup_hit` is `false`, THE Router SHALL dispatch an HTTP POST to `http://cache:8086/cache/write` as a background task (fire-and-forget) without blocking the response to the caller.
2. THE Cache_Write POST body SHALL contain `messages` (from `request.messages`), `model` (the selected model name), `task_type` (the classified task type), and `response_imf` (the full IMF with the populated `response` block).
3. IF the Cache_Layer returns a non-200 status code, does not respond within 3 seconds, or the connection cannot be established on the cache write, THE Router SHALL log a WARNING containing `request_id` and the failure reason, and SHALL NOT propagate the cache write failure to the caller.
4. IF `cache.lookup_hit` is `true`, THE Router SHALL NOT dispatch a cache write; the response was already served from cache.
5. THE Router SHALL NOT await the completion of the cache write background task before returning the IMF response to the caller; the caller response and cache write are decoupled.

---

### Requirement 8: Audit Logging

**User Story:** As a compliance officer, I want every routing decision, cache hit, and fallback event recorded in the Audit Store so that the full routing trace for any request is auditable.

#### Acceptance Criteria

1. WHEN the routing pipeline completes successfully (HTTP 200 response), THE Audit_Logger SHALL fire an HTTP POST to `AUDIT_STORE_URL/audit/events` as a background task containing a `routing_decision` audit event with: `request_id`, `layer: "router"`, `event_type: "inference_complete"`, `outcome: "pass"`, `model_used` (value of `routing.selected_model`), `latency_ms` (integer wall-clock milliseconds from receipt of the `/route` request to pipeline completion), and `timestamp_utc` (ISO-8601 UTC).
2. WHEN the routing pipeline completes with an error (HTTP 503 or 500 response), THE Audit_Logger SHALL fire an HTTP POST to `AUDIT_STORE_URL/audit/events` as a background task containing a `routing_decision` audit event with: `request_id`, `layer: "router"`, `event_type: "inference_start"`, `outcome: "error"`, `model_used` (value of `routing.selected_model` at time of failure), `latency_ms`, and `timestamp_utc`.
3. WHEN the Cache_Layer returns a cache HIT during the routing pipeline, THE Audit_Logger SHALL fire an HTTP POST to `AUDIT_STORE_URL/audit/events` as a background task containing a `cache_hit` audit event with: `request_id`, `layer: "router"`, `event_type: "cache_hit"`, `outcome: "pass"`, `model_used` (selected model name), `latency_ms` (integer wall-clock milliseconds), and `timestamp_utc`.
4. WHEN the Fallback_Manager advances to a fallback model, THE Audit_Logger SHALL fire an HTTP POST to `AUDIT_STORE_URL/audit/events` as a background task containing a `routing_fallback` event with: `request_id`, `layer: "router"`, `event_type: "inference_start"`, `outcome: "fallback"`, `model_used` (the model being abandoned — the one that failed), `fallback_level` (current integer value), `latency_ms`, and `timestamp_utc`.
5. THE Audit_Logger SHALL use a 2-second HTTP timeout on every POST to the Audit_Store; if the request exceeds 2 seconds, THE Audit_Logger SHALL cancel the request, log a WARNING containing `request_id` and `"timeout"`, and continue without retrying.
6. IF the Audit_Store returns a non-2xx HTTP response, THE Audit_Logger SHALL log a WARNING containing `request_id` and the received HTTP status code, and SHALL continue normal processing without retrying.
7. IF `AUDIT_STORE_URL` is not set or is an empty string at startup, THEN THE Router SHALL log an ERROR and refuse to start, exiting with a non-zero exit code.


---

### Requirement 9: OpenAI-Compatible Endpoint

**User Story:** As the Agent Framework LangChain client, I want to call a standard `POST /v1/chat/completions` endpoint on the Router so that LangChain's `ChatOpenAI` client can interact with the platform without any custom integration code.

#### Acceptance Criteria

1. WHEN a POST request is received at `/v1/chat/completions` with a JSON body containing a non-empty `messages` array and an optional `model` field, THE Router SHALL construct a valid IMF, populate `user` with POC defaults (`user_id: "poc-user"`, `department: "poc"`, `roles: ["developer"]`, `auth_method: "api_key"`), generate a new `request_id` (UUID-v4), set `governance.content_safety_passed` to `true`, and pass the constructed IMF through the full routing pipeline.
2. WHEN the routing pipeline completes successfully for a `/v1/chat/completions` request, THE Router SHALL return HTTP 200 with `Content-Type: application/json` and an OpenAI-compatible response body containing: `id` (the `request_id`), `object: "chat.completion"`, `created` (Unix epoch integer, seconds), `model` (the value of `routing.selected_model`), `choices` as a JSON array with a single object containing `message.role: "assistant"`, `message.content` (the value of `response.content`), and `finish_reason` (the value of `response.finish_reason`, defaulting to `"stop"` if null), and `usage` with non-negative integer fields `prompt_tokens`, `completion_tokens`, and `total_tokens` from `response.usage`.
3. IF `messages` is absent or is an empty array in the `/v1/chat/completions` request body, THEN THE Router SHALL return HTTP 422 with `{"error": {"code": 422, "message": "messages array is required and must be non-empty"}}`.
4. IF the `/v1/chat/completions` request body contains a `model` field that is a non-null, non-empty string, THE Router SHALL set `routing.routing_mode` to `"pinned"` and `request.model` to that value in the constructed IMF; IF `model` is absent, null, or an empty string, THE Router SHALL set `routing.routing_mode` to `"auto"`.
5. IF the routing pipeline returns a non-200 response for a `/v1/chat/completions` request, THEN THE Router SHALL return the same HTTP status code with an OpenAI-compatible error body `{"error": {"code": <status_code>, "message": "<reason>", "type": "service_unavailable"}}`.
6. THE `/v1/chat/completions` endpoint SHALL NOT require an `X-API-Key` header in the POC; authentication at this endpoint is handled by the API Gateway before the request reaches the Router.


---

### Requirement 10: Health Check Endpoint

**User Story:** As a Kubernetes liveness and readiness probe, I want a lightweight health endpoint on the Router so that the orchestrator can detect when the Router is unavailable before sending traffic to it.

#### Acceptance Criteria

1. WHEN `GET /health` is called and the Router has successfully loaded both `task_classifier_rules.yaml` and `model_matrix.yaml` into memory, THE Router SHALL return HTTP 200 with `Content-Type: application/json` and a JSON body `{"status": "ok", "rules_loaded": <int>, "models_loaded": <int>}` where `rules_loaded` is the total count of keyword entries across all task types and `models_loaded` is the count of entries in the `models` map.
2. IF the Task_Classifier rules or the Model_Matrix failed to load at startup, THEN `GET /health` SHALL return HTTP 503 with `{"status": "degraded", "reason": "<rules_load_failed|matrix_load_failed>"}`.
3. THE Router SHALL complete the `GET /health` response within 200 ms, measured from receipt of the request.
4. THE `GET /health` endpoint SHALL NOT require any authentication header and SHALL NOT trigger any downstream calls to the Cache_Layer, Inference_Adapter, or Audit_Store.

---

### Requirement 11: IMF Field Contract

**User Story:** As a platform engineer, I want the Router to write exactly the defined IMF fields and leave all other IMF fields unchanged so that downstream layers receive a schema-compliant IMF with no unexpected mutations.

#### Acceptance Criteria

1. WHILE the Router is running and both `task_classifier_rules.yaml` and `model_matrix.yaml` have been successfully loaded, THE Router SHALL write the following IMF fields and only these fields during the routing pipeline: `request.task_type`, `routing.selected_model`, `routing.routing_mode`, `routing.fallback_level`, `cache.lookup_hit`, and `cache.cache_key` (see criteria 3 and 4 for response-block exceptions).
2. THE Router SHALL preserve all incoming IMF fields outside the `request.task_type`, `routing`, and `cache` blocks (including `request_id`, `trace_id`, `user`, `governance`, `request.messages`, `request.model`, `request.max_tokens`, `request.temperature`, `metadata`, and `extensions`) unchanged in the outbound IMF.
3. WHEN a cache HIT occurs, THE Router SHALL overwrite `response.content`, `response.finish_reason`, and `response.usage` in the IMF `response` block with the corresponding values from the Cache_Layer response; THE Router SHALL NOT modify any other `response` sub-fields on any routing path.
4. WHEN inference dispatch succeeds, THE Router SHALL use the Inference_Adapter's returned IMF as the basis for the returned IMF, preserving all fields the Inference_Adapter set in the `response` block.
5. IF a cache HIT response from the Cache_Layer is missing `response.content`, `response.finish_reason`, or `response.usage`, THE Router SHALL treat the cache entry as invalid, reset `cache.lookup_hit` to `false`, and proceed to the inference dispatch stage.
6. THE Router SHALL NOT set any field in `governance` or `user`.


---

### Requirement 12: Prometheus Metrics

**User Story:** As a platform SRE, I want the Router to expose Prometheus metrics so that I can monitor routing throughput, cache hit rates, fallback rates, and latency from the central observability stack.

#### Acceptance Criteria

1. THE Router SHALL expose a `/metrics` endpoint on port 9090 that returns a Prometheus text exposition format 0.0.4 response with `Content-Type: text/plain; version=0.0.4; charset=utf-8`.
2. THE Router SHALL increment the counter `llm_router_requests_total` with labels `{outcome, task_type, routing_mode}` on every completed routing pipeline invocation, where `outcome` is one of `"cache_hit"`, `"inference_success"`, `"fallback_success"`, or `"error"`.
3. THE Router SHALL observe the end-to-end wall-clock latency of the routing pipeline (from receipt of the `/route` request to the moment the HTTP response is sent) in the histogram `llm_router_latency_seconds` with labels `{task_type, routing_mode}`, using bucket boundaries `[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 120.0]`.
4. THE Router SHALL increment the counter `llm_router_cache_hits_total` with labels `{task_type, model}` every time a cache HIT is returned and inference is skipped.
5. THE Router SHALL increment the counter `llm_router_fallbacks_total` with labels `{task_type, reason}` where `reason` is one of `"health_check_failed"` or `"inference_error"`, every time the Fallback_Manager advances to the next model.
6. THE Router SHALL increment the counter `llm_router_errors_total` with labels `{error_code}` where `error_code` is one of `"governance_check_failed"`, `"all_backends_exhausted"`, `"invalid_pinned_model"`, or `"internal_error"`, every time the routing pipeline returns a non-200 response.
7. IF port 9090 cannot be bound at startup, THE Router SHALL fail to start and emit a structured JSON error log indicating the metrics port conflict.

---

### Requirement 13: Structured JSON Logging

**User Story:** As a platform SRE, I want every significant Router action logged as structured JSON to stdout so that log aggregation pipelines can index and query routing decisions.

#### Acceptance Criteria

1. THE Router SHALL emit all log entries as single-line JSON objects to stdout; no log entry SHALL span multiple lines.
2. WHEN a routing pipeline completes, THE Router SHALL emit an INFO-level log entry containing at minimum: `request_id`, `task_type`, `selected_model`, `routing_mode`, `cache_hit` (bool), `fallback_level` (int), `outcome`, and `latency_ms`.
3. WHEN the Fallback_Manager advances to a fallback model, THE Router SHALL emit an INFO-level log entry containing: `request_id`, `failed_model`, `fallback_level`, `reason` (one of `"health_check_failed"` or `"inference_error"`).
4. WHEN a cache HIT is returned, THE Router SHALL emit an INFO-level log entry containing: `request_id`, `task_type`, `selected_model`, `cache_hit: true`, and `latency_ms`.
5. THE Router SHALL include a `timestamp` field in ISO-8601 UTC format and a `level` field (one of `DEBUG`, `INFO`, `WARNING`, `ERROR`) in every log entry.
6. THE Router SHALL respect the `LOG_LEVEL` environment variable to set the minimum log level; valid values are `DEBUG`, `INFO`, `WARNING`, `ERROR`; log entries below the configured level SHALL NOT be emitted.
7. IF `LOG_LEVEL` is not set or is set to an unrecognized value, THEN THE Router SHALL default to `INFO` level logging and SHALL NOT refuse to start.
8. THE Router SHALL always emit the routing-decision log entry defined in criterion 2 at INFO level regardless of the configured `LOG_LEVEL`, so that routing outcomes are never suppressed by log level configuration.


---

### Requirement 14: Service Configuration via Environment Variables

**User Story:** As a DevOps engineer, I want the Router configured entirely through environment variables so that no secrets or environment-specific values are hardcoded into the container image.

#### Acceptance Criteria

1. THE Router SHALL read the following environment variables on startup: `LOG_LEVEL`, `MODEL_MATRIX_PATH`, `TASK_RULES_PATH`, `CACHE_URL`, `INFERENCE_ADAPTER_URL`, `AUDIT_STORE_URL`, `INFERENCE_TIMEOUT_SECONDS`, and `HEALTH_CHECK_TIMEOUT_SECONDS`.
2. IF `MODEL_MATRIX_PATH` is not set or is an empty string at startup, THEN THE Router SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code.
3. IF `TASK_RULES_PATH` is not set or is an empty string at startup, THEN THE Router SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code.
4. IF `AUDIT_STORE_URL` is not set or is an empty string at startup, THEN THE Router SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code.
5. WHERE `CACHE_URL` is not set, THE Router SHALL default to `"http://cache:8086"` and log an INFO message indicating the default is being used.
6. WHERE `INFERENCE_ADAPTER_URL` is not set, THE Router SHALL default to `"http://inference-adapter:8087"` and log an INFO message indicating the default is being used.
7. WHERE `INFERENCE_TIMEOUT_SECONDS` is not set, THE Router SHALL default to `120`; IF the value is set to a value outside the range `[1, 600]`, THE Router SHALL log an ERROR and refuse to start.
8. WHERE `HEALTH_CHECK_TIMEOUT_SECONDS` is not set, THE Router SHALL default to `5`; IF the value is set to a value outside the range `[1, 30]`, THE Router SHALL log an ERROR and refuse to start.


---

### Requirement 15: FastAPI Service Structure

**User Story:** As a developer, I want the Router to follow the same FastAPI conventions as the other platform service implementations so that the codebase is consistent and onboarding is straightforward.

#### Acceptance Criteria

1. WHEN the Router starts, THE Router SHALL execute an async lifespan startup handler that (a) loads the file at the path specified by `TASK_RULES_PATH` into `app.state.classifier_rules`, (b) loads the file at the path specified by `MODEL_MATRIX_PATH` into `app.state.model_matrix`, and (c) creates a shared `httpx.AsyncClient` stored on `app.state.http_client`; IF any of these steps fail, THE Router SHALL refuse to start with a non-zero exit code.
2. WHEN the Router shuts down, THE Router SHALL execute an async lifespan shutdown handler that closes the shared `httpx.AsyncClient` before the process exits.
3. THE Router SHALL be organized into the module structure: `router/main.py`, `router/config.py`, `router/routers/route.py`, `router/routers/openai_compat.py`, `router/routers/health.py`, `router/services/classifier.py`, `router/services/model_selector.py`, `router/services/health_checker.py`, `router/services/cache_client.py`, `router/services/inference_client.py`, `router/services/audit_logger.py`, `router/services/fallback_manager.py`, `router/schemas/imf.py`, and `router/middleware/logging.py`.
4. THE Router SHALL run on the port specified by the `PORT` environment variable (default: `8082`; valid range: 1–65535); IF `PORT` is not a valid integer or is outside this range, THE Router SHALL fail to start with a structured JSON error log identifying the invalid value.
5. THE Router SHALL use `httpx.AsyncClient` for all downstream HTTP calls (Cache_Layer, Inference_Adapter, Audit_Store, Health_Checker), with per-call timeouts governed by `HEALTH_CHECK_TIMEOUT_SECONDS` for health checks, `INFERENCE_TIMEOUT_SECONDS` for inference calls, and 3 seconds for cache and audit calls.

---

### Requirement 16: Helm Chart — `llm-platform/charts/router/`

**User Story:** As a platform DevOps engineer, I want a Helm chart for the Router so that it can be deployed consistently to any Kubernetes cluster running the platform.

#### Acceptance Criteria

1. THE Router Helm chart SHALL include the following files: `Chart.yaml`, `values.yaml`, `templates/deployment.yaml`, `templates/service.yaml`, `templates/configmap.yaml`, `templates/networkpolicy.yaml`, `templates/servicemonitor.yaml`, `templates/hpa.yaml`, `templates/_helpers.tpl`, and `README.md`.
2. THE `Chart.yaml` SHALL declare `apiVersion: v2`, `name: router`, `version: 0.1.0`, and `appVersion: "0.1.0"`.
3. THE `values.yaml` SHALL include the following POC defaults: `replicaCount: 1`, `image.repository: registry.local/router`, `image.tag: ""`, `image.pullPolicy: IfNotPresent`, `service.port: 8082`, `env.LOG_LEVEL: "INFO"`, `env.MODEL_MATRIX_PATH: "/config/model_matrix.yaml"`, `env.TASK_RULES_PATH: "/config/task_classifier_rules.yaml"`, `env.CACHE_URL: "http://cache:8086"`, `env.INFERENCE_ADAPTER_URL: "http://inference-adapter:8087"`, `env.AUDIT_STORE_URL: "http://audit-store:9200"`, `env.INFERENCE_TIMEOUT_SECONDS: "120"`, `env.HEALTH_CHECK_TIMEOUT_SECONDS: "5"`, `resources.requests.cpu: "100m"`, `resources.requests.memory: "256Mi"`, `resources.limits.cpu: "500m"`, `resources.limits.memory: "512Mi"`, `observability.metrics.enabled: true`, `observability.metrics.port: 9090`, `autoscaling.enabled: false`, and `vault.enabled: false`.
4. THE `templates/configmap.yaml` SHALL define a ConfigMap containing both `model_matrix.yaml` and `task_classifier_rules.yaml` as data keys, and `templates/deployment.yaml` SHALL mount this ConfigMap as a volume at `/config/` inside the Router container so that the files are accessible at the paths specified by `MODEL_MATRIX_PATH` and `TASK_RULES_PATH`.
5. THE `templates/service.yaml` SHALL expose port 8082 (named `http`) and port 9090 (named `metrics`) as a ClusterIP Service.
6. WHERE `networkPolicy.enabled` is `true` in `values.yaml`, THE `templates/networkpolicy.yaml` SHALL allow ingress to port 8082 only from pods matching label `app.kubernetes.io/name: security-layer`; all other ingress to port 8082 SHALL be denied. For POC, `networkPolicy.enabled` defaults to `false`.
7. THE `templates/servicemonitor.yaml` SHALL configure Prometheus to scrape the `/metrics` endpoint on port 9090 at a 30-second interval, with a label selector matching `app.kubernetes.io/name: router`.
8. THE deployment manifest SHALL define liveness and readiness probes pointing to `GET /health` with `initialDelaySeconds: 15`, `periodSeconds: 15`, `timeoutSeconds: 5`, and `failureThreshold: 3`.


---

## Correctness Properties

The following properties are suitable for property-based testing (using Hypothesis or equivalent) against the Router's pure logic components. All external service calls (Cache_Layer, Inference_Adapter, Audit_Store, Health_Checker) are mocked. Integration tests cover end-to-end behaviour with real downstream services.

---

### Property 1: Task Classification — Keyword Match Invariant

**Applicable Requirements:** Requirement 2

**Property:** For all non-empty message lists where the concatenated content contains at least one keyword from `task_classifier_rules.yaml`, the Task_Classifier always returns a `task_type` that corresponds to the highest-priority rule whose keyword is present (priority order: `code` → `reasoning` → `summarization` → `translation` → `chat`).

**Formal Statement:** For all `messages` arrays `M` and all loaded rule sets `R`, if `concatenate(M)` contains a keyword from rule set `R[task_type_k]`, then `classify(M, R) == k` where `k` is the first task type in priority order whose keywords appear in `concatenate(M)`.

**Testing Approach:** Generate random message lists by injecting known keywords at arbitrary positions. Verify that the returned `task_type` always matches the highest-priority keyword present. Also generate messages with no keywords and verify the result is always `"chat"`.

**Why PBT:** The message content space is large; 100 iterations across varied string lengths, positions, and case variants will find bugs in the priority ordering or substring matching logic that example tests would miss.

---

### Property 2: Task Classification — Default Invariant

**Applicable Requirements:** Requirement 2 (criteria 2, 3)

**Property:** For all message lists whose concatenated content contains no keyword from any rule in `task_classifier_rules.yaml`, the Task_Classifier always returns `"chat"`.

**Formal Statement:** `∀ M. keywords_present(M, R) = ∅ → classify(M, R) = "chat"`

**Testing Approach:** Generate arbitrary strings that are guaranteed to contain none of the configured keywords. Verify the classifier always returns `"chat"`. Combine with the keyword match property to verify mutual exclusivity.

---

### Property 3: Model Selection — Selected Model Always in Matrix (Auto Mode)

**Applicable Requirements:** Requirement 3 (criteria 1, 6)

**Property:** For all valid `task_type` values and `routing_mode = "auto"`, the `selected_model` returned by the model selector is always a key present in the `models` map of the Model_Matrix.

**Formal Statement:** `∀ task_type ∈ valid_task_types, auto_select(task_type, matrix) ∈ keys(matrix.models)`

**Testing Approach:** Generate valid task types (including edge cases like task types with no explicit entry in `task_defaults` that must fall back to `chat`). Verify that the returned model name is always a defined key in the matrix. Verify with both full and minimal model matrices.

**Why PBT:** Subtle bugs in the fallback-to-chat logic or missing task_defaults entries produce incorrect model names that example tests might not cover exhaustively across all task type combinations.

---

### Property 4: IMF Field Preservation Invariant

**Applicable Requirements:** Requirement 11 (criteria 1, 2, 5)

**Property:** For all valid inbound IMF documents, the routing pipeline only modifies the fields `request.task_type`, `routing.selected_model`, `routing.routing_mode`, `routing.fallback_level`, `cache.lookup_hit`, and `cache.cache_key`. All other fields in the returned IMF are byte-identical to their inbound values.

**Formal Statement:** `∀ imf_in, let imf_out = route(imf_in) in ∀ field ∉ WRITE_SET. imf_out[field] = imf_in[field]`

Where `WRITE_SET = {request.task_type, routing.selected_model, routing.routing_mode, routing.fallback_level, cache.lookup_hit, cache.cache_key}`.

**Testing Approach:** Generate arbitrary valid IMF documents with random values in all non-write-set fields. Run the pipeline with a mocked Inference_Adapter that echoes the IMF back. Compare every non-write-set field in the output against the input. Any mutation outside the write set is a test failure.

**Why PBT:** This is a classic preservation invariant. Manual field enumeration in example tests is error-prone as the IMF schema grows. PBT will exhaustively cover all field combinations.

---

### Property 5: Fallback Level Monotonicity

**Applicable Requirements:** Requirement 4 (criteria 3, 4, 7), Requirement 6 (criteria 3, 4)

**Property:** For any routing attempt, `routing.fallback_level` in the returned IMF is a non-negative integer equal to the number of models that were tried minus one (i.e., 0 when the primary model succeeded, 1 when one fallback was used, and so on). The fallback level never decreases during a single request and never exceeds the length of the fallback chain.

**Formal Statement:**
- `fallback_level ≥ 0` always.
- `fallback_level = models_tried - 1`.
- `fallback_level ≤ len(fallback_chain)`.

**Testing Approach:** Generate model matrices with chains of varying lengths (1, 2, 3 models). Simulate health check failures for the first N models using mocked health checkers. Verify that `fallback_level` equals exactly N for every N in `[0, chain_length - 1]`, and that the 503 case sets `fallback_level = chain_length`.

**Why PBT:** Fallback chain traversal is stateful. Generating varied chain lengths and failure positions will catch off-by-one errors in the fallback counter that are difficult to find with a fixed set of examples.

---

### Property 6: OpenAI Compatibility — Response Shape Invariant

**Applicable Requirements:** Requirement 9 (criteria 2, 3, 5)

**Property:** For all valid OpenAI-format request bodies (with a non-empty `messages` array), the `/v1/chat/completions` endpoint always returns a JSON response body that conforms to the OpenAI chat completions schema: containing `id`, `object`, `model`, `choices` (a non-empty array where `choices[0].message.role = "assistant"` and `choices[0].message.content` is a non-null string), and `usage` (with non-negative integer fields `prompt_tokens`, `completion_tokens`, `total_tokens`).

**Formal Statement:** `∀ req ∈ valid_openai_requests, shape(openai_complete(req)) ∈ openai_response_schema`

**Testing Approach:** Generate valid OpenAI request bodies with varied message counts, content lengths, and optional `model` fields (both present and absent). Route through the endpoint with a mocked Inference_Adapter. Verify the response against the schema for every generated input.

**Why PBT:** LangChain's `ChatOpenAI` client is strict about the response schema. Generating diverse request shapes will find serialization bugs in the IMF-to-OpenAI translation layer that example tests with a single fixed request would miss.

---

### Property 7: Cache Lookup Result Consistency

**Applicable Requirements:** Requirement 5 (criteria 2, 3), Requirement 11 (criteria 3)

**Property:** For any routing pipeline invocation where the mocked Cache_Layer returns `{"hit": true, "response": R}`, the pipeline returns an IMF with `cache.lookup_hit = true` and `response.content` equal to the content field in `R`, and the Inference_Adapter is never called. Conversely, when the mocked Cache_Layer returns `{"hit": false}`, the pipeline sets `cache.lookup_hit = false` and calls the Inference_Adapter exactly once.

**Formal Statement:**
- `cache_hit(mock_cache=HIT) → imf_out.cache.lookup_hit = true ∧ inference_calls = 0`
- `cache_hit(mock_cache=MISS) → imf_out.cache.lookup_hit = false ∧ inference_calls = 1`

**Testing Approach:** Generate valid IMF inputs. Mock the Cache_Layer to return HIT or MISS responses (vary the hit/miss decision across iterations). Use a call-counting mock for the Inference_Adapter. Verify the above invariants hold for all generated inputs with both hit and miss outcomes.

**Why PBT:** The cache path is a conditional branch whose correctness depends on the cache response content being correctly threaded into the IMF output. Testing across varied message content and model combinations confirms the threading is correct for all inputs.

