# Requirements Document

## Introduction

This document specifies the requirements for the **Inference Layer** (Layer 5) of the Enterprise On-Premises LLM Platform. The Inference Layer is responsible for running LLM models locally inside Kubernetes and serving completions back through the platform pipeline.

The POC implementation consists of two tightly coupled components:

1. **Ollama** — the inference engine, deployed as a Kubernetes Deployment with a persistent volume for model storage, serving models over its native HTTP API on port 11434.
2. **Inference Adapter** — a lightweight FastAPI service that translates incoming IMF (Internal Message Format) requests into Ollama's `/api/chat` format, calls Ollama, and maps the Ollama response back to IMF before returning it to the Router.

The Helm chart is located at `llm-platform/charts/inference-ollama/`. The Inference Adapter runs on port **8087**. Ollama runs on port **11434**. Prometheus metrics are exposed on port **9090** from the Adapter.

The POC supports non-streaming inference only. The optional vLLM backend (GPU-only) is out of scope for the primary POC but is noted where relevant.

---

## Glossary

- **Inference_Adapter**: The FastAPI application (port 8087) that implements the Inference Layer's external interface. It receives IMF requests, calls Ollama, and returns IMF responses. Also referred to as "the Adapter".
- **Ollama**: The inference engine deployed as a Kubernetes Deployment. Serves its native HTTP API on port 11434. Manages model storage and execution.
- **IMF**: Internal Message Format — the canonical JSON envelope shared by all platform layers (defined in the Master Integration Contract).
- **Ollama_API**: The native HTTP API exposed by the Ollama process at `http://inference-ollama:11434`.
- **Model_Store**: The Kubernetes PersistentVolumeClaim mounted at `/root/.ollama` inside the Ollama container, providing durable storage for pulled model weights.
- **Init_Job**: A Kubernetes init container or post-deploy Job that pulls the configured model list into the Model_Store before the Ollama container declares itself ready.
- **Router**: The Intelligent Router (Layer 3) that dispatches inference requests to the Inference_Adapter and reads the IMF response.
- **selected_model**: The value of `routing.selected_model` in the incoming IMF, identifying the Ollama model tag to use (e.g., `llama3.2:3b`).
- **inference_latency_ms**: The wall-clock milliseconds elapsed from the moment the Inference_Adapter dispatches the Ollama request until the last byte of the response body is received.
- **total_duration_ns**: The `total_duration` field in the Ollama `/api/chat` response body, expressed in nanoseconds.
- **audit_event**: A structured JSON log entry emitted to stdout by the Inference_Adapter and captured by Kubernetes logging infrastructure.

---

## Requirements

---

### Requirement 1: Inference Request Endpoint

**User Story:** As the Intelligent Router, I want to submit an IMF request to the Inference Layer and receive an IMF response containing the model's completion, so that the platform can serve LLM responses without coupling the Router to any specific inference engine.

#### Acceptance Criteria

