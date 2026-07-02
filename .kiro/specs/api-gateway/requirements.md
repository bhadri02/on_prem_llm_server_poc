# Requirements Document

## Introduction

This document defines the requirements for Layer 1 — API Gateway (POC) of the Enterprise On-Premises LLM Platform. The API Gateway is the single ingress point for all LLM traffic from enterprise consumer applications. It accepts OpenAI-compatible HTTP requests, authenticates callers via a static API key, applies in-memory rate limiting, normalizes payloads into the Internal Message Format (IMF), and forwards them downstream to the Security & Governance Layer. Responses from downstream are serialized back to OpenAI-compatible JSON before being returned to the client.

This is a Proof-of-Concept implementation focused on functional correctness over production hardening. Production concerns such as TLS, OIDC/OAuth2, Redis-backed rate limiting, mTLS, gRPC, and HPA are explicitly deferred to Phase 2.

---

## Glossary

- **API_Gateway**: The FastAPI-based Layer 1 service defined in this document. The single ingress point for all LLM requests.
- **Auth_Middleware**: The FastAPI middleware component responsible for validating the `X-Api-Key` header against `GATEWAY_API_KEY`.
- **Rate_Limiter**: The in-memory middleware component that enforces the 60 requests/minute per-key sliding window limit.
- **Request_Normalizer**: The FastAPI middleware that parses an OpenAI-compatible client payload and constructs an IMF object.
- **Response_Serializer**: The FastAPI middleware that converts an IMF response from downstream back to an OpenAI-compatible JSON response.
- **IMF**: Internal Message Format — the canonical JSON structure used for all inter-layer communication on this platform. Defined in `00-platform-master-contract.md`.
- **Security_Layer**: The downstream Layer 2 service reachable at `http://security-layer:8081/process`. Receives the IMF JSON via HTTP POST.
- **OpenAI_Payload**: A JSON request body conforming to the OpenAI Chat Completions API schema (fields: `model`, `messages`, `stream`, `max_tokens`, `temperature`).
- **SSE**: Server-Sent Events — the streaming protocol used to proxy inference responses back to the client when `stream: true`.
- **Audit_Event**: A structured JSON record written to stdout representing a significant lifecycle event within the API Gateway.
- **GATEWAY_API_KEY**: Environment variable holding the single accepted API key for POC authentication.
- **DOWNSTREAM_SECURITY_URL**: Environment variable holding the base URL of the Security Layer (default: `http://security-layer:8081`).
- **LOG_LEVEL**: Environment variable controlling the minimum log level (default: `INFO`).
- **request_id**: A UUID v4 generated per request by the API Gateway, used to correlate all log and audit records for that request.
- **trace_id**: For POC, set equal to `request_id`. Propagated throughout the IMF.
- **ClusterIP**: Kubernetes service type that exposes the service only within the cluster.
- **Helm_Chart**: The Kubernetes deployment packaging under `llm-platform/charts/api-gateway/`.

---

## Requirements

### Requirement 1: API Endpoint Routing

**User Story:** As an enterprise application developer, I want a versioned REST API endpoint that accepts OpenAI-compatible chat completion requests, so that I can integrate with the platform without modifying my existing OpenAI client code.

#### Acceptance Criteria