1. WHEN a `POST /infer` request is received with a valid IMF document, THE Inference_Adapter SHALL return HTTP 200 with a valid IMF document with the `response` block populated.
2. WHEN a `POST /infer` request is received with a valid IMF document, THE Inference_Adapter SHALL extract `routing.selected_model`, `request.messages`, `request.max_tokens`, and `request.temperature` to construct the Ollama request body.
3. WHEN constructing the Ollama request body, THE Inference_Adapter SHALL set `"model"` to `routing.selected_model`, `"messages"` to `request.messages`, `"stream"` to `false`, and `"options"` to `{"num_predict": request.max_tokens, "temperature": request.temperature}`.
4. WHEN `request.max_tokens` is null or absent, THE Inference_Adapter SHALL default the Ollama `options.num_predict` value to `2048`.
5. WHEN `request.temperature` is null or absent, THE Inference_Adapter SHALL default the Ollama `options.temperature` value to `0.7`.
6. WHEN Ollama returns HTTP 200 with a non-empty `message.content` field, THE Inference_Adapter SHALL return HTTP 200 with the incoming IMF document updated to include the populated `response` block and the `metadata` fields defined in Requirement 3.
7. IF the incoming IMF document is missing `routing.selected_model` or `request.messages`, THEN THE Inference_Adapter SHALL return HTTP 422 with a structured error body listing the missing fields and SHALL NOT call Ollama.
8. IF `routing.selected_model` is set to a model name not present in the Ollama model list at request time, THEN THE Inference_Adapter SHALL return HTTP 422 with `{"event": "model_not_loaded", "model": "<selected_model>", "request_id": "<request_id from IMF>"}` and SHALL NOT call Ollama.
9. IF Ollama returns a non-200 HTTP response to the `/api/chat` request, THEN THE Inference_Adapter SHALL return HTTP 502 with `{"event": "ollama_backend_error", "ollama_status": <code>, "request_id": "<request_id from IMF>"}`.
10. IF the Inference_Adapter cannot connect to Ollama within the configured timeout, THEN THE Inference_Adapter SHALL return HTTP 504 with `{"event": "ollama_timeout", "request_id": "<request_id from IMF>"}`.

---

### Requirement 2: IMF Response Mapping

**User Story:** As a platform engineer, I want the Inference_Adapter to produce a fully populated IMF response block from the Ollama response, so that all downstream layers (governance, audit, cache write) operate on a consistent schema regardless of which inference backend was used.

#### Acceptance Criteria

1. WHEN Ollama returns HTTP 200 with a parseable response body containing `message`, `done_reason`, `prompt_eval_count`, and `eval_count`, THE Inference_Adapter SHALL set `response.content` to the value of `message.content` from the Ollama response body.
2. WHEN Ollama returns a valid `/api/chat` response, THE Inference_Adapter SHALL set `response.finish_reason` to `"stop"` when `done_reason` equals `"stop"`, to `"length"` when `done_reason` equals `"length"`, and to `null` for any other `done_reason` value or when `done_reason` is absent.
3. WHEN Ollama returns a valid `/api/chat` response, THE Inference_Adapter SHALL set `response.usage.prompt_tokens` to the integer value of `prompt_eval_count`, `response.usage.completion_tokens` to the integer value of `eval_count`, and `response.usage.total_tokens` to their sum.
4. WHEN the Ollama response body omits `prompt_eval_count` or `eval_count`, THE Inference_Adapter SHALL set the missing token count(s) to `0` and `response.usage.total_tokens` to the sum of the two resolved values.
5. THE Inference_Adapter SHALL NOT modify any IMF field outside `response`, `metadata`, and `extensions` when constructing the outbound response.
6. THE Inference_Adapter SHALL preserve all incoming IMF fields (`request_id`, `trace_id`, `user`, `governance`, `routing`, `cache`) unchanged in the outbound IMF document.
7. WHEN Ollama returns HTTP 200 with a parseable response body, THE Inference_Adapter SHALL set `metadata.inference_backend` to `"ollama"` and `metadata.inference_latency_ms` to `floor(total_duration / 1_000_000)` where `total_duration` is in nanoseconds.
8. IF the Ollama response body is missing `message` or `message.content`, THEN THE Inference_Adapter SHALL return HTTP 502 with `{"event": "ollama_invalid_response", "request_id": "<request_id from IMF>"}` and SHALL NOT populate the `response` block.

---

### Requirement 3: Inference Metadata Population

**User Story:** As a platform operator, I want inference backend and latency metadata attached to every IMF response, so that downstream layers and observability tooling can identify which backend served the request and how long it took.

#### Acceptance Criteria

1. WHEN Ollama returns HTTP 200 with a parseable response body, THE Inference_Adapter SHALL set `metadata.inference_backend` to the string `"ollama"`.
2. WHEN Ollama returns HTTP 200 with a parseable response body containing a `total_duration` field greater than 0, THE Inference_Adapter SHALL set `metadata.inference_latency_ms` to `floor(total_duration / 1_000_000)`.
3. WHEN the Ollama response body omits `total_duration` or `total_duration` is zero or negative, THE Inference_Adapter SHALL set `metadata.inference_latency_ms` to the wall-clock milliseconds elapsed from the moment the Ollama request was dispatched until the last byte of the response body was received, rounded down to the nearest integer.
4. WHEN Ollama returns HTTP 200 with a parseable response body and `routing.selected_model` is present and non-null in the incoming IMF, THE Inference_Adapter SHALL set `metadata.model_name` to the value of `routing.selected_model`. IF `routing.selected_model` is null or absent, THE Inference_Adapter SHALL set `metadata.model_name` to `null`.
5. IF Ollama returns a non-200 response or an unparseable body, THE Inference_Adapter SHALL still set `metadata.inference_backend` to `"ollama"` and `metadata.inference_latency_ms` to the measured wall-clock elapsed milliseconds, and SHALL omit `metadata.model_name`.
6. THE Inference_Adapter SHALL NOT set any `metadata` field other than `inference_backend`, `inference_latency_ms`, and `model_name` unless explicitly extended in a future requirement.

---

### Requirement 4: Health Endpoint

**User Story:** As a platform operator and as the Kubernetes liveness/readiness probe, I want a health endpoint on the Inference_Adapter that validates both the adapter process and the Ollama backend, so that unhealthy pods are removed from service before the Router sends traffic to them.

#### Acceptance Criteria

1. THE Inference_Adapter SHALL expose a `GET /health` endpoint that returns HTTP 200 when (a) the HTTP server is accepting connections AND (b) the last Ollama `/api/tags` check passed.
2. WHEN `GET /health` is called, THE Inference_Adapter SHALL issue a `GET http://inference-ollama:11434/api/tags` request to Ollama with a 5-second timeout and parse the returned model list.
3. WHEN the Ollama `/api/tags` response returns HTTP 200 and the model list contains at least one entry matching the value of the `DEFAULT_MODEL` environment variable, THE Inference_Adapter SHALL return HTTP 200 with `{"status": "ok", "backend": "ollama", "model": "<DEFAULT_MODEL>"}`.
4. IF the Ollama `/api/tags` request fails, times out, or returns a non-200 status code, THEN THE Inference_Adapter SHALL return HTTP 503 with `{"status": "unavailable", "reason": "ollama_unreachable"}`.
5. IF the Ollama `/api/tags` request succeeds but the `DEFAULT_MODEL` value is not present in the returned model list, THEN THE Inference_Adapter SHALL return HTTP 503 with `{"status": "unavailable", "reason": "model_not_loaded", "model": "<DEFAULT_MODEL>"}`.
6. WHILE the Inference_Adapter is still completing startup initialization (capped at 30 seconds from process start), THE `GET /health` endpoint SHALL return HTTP 503 with `{"status": "starting"}`.

---

### Requirement 5: Ollama Backend Deployment

**User Story:** As a platform engineer, I want Ollama deployed as a Kubernetes Deployment with a persistent volume for model storage, so that model weights survive pod restarts and do not need to be re-downloaded on every restart.

#### Acceptance Criteria