1. THE API_Gateway SHALL expose a `POST /v1/chat/completions` endpoint that accepts a JSON request body with `Content-Type: application/json`, where the `model` field is optional (treated as `null` if absent) and the `messages` field is required.
2. THE API_Gateway SHALL expose a `GET /v1/models` endpoint that returns HTTP 200 with a JSON body conforming to the OpenAI models list format: `{"object": "list", "data": [{"id": "<model-name>", "object": "model"}, ...]}` containing the static list of available models.
3. THE API_Gateway SHALL expose a `GET /health` endpoint that returns HTTP 200 with a JSON body `{"status": "ok"}`.
4. WHEN a request is received at an undefined path, THE API_Gateway SHALL return HTTP 404.
5. WHEN a request body for `POST /v1/chat/completions` is missing the required `messages` field, THE API_Gateway SHALL return HTTP 400 with body `{"error": {"code": "400", "message": "Bad request"}}`.
6. WHEN a request body for `POST /v1/chat/completions` contains a `messages` field that is not a non-empty array, THE API_Gateway SHALL return HTTP 400 with body `{"error": {"code": "400", "message": "Bad request"}}`.
7. WHEN a request uses an HTTP method not supported by a known path (e.g., `GET /v1/chat/completions`), THE API_Gateway SHALL return HTTP 405 Method Not Allowed.
8. WHEN a request arrives at `POST /v1/chat/completions` or `GET /v1/models` without a valid `X-Api-Key` header, THE API_Gateway SHALL return HTTP 401 with body `{"error": {"code": "401", "message": "Unauthorized"}}`.
9. WHEN a caller exceeds 60 requests per minute for a given API key on `POST /v1/chat/completions`, THE API_Gateway SHALL return HTTP 429 with body `{"error": {"code": "429", "message": "Rate limit exceeded"}}`.

---

### Requirement 2: Static API Key Authentication

**User Story:** As a platform operator, I want all API requests to be authenticated with a static API key, so that only authorized consumers can send requests to the gateway during the POC phase.

#### Acceptance Criteria

1. WHEN the API_Gateway starts and the `GATEWAY_API_KEY` environment variable is not set or is an empty string, THE API_Gateway SHALL fail to start and log an error indicating that the required configuration is missing.
2. WHEN a request arrives at `POST /v1/chat/completions` or `GET /v1/models`, THE Auth_Middleware SHALL extract the value of the `X-Api-Key` HTTP header.
3. WHEN the `X-Api-Key` header value matches the value of the `GATEWAY_API_KEY` environment variable (non-empty, exact string match), THE Auth_Middleware SHALL allow the request to proceed to the next middleware stage.
4. WHEN the `X-Api-Key` header is absent or contains an empty string, THE Auth_Middleware SHALL return HTTP 401 with body `{"error": {"code": "401", "message": "Unauthorized"}}` and SHALL NOT forward the request downstream.
5. WHEN the `X-Api-Key` header value does not match the `GATEWAY_API_KEY` environment variable, THE Auth_Middleware SHALL return HTTP 401 with body `{"error": {"code": "401", "message": "Unauthorized"}}` and SHALL NOT forward the request downstream.
6. IF the request path is `GET /health`, THEN THE Auth_Middleware SHALL allow the request to proceed without inspecting the `X-Api-Key` header.
7. WHEN authentication succeeds, THE Auth_Middleware SHALL emit an `auth_pass` Audit_Event to stdout containing at minimum `event_type`, `request_id`, `user_id`, and `timestamp_utc`.
8. WHEN authentication fails, THE Auth_Middleware SHALL emit an `auth_fail` Audit_Event to stdout containing at minimum `event_type`, `request_id`, `timestamp_utc`, and a `reason` field set to either `missing_header` or `key_mismatch`.

---

### Requirement 3: In-Memory Rate Limiting

**User Story:** As a platform operator, I want per-key rate limiting enforced at the gateway, so that no single API key can overwhelm downstream services during the POC.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL maintain an in-memory store mapping each API key to the list of request timestamps recorded within the current 60-second sliding window.
2. WHEN a request is received with a valid API key that has passed authentication, THE Rate_Limiter SHALL count the number of timestamps in that key's stored list that fall within the preceding 60-second window.
3. WHEN the count of timestamps within the preceding 60-second window is fewer than 60, THE Rate_Limiter SHALL allow the request to proceed to the next middleware stage.
4. WHEN the count of timestamps within the preceding 60-second window reaches or exceeds 60, THE Rate_Limiter SHALL return HTTP 429 with an error body indicating rate limit exceeded, and SHALL include a `Retry-After: 60` response header, and SHALL NOT forward the request downstream.
5. AFTER each evaluation, THE Rate_Limiter SHALL remove from the stored list all timestamps that are older than 60 seconds relative to the current time, such that the stored list for any key contains only entries within the preceding 60-second window.
6. WHEN a request is rate limited, THE Rate_Limiter SHALL emit a `rate_limited` Audit_Event to stdout containing at minimum `event_type`, `request_id`, `timestamp_utc`, the key identifier (hashed or truncated for safety), and `outcome: "block"`.
7. THE Rate_Limiter SHALL NOT apply rate limiting to the `GET /health` endpoint.

---

### Requirement 4: Request Normalization into IMF

**User Story:** As a platform engineer, I want all incoming OpenAI-compatible payloads to be converted into the canonical IMF structure, so that all downstream layers operate on a consistent message format regardless of client SDK or version.

#### Acceptance Criteria

1. WHEN an authenticated, non-rate-limited request is received, THE Request_Normalizer SHALL generate a UUID v4 value and assign it to the `request_id` field of the IMF.
2. THE Request_Normalizer SHALL set the `trace_id` field of the IMF to the same value as `request_id`.
3. THE Request_Normalizer SHALL set the `timestamp_utc` field of the IMF to the current UTC time formatted as ISO-8601 with a UTC timezone indicator (e.g., `Z` or `+00:00`).
4. THE Request_Normalizer SHALL populate the `user.user_id` field of the IMF with the static value `poc-user`.
5. THE Request_Normalizer SHALL set the `user.department` field to `poc`, the `user.roles` field to `["developer"]`, and the `user.auth_method` field to `api_key`.
6. WHEN the OpenAI_Payload includes a `model` field, THE Request_Normalizer SHALL map its value to `request.model` in the IMF. IF the `model` field is absent, THEN THE Request_Normalizer SHALL set `request.model` to `null`.
7. WHEN the OpenAI_Payload `messages` field is a non-empty array, THE Request_Normalizer SHALL map each message object to `request.messages` in the IMF, preserving order and field values. IF the `messages` field is absent or is not a non-empty array, THEN THE Request_Normalizer SHALL reject the request with HTTP 400.
8. WHEN the OpenAI_Payload includes a `stream` field, THE Request_Normalizer SHALL map its boolean value to `request.stream` in the IMF. IF `stream` is absent, THEN THE Request_Normalizer SHALL set `request.stream` to `false`.
9. WHEN the OpenAI_Payload includes a `max_tokens` field, THE Request_Normalizer SHALL map its integer value to `request.max_tokens` in the IMF. IF `max_tokens` is absent, THEN THE Request_Normalizer SHALL set `request.max_tokens` to `2048`.
10. WHEN the OpenAI_Payload includes a `temperature` field, THE Request_Normalizer SHALL map its numeric value to `request.temperature` in the IMF. IF `temperature` is absent, THEN THE Request_Normalizer SHALL set `request.temperature` to `0.7`.
11. THE Request_Normalizer SHALL initialize the `governance`, `routing`, `cache`, and `response` blocks of the IMF to their schema-defined default values as specified in the platform master contract.
12. WHEN the IMF is fully constructed, THE Request_Normalizer SHALL emit a `request_received` Audit_Event to stdout.
13. WHEN the incoming request body cannot be parsed as valid JSON, THE Request_Normalizer SHALL return HTTP 400 with body `{"error": {"code": "400", "message": "Bad request"}}` and SHALL NOT produce or forward an IMF object.

---

### Requirement 5: Downstream Forwarding to Security Layer

**User Story:** As a platform engineer, I want the normalized IMF to be forwarded to the Security & Governance Layer, so that all governance checks are applied before inference is invoked.

#### Acceptance Criteria