1. THE Ollama Deployment SHALL use the `ollama/ollama` container image with `pullPolicy: IfNotPresent` and declare container port `11434`.
2. THE Ollama Deployment SHALL mount a PersistentVolumeClaim of at least `20Gi` with `accessMode: ReadWriteOnce` at path `/root/.ollama` inside the container.
3. THE Ollama Deployment SHALL set the environment variable `OLLAMA_HOST` to `"0.0.0.0"` so that the Ollama HTTP API accepts connections from within the cluster.
4. THE Ollama Deployment SHALL set the environment variable `OLLAMA_KEEP_ALIVE` to `"24h"` so that loaded models remain resident in memory between requests for at least 24 hours.
5. THE Ollama Deployment SHALL define resource requests of at minimum `cpu: "1"` and `memory: "8Gi"`, and resource limits of at maximum `cpu: "4"` and `memory: "16Gi"`.
6. THE Ollama Deployment SHALL set `replicaCount: 1` for the POC, with autoscaling disabled.
7. THE Helm chart SHALL define a ClusterIP Service named `inference-ollama` exposing port `11434` so that `http://inference-ollama:11434` resolves within the `llm-platform` namespace.
8. THE Ollama Deployment manifest SHALL define readiness and liveness probes on `GET /api/tags` at port `11434` with `initialDelaySeconds: 30`, `periodSeconds: 15`, `timeoutSeconds: 30`, and `failureThreshold: 5`.
9. THE Helm chart SHALL include a model pre-load init container or post-deploy Job that pulls configured models into the Model_Store before the Ollama pod is declared Ready.

---

### Requirement 6: Model Pre-Loading

**User Story:** As a platform engineer, I want the configured models to be pulled into the Model_Store before the Ollama pod declares itself ready, so that the first inference request is not delayed by a model download.

#### Acceptance Criteria

1. THE Helm chart SHALL include an Init_Job that runs before the Ollama container enters the `Ready` state and pulls each model listed in `models.preload`, waiting for each pull to signal completion (Ollama signals the model is fully downloaded) before starting the next.
2. WHEN the `models.preload` list contains more than one model name, THE Init_Job SHALL pull each model sequentially, with a per-model pull timeout of 600 seconds.
3. IF a model name in `models.preload` is already present in the Model_Store, THE Ollama process SHALL skip the download and return a successful response without re-downloading the weights.
4. THE default `models.preload` list in `values.yaml` SHALL include `llama3.2:3b` as the baseline POC model.
5. IF a model pull fails during Init_Job execution (e.g., network error, invalid model name, timeout), THEN THE Init_Job SHALL emit a structured JSON log event with `event: "model_pull_failed"`, `model: "<name>"`, and `reason: "<error>"`, and SHALL exit with a non-zero exit code so that Kubernetes retries the job.
6. IF the `models.preload` list is empty, THE Init_Job SHALL exit with code 0 without calling the Ollama pull API.

---

### Requirement 7: IMF Translation — Request

**User Story:** As a platform engineer, I want the Inference_Adapter to translate a well-formed IMF request into the Ollama `/api/chat` wire format, so that Ollama receives exactly the fields it needs and nothing more.

#### Acceptance Criteria

1. WHEN constructing the Ollama request body, THE Inference_Adapter SHALL set `model` to the value of `routing.selected_model` from the incoming IMF (not `request.model`), and translate the IMF `request.messages` array to the Ollama `messages` field without modifying message `role` or `content` values.
2. THE Inference_Adapter SHALL set the Ollama `stream` field to `false` for all POC requests.
3. WHEN the IMF `request.messages` array is empty, THE Inference_Adapter SHALL return HTTP 422 with `{"event": "empty_messages", "request_id": "<request_id from IMF>"}` and SHALL NOT call Ollama.
4. THE Inference_Adapter SHALL include only `model`, `messages`, `stream`, and `options` fields in the Ollama request body, omitting all IMF governance, routing, cache, and user fields.
5. IF `request.max_tokens` exceeds `4096` and is greater than 0, THE Inference_Adapter SHALL clamp `options.num_predict` to `4096` and log a structured JSON warning with `event: "max_tokens_clamped"`, `requested: <value>`, and `clamped_to: 4096`.
6. IF `request.max_tokens` is 0, null, or absent, THE Inference_Adapter SHALL set `options.num_predict` to the `DEFAULT_MAX_TOKENS` configuration value (default: `2048`).
7. WHEN constructing the Ollama request body, THE Inference_Adapter SHALL set `options.temperature` to the value of `request.temperature`. IF `request.temperature` is null or absent, THE Inference_Adapter SHALL set `options.temperature` to the `DEFAULT_TEMPERATURE` configuration value (default: `0.7`).
8. WHEN `request.max_tokens` is a positive integer in the range `[1, 4096]`, THE Inference_Adapter SHALL pass the value to `options.num_predict` without modification.