1. WHEN a valid IMF has been constructed, THE API_Gateway SHALL send an HTTP POST request to the URL `{DOWNSTREAM_SECURITY_URL}/process` with the serialized IMF JSON as the request body, `Content-Type: application/json` header, and a timeout of 10 seconds.
2. WHEN the Security_Layer returns HTTP 200, THE API_Gateway SHALL pass the response body to the Response_Serializer for client delivery.
3. IF the Security_Layer returns a non-200 HTTP status code, THEN THE API_Gateway SHALL return HTTP 502 with body `{"error": {"code": "502", "message": "Bad gateway"}}` to the client.
4. IF a network or connection error occurs while contacting the Security_Layer (including connection refused, DNS failure, or timeout), THEN THE API_Gateway SHALL return HTTP 502 with body `{"error": {"code": "502", "message": "Bad gateway"}}` to the client.
5. IF the Security_Layer returns HTTP 200 but with an empty or non-JSON response body, THEN THE API_Gateway SHALL return HTTP 502 with body `{"error": {"code": "502", "message": "Bad gateway"}}` to the client.

---

### Requirement 6: Response Serialization to OpenAI-Compatible Format

**User Story:** As an enterprise application developer, I want responses from the platform to conform to the OpenAI Chat Completions response schema, so that my existing OpenAI client code can parse them without modification.

#### Acceptance Criteria

1. WHEN the Security_Layer returns a successful IMF response with `request.stream` equal to `false`, THE Response_Serializer SHALL construct an OpenAI-compatible JSON response containing `id` (prefixed `chatcmpl-` followed by the `request_id`), `object` (value `chat.completion`), `created` (current Unix epoch integer), `model`, `choices`, and `usage` fields.
2. THE Response_Serializer SHALL populate `choices[0].message.role` with `assistant`, `choices[0].message.content` from the `response.content` field of the IMF, and `choices[0].index` with `0`.
3. THE Response_Serializer SHALL populate `choices[0].finish_reason` from the `response.finish_reason` field of the IMF.
4. THE Response_Serializer SHALL populate the `usage` block with `prompt_tokens`, `completion_tokens`, and `total_tokens` from the corresponding fields in `response.usage` of the IMF.
5. WHEN the IMF response has `request.stream` equal to `true`, THE Response_Serializer SHALL proxy the SSE byte stream from the downstream response to the client via FastAPI `StreamingResponse` with `Content-Type: text/event-stream`, without accumulating the full response body before sending the first chunk. IF the downstream stream terminates with an error status before completion, THE Response_Serializer SHALL close the client connection immediately.
6. WHEN the complete response body has been written to the client connection without error, THE API_Gateway SHALL emit a `response_sent` Audit_Event to stdout.

---

### Requirement 7: Streaming Support

**User Story:** As an enterprise application developer, I want to receive streamed responses via Server-Sent Events when I set `stream: true`, so that I can display incremental model output in my application without waiting for the full response.

#### Acceptance Criteria

1. WHEN a request contains `stream: true`, THE API_Gateway SHALL set `request.stream` to `true` in the IMF and forward it to the Security_Layer.
2. WHEN the Security_Layer responds with a streaming SSE body, THE API_Gateway SHALL proxy each SSE chunk to the client using FastAPI `StreamingResponse`, formatting each chunk as `data: <payload>\n\n` and terminating the stream with `data: [DONE]\n\n`, without accumulating the full response body before sending the first chunk.
3. WHEN the downstream returns an HTTP error status code before the stream completes, OR when the downstream connection drops before the stream completes, THE API_Gateway SHALL close the client SSE connection and emit a `response_sent` Audit_Event to stdout with `outcome: "error"`.
4. WHEN a request contains `stream: false` or omits the `stream` field, THE API_Gateway SHALL set `request.stream` to `false` in the IMF and return a single complete JSON response body.
5. WHEN the full SSE stream completes successfully, THE API_Gateway SHALL emit a `response_sent` Audit_Event to stdout with `outcome: "pass"`.

---

### Requirement 8: Structured JSON Logging

**User Story:** As a platform operator, I want all gateway activity logged as structured JSON to stdout, so that I can ingest logs into any centralized log aggregation system without custom parsing.

#### Acceptance Criteria