---

### Requirement 8: IMF Translation — Response (Round-Trip Integrity)

**User Story:** As a platform engineer, I want the IMF response produced by the Inference_Adapter to be the stable, lossless result of parsing the Ollama response and mapping it to the IMF schema, so that any two calls with identical Ollama responses produce byte-identical IMF response blocks.

#### Acceptance Criteria

1. WHEN Ollama returns a valid `/api/chat` response body containing `message.content`, `done_reason`, `prompt_eval_count`, and `eval_count`, THE Inference_Adapter SHALL produce an IMF `response` block where `content` equals `message.content`, `finish_reason` is one of `"stop"`, `"length"`, or `null`, and `usage.total_tokens` equals `usage.prompt_tokens` plus `usage.completion_tokens`.
2. IF the Ollama response body is missing any of `message.content`, `done_reason`, `prompt_eval_count`, or `eval_count`, THE Inference_Adapter SHALL return HTTP 502 with `{"event": "ollama_invalid_response", "request_id": "<request_id from IMF>"}` and SHALL NOT return a partial `response` block.
3. WHEN a valid Ollama response body contains a `done_reason` value other than `"stop"` or `"length"`, THE Inference_Adapter SHALL set `response.finish_reason` to `null`.
4. WHEN the same Ollama response body is presented to the IMF mapping logic more than once, THE Inference_Adapter SHALL produce an IMF `response` block with field values and types identical across all invocations, with no random fields, no timestamps, and no non-deterministic data injected into `response`.
5. THE Inference_Adapter SHALL preserve string types as strings, integer types as integers, and null as null when mapping Ollama response fields to IMF `response` fields, with no implicit type coercion. IF a field arrives with an unexpected type, the request SHALL be rejected per Criterion 2.

---

### Requirement 9: Error Handling and Ollama Failure Modes

**User Story:** As the Intelligent Router, I want the Inference_Adapter to return structured, actionable error responses when Ollama is unavailable or returns an error, so that the Router can apply fallback logic without parsing unstructured error text.

#### Acceptance Criteria

1. IF the Inference_Adapter cannot connect to Ollama within the `OLLAMA_TIMEOUT_SECONDS` timeout, THEN THE Inference_Adapter SHALL return HTTP 503 with `Content-Type: application/json` and body `{"event": "ollama_unreachable", "request_id": "<request_id from IMF>"}`.
2. IF Ollama returns an HTTP 400–499 response to the `/api/chat` request, THEN THE Inference_Adapter SHALL return HTTP 422 with `Content-Type: application/json` and body `{"event": "ollama_request_rejected", "ollama_status": <code>, "request_id": "<request_id from IMF>"}`.
3. IF Ollama returns an HTTP 500–599 response to the `/api/chat` request, THEN THE Inference_Adapter SHALL return HTTP 502 with `Content-Type: application/json` and body `{"event": "ollama_backend_error", "ollama_status": <code>, "request_id": "<request_id from IMF>"}`.
4. IF the Ollama response body cannot be parsed as valid JSON, THEN THE Inference_Adapter SHALL return HTTP 502 with `Content-Type: application/json` and body `{"event": "ollama_invalid_response", "request_id": "<request_id from IMF>"}`.
5. IF an unhandled Python exception occurs, THE Inference_Adapter SHALL catch it and return HTTP 500 with `Content-Type: application/json` and body `{"event": "internal_error", "request_id": "<request_id from IMF>"}`.
6. WHEN any error in Criteria 1–5 occurs, THE Inference_Adapter SHALL emit a structured JSON log entry with fields `event` (matching the error event name), `request_id`, `exception_type`, and `exception_message` to stdout.

---

### Requirement 10: Structured Logging and Audit Events

**User Story:** As a platform operator, I want every inference call to emit structured JSON audit events to stdout, so that `inference_start` and `inference_complete` events are captured by the Kubernetes logging infrastructure and forwarded to the audit store.

#### Acceptance Criteria

1. WHEN a `POST /infer` request is received, THE Inference_Adapter SHALL emit a structured JSON log entry with `event: "inference_start"`, `request_id`, `model` (value of `routing.selected_model`), and `timestamp_utc` (ISO-8601 with UTC `Z` suffix) before calling Ollama.
2. WHEN an Ollama response is received successfully, THE Inference_Adapter SHALL emit a structured JSON log entry with `event: "inference_complete"`, `request_id`, `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, and `latency_ms` (wall-clock from receipt of the `POST /infer` request to the moment this log entry is emitted, as a non-negative integer).
3. IF an Ollama call fails for any reason, THE Inference_Adapter SHALL emit a structured JSON log entry with `event: "inference_error"`, `request_id`, `model`, `error_code` (matching the event name from Requirement 9), and `latency_ms` (wall-clock from receipt of the `POST /infer` request to the moment this log entry is emitted).
4. WHEN any HTTP request is processed, THE Inference_Adapter SHALL emit one structured JSON log entry to stdout via its `LoggingMiddleware` containing `request_id`, `method`, `path`, `status_code`, and `latency_ms`. This applies to all endpoints including `/health` and `/metrics`.
5. THE Inference_Adapter SHALL emit all log entries to stdout as newline-delimited JSON (each entry on a single line terminated by `\n`) at the log level configured by the `LOG_LEVEL` environment variable (valid values: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`; default: `"INFO"`; invalid values SHALL fall back to `"INFO"`).
6. THE Inference_Adapter SHALL omit the field key entirely (not redact or null it) for any field name listed in the IMF `governance.pii_fields_detected` array, and SHALL NOT include the raw string values of `request.messages[].content` in any log entry at any log level.

---

### Requirement 11: Prometheus Metrics

**User Story:** As a platform operator, I want the Inference_Adapter to expose standardized Prometheus metrics on a dedicated port, so that inference throughput, latency, and error rates are observable through the shared platform observability stack.

#### Acceptance Criteria

1. WHEN the Inference_Adapter starts successfully, THE Inference_Adapter SHALL expose a `/metrics` endpoint on port `9090` returning Prometheus text-format exposition data with `Content-Type: text/plain; version=0.0.4; charset=utf-8`.
2. THE Inference_Adapter SHALL emit the counter metric `llm_inference_requests_total` with labels `{status="success|error", model, task_type, department}` incremented on every completed or failed inference request.
3. THE Inference_Adapter SHALL emit the histogram metric `llm_inference_latency_seconds` with labels `{model, task_type, department}` covering the end-to-end wall-clock latency from Ollama call initiation to response receipt, using bucket boundaries `[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]`.
4. THE Inference_Adapter SHALL emit the counter metric `llm_inference_errors_total` with labels `{error_code, model, department}` where `error_code` is one of `"ollama_unreachable"`, `"ollama_error_response"`, or `"ollama_unparseable_body"`, incremented on every corresponding failure.
5. WHEN an inference request completes (successfully or with error), THE Inference_Adapter SHALL update all applicable metric counters and histograms before returning the HTTP response to the caller.
6. IF port `9090` cannot be bound at startup, THE Inference_Adapter SHALL fail to start and emit a structured JSON error log indicating the metrics port conflict.

---

### Requirement 12: Helm Chart and Kubernetes Deployment

**User Story:** As a platform engineer, I want the Inference Layer packaged as a Helm chart at `llm-platform/charts/inference-ollama/`, so that Ollama and the Inference_Adapter can be deployed, configured, and torn down together as a single unit using the platform's standard Helm conventions.

#### Acceptance Criteria