1. THE API_Gateway SHALL emit one structured JSON log record per request on a single line to stdout, containing the fields: `request_id`, `timestamp` (ISO-8601 UTC), `method`, `path`, `status_code`, and `latency_ms`.
2. THE API_Gateway SHALL write all log records to stdout.
3. THE API_Gateway SHALL respect the `LOG_LEVEL` environment variable, emitting records at or above the configured level (accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`).
4. THE API_Gateway SHALL default `LOG_LEVEL` to `INFO` when the environment variable is not set.
5. WHEN an unhandled exception occurs during request processing, THE API_Gateway SHALL log a JSON record with level `ERROR` to stdout containing `request_id`, `exception_type`, `exception_message`, `traceback`, and `latency_ms` measured up to the point of failure.

---

### Requirement 9: Audit Event Emission

**User Story:** As a compliance engineer, I want gateway lifecycle events written as structured JSON to stdout, so that the POC audit trail can be captured by any log collector without a dedicated audit store dependency.

#### Acceptance Criteria

1. WHEN the IMF is fully constructed for a request, THE API_Gateway SHALL emit an Audit_Event to stdout with `event_type: request_received` containing `audit_id`, `request_id`, `timestamp_utc`, `user_id`, `method`, `path`, `layer: "api_gateway"`, and `outcome: "pass"`.
2. WHEN authentication succeeds, THE API_Gateway SHALL emit an Audit_Event to stdout with `event_type: auth_pass` containing `audit_id`, `request_id`, `user_id`, `timestamp_utc`, `layer: "api_gateway"`, and `outcome: "pass"`.
3. WHEN authentication fails, THE API_Gateway SHALL emit an Audit_Event to stdout with `event_type: auth_fail` containing `audit_id`, `request_id`, `timestamp_utc`, `layer: "api_gateway"`, `outcome: "block"`, and a `reason` field set to `missing_header` or `key_mismatch`; `user_id` SHALL be omitted when the identity cannot be established.
4. WHEN a request is rate limited, THE API_Gateway SHALL emit an Audit_Event to stdout with `event_type: rate_limited` containing `audit_id`, `request_id`, `user_id`, `timestamp_utc`, `layer: "api_gateway"`, and `outcome: "block"`.
5. WHEN a response is delivered to the client, THE API_Gateway SHALL emit an Audit_Event to stdout with `event_type: response_sent` containing `audit_id`, `request_id`, `timestamp_utc`, `status_code`, `latency_ms` (measured from request receipt to last byte of response delivered), `layer: "api_gateway"`, and `outcome`.
6. THE API_Gateway SHALL write all Audit_Events to stdout as JSON on a single line per event.
7. EVERY Audit_Event emitted by THE API_Gateway SHALL include an `audit_id` (UUID v4 unique per event), a `layer` field set to `api_gateway`, and an `outcome` field set to `pass`, `block`, or `error`.

---

### Requirement 10: Prometheus Metrics Exposure

**User Story:** As a platform operator, I want basic Prometheus metrics exposed at `/metrics`, so that request counts and error rates can be scraped and monitored during the POC.

#### Acceptance Criteria

1. THE API_Gateway SHALL expose a `GET /metrics` endpoint that returns HTTP 200 with `Content-Type: text/plain; version=0.0.4; charset=utf-8` and a body in Prometheus text exposition format.
2. THE API_Gateway SHALL maintain a counter `llm_api_gateway_requests_total` labeled by `status_code` and `path` (using the route template, not the raw URL), incremented on every completed request excluding the `/metrics` and `/health` paths.
3. THE API_Gateway SHALL maintain a counter `llm_api_gateway_errors_total` labeled by `error_code` (set to the numeric HTTP status code string, e.g., `"401"`), incremented on every 4xx and 5xx response excluding the `/metrics` and `/health` paths.
4. THE API_Gateway SHALL maintain a histogram `llm_api_gateway_latency_seconds` labeled by `path` (route template), recording the end-to-end request latency from receipt of the first byte to delivery of the last byte of the response, using the default `prometheus_client` histogram buckets for POC.

---

### Requirement 11: IMF Schema Compliance

**User Story:** As a platform engineer, I want the IMF produced by the API Gateway to fully conform to the platform IMF schema, so that downstream layers can deserialize and process it without schema errors.

#### Acceptance Criteria

1. THE Request_Normalizer SHALL produce an IMF object that includes all top-level fields defined in the platform IMF schema — `request_id`, `trace_id`, `span_id`, `timestamp_utc`, `user`, `request`, `governance`, `routing`, `cache`, `response`, `metadata`, and `extensions` — where `request_id` is a UUID-v4 string, `timestamp_utc` is an ISO-8601 string with a UTC timezone indicator (`Z` or `+00:00`), and all structured blocks contain every sub-field defined in the platform IMF master contract with its specified default value.
2. THE Request_Normalizer SHALL set `span_id` to an empty string for the POC, as OpenTelemetry tracing is deferred.
3. THE Request_Normalizer SHALL set `trace_id` to the same UUID-v4 value as `request_id` for the POC, as distributed trace propagation is deferred.
4. THE Request_Normalizer SHALL set `metadata` and `extensions` to empty objects `{}`.
5. WHEN a valid OpenAI-compatible payload is received, THE Request_Normalizer SHALL populate `request.messages` with the same sequence of message objects as the input payload, preserving insertion order and the `role` and `content` field values of each message exactly, with no additions, removals, or reordering.
6. WHEN the IMF object produced by THE Request_Normalizer is serialized to JSON and the resulting JSON is deserialized into a fresh IMF Pydantic model instance, THE resulting model instance SHALL have every field value equal to the corresponding field value in the original IMF object, with no data loss or type coercion.
7. IF the incoming OpenAI-compatible payload is missing the `messages` field or contains a `messages` value that is not a non-empty list, THEN THE Request_Normalizer SHALL reject the request and return HTTP 400, without producing or forwarding an IMF object.

---

### Requirement 12: Helm Chart Deployment

**User Story:** As a platform operator, I want the API Gateway packaged as a Helm chart following platform conventions, so that I can deploy and configure it consistently with all other platform layers.

#### Acceptance Criteria

1. THE Helm_Chart SHALL be located at `llm-platform/charts/api-gateway/` and SHALL include `Chart.yaml`, `values.yaml`, and a `templates/` directory containing `deployment.yaml`, `service.yaml`, `ingress.yaml`, `networkpolicy.yaml`, `servicemonitor.yaml`, and `_helpers.tpl`.
2. THE Helm_Chart SHALL default `replicaCount` to `1` for the POC.
3. THE Helm_Chart SHALL configure the Kubernetes Service as type `ClusterIP` on port `8080`.
4. THE Helm_Chart SHALL configure a Kubernetes Ingress resource with `ingressClassName: nginx`, host `llm-poc.local`, and paths `/v1` and `/health`.
5. THE Helm_Chart SHALL accept `GATEWAY_API_KEY`, `DOWNSTREAM_SECURITY_URL` (must be a valid HTTP URL), and `LOG_LEVEL` (valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`) as configurable environment variables injected into the pod via the values file.
6. THE Helm_Chart SHALL set pod resource requests to `cpu: 100m` and `memory: 256Mi`, and limits to `cpu: 500m` and `memory: 512Mi`.
7. THE Helm_Chart SHALL set `autoscaling.enabled` to `false` for the POC.
8. THE Helm_Chart SHALL set `vault.enabled` to `false` for the POC; secrets are supplied via environment variables.
9. THE Helm_Chart SHALL configure the container image repository as `registry.local/api-gateway` with an empty default tag; the chart's `README.md` SHALL document that the image tag must be explicitly overridden at deploy time and that deploying with an empty tag is invalid.
10. WHERE `GATEWAY_API_KEY` is not overridden at deploy time, THE Helm_Chart SHALL use the placeholder value `poc-secret-key`; the chart's `README.md` SHALL document that this value must be replaced before any deployment outside a local development environment.