1. THE Helm chart SHALL be located at `llm-platform/charts/inference-ollama/` and SHALL contain `Chart.yaml`, `values.yaml`, `README.md`, and a `templates/` directory with `deployment.yaml` (Ollama), `adapter-deployment.yaml` (Inference_Adapter), `service.yaml`, `networkpolicy.yaml`, `servicemonitor.yaml`, and `_helpers.tpl`.
2. THE `values.yaml` SHALL include the following POC defaults: `replicaCount: 1`, `ollama.image.repository: "ollama/ollama"`, `ollama.image.tag: "latest"`, `ollama.service.port: 11434`, `adapter.image.repository: "registry.internal/inference-adapter"`, `adapter.service.port: 8087`, `models.preload: ["llama3.2:3b"]`, `persistence.enabled: true`, `persistence.size: "20Gi"`, `env.OLLAMA_HOST: "0.0.0.0"`, `env.OLLAMA_KEEP_ALIVE: "24h"`, `env.DEFAULT_MODEL: "llama3.2:3b"`, `env.LOG_LEVEL: "INFO"`, `autoscaling.enabled: false`, and `vault.enabled: false`.
3. THE Helm chart SHALL define a `NetworkPolicy` in the `llm-platform` namespace that permits ingress to the Inference_Adapter only from pods matching `app.kubernetes.io/name: router`, and permits egress from the Inference_Adapter only to pods matching `app.kubernetes.io/name: inference-ollama`.
4. THE Helm chart SHALL define a `ServiceMonitor` that configures Prometheus to scrape the Inference_Adapter's `/metrics` endpoint on port `9090` with `interval: 30s`.
5. THE Inference_Adapter deployment manifest SHALL define liveness and readiness probes pointing to `GET /health` with `initialDelaySeconds: 20`, `periodSeconds: 15`, `timeoutSeconds: 5`, and `failureThreshold: 3`.
6. THE Ollama deployment manifest SHALL define a readiness probe pointing to `GET /api/tags` on port `11434` with `initialDelaySeconds: 30`, `periodSeconds: 15`, `timeoutSeconds: 5`, and `failureThreshold: 5`.
7. IF the Helm chart image tag is not explicitly overridden via `--set adapter.image.tag=<sha>` at deploy time, THEN the Inference_Adapter deployment SHALL use the tag value `"latest"` as a fallback, and `pullPolicy` SHALL always be `IfNotPresent`.

---

### Requirement 13: FastAPI Service Structure

**User Story:** As a developer, I want the Inference_Adapter to follow the same FastAPI conventions as the other platform service implementations, so that the codebase is consistent and onboarding is straightforward.

#### Acceptance Criteria

1. WHEN the Inference_Adapter starts, THE Inference_Adapter SHALL execute an async lifespan startup handler that performs an initial Ollama health check and stores `app.state.ollama_reachable` (boolean) and `app.state.ollama_models` (list) on the application state. IF Ollama is unreachable at startup, THE Inference_Adapter SHALL log a structured JSON warning and continue in degraded mode rather than refusing to start.
2. WHEN the Inference_Adapter shuts down, THE Inference_Adapter SHALL execute an async lifespan shutdown handler that closes all open `httpx.AsyncClient` sessions before the process exits.
3. THE Inference_Adapter SHALL be organized into the module structure: `inference_adapter/main.py`, `inference_adapter/config.py`, `inference_adapter/routers/infer.py`, `inference_adapter/routers/health.py`, `inference_adapter/schemas/imf.py`, `inference_adapter/services/ollama_client.py`, `inference_adapter/services/imf_mapper.py`, and `inference_adapter/middleware/logging.py`.
4. THE Inference_Adapter SHALL run on the port specified by the `PORT` environment variable (default: `8087`; valid range: 1–65535). IF `PORT` is set to a value outside this range, THE service SHALL fail to start and emit a structured JSON error log indicating an invalid port configuration.
5. THE Inference_Adapter SHALL use `httpx.AsyncClient` for all calls to the Ollama API, with a timeout set to the `OLLAMA_TIMEOUT_SECONDS` configuration value. IF `OLLAMA_TIMEOUT_SECONDS` is set to a value outside the range [1, 600], THE service SHALL fail to start and emit a structured JSON error log indicating an invalid timeout configuration.
6. IF the `httpx.AsyncClient` call to Ollama raises a connection error, timeout, or any transport-level exception, THE Inference_Adapter SHALL catch the exception and return HTTP 503 with `{"event": "ollama_unreachable", "request_id": "<request_id from IMF>"}`, and SHALL emit a structured JSON log entry with `request_id` and the failure reason.

---

### Requirement 14: Non-Streaming Inference (POC Scope)

**User Story:** As a platform engineer, I want the POC to support non-streaming inference only, so that the implementation is straightforward and end-to-end validation can proceed without SSE streaming complexity.

#### Acceptance Criteria

1. THE Inference_Adapter SHALL set `"stream": false` in all Ollama `/api/chat` request bodies, regardless of the value of `request.stream` in the incoming IMF document.
2. IF `request.stream` is `true` in the incoming IMF, THE Inference_Adapter SHALL log a structured JSON warning with `event: "streaming_not_supported"` and `request_id`.
3. IF `request.stream` is `true` in the incoming IMF, THE Inference_Adapter SHALL proceed with non-streaming inference (enforcing `"stream": false` in the Ollama call) and return the complete response as a single JSON object.
4. THE Inference_Adapter SHALL return the complete inference response as a single JSON object with HTTP 200 in all success cases, with no chunked or SSE transfer encoding in the POC.

---

### Requirement 15: Configuration

**User Story:** As a platform operator, I want all Inference_Adapter behavior to be driven by environment variables with safe defaults, so that the service can be configured at deploy time without rebuilding the container image.

#### Acceptance Criteria

1. THE Inference_Adapter SHALL read all configuration from environment variables, applying the specified defaults when a variable is absent.
2. THE Inference_Adapter configuration SHALL include: `OLLAMA_BASE_URL` (default: `"http://inference-ollama:11434"`), `DEFAULT_MODEL` (default: `"llama3.2:3b"`), `OLLAMA_TIMEOUT_SECONDS` (default: `120`, integer in [1, 600]), `MAX_TOKENS_LIMIT` (default: `4096`, positive integer), `DEFAULT_MAX_TOKENS` (default: `2048`, positive integer), `DEFAULT_TEMPERATURE` (default: `0.7`, float in [0.0, 2.0]), `LOG_LEVEL` (default: `"INFO"`, one of `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`), and `PORT` (default: `8087`, integer in [1, 65535]).
3. IF `OLLAMA_TIMEOUT_SECONDS` is set to a value outside [1, 600], THEN THE Inference_Adapter SHALL fail to start and emit a structured JSON error log indicating an invalid timeout configuration.
4. IF `DEFAULT_TEMPERATURE` is set to a value outside [0.0, 2.0], THEN THE Inference_Adapter SHALL fail to start and emit a structured JSON error log indicating an invalid temperature configuration.
5. IF `MAX_TOKENS_LIMIT`, `DEFAULT_MAX_TOKENS`, or `PORT` is set to a non-positive integer or a value outside its valid range, THEN THE Inference_Adapter SHALL fail to start and emit a structured JSON error log naming the invalid variable.
6. IF `OLLAMA_BASE_URL` is set to a value that is not a well-formed HTTP or HTTPS URL, THEN THE Inference_Adapter SHALL fail to start and emit a structured JSON error log indicating a malformed base URL.
7. THE `DEFAULT_MAX_TOKENS` value SHALL be less than or equal to `MAX_TOKENS_LIMIT`; IF `DEFAULT_MAX_TOKENS` exceeds `MAX_TOKENS_LIMIT` at startup, THE Inference_Adapter SHALL fail to start and emit a structured JSON error log.
