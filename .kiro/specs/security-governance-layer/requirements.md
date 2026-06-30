# Requirements Document

## Introduction

The Security & Governance Layer (Layer 2) is a standalone FastAPI microservice that sits between the API Gateway (Layer 1) and the Intelligent Router (Layer 3) in the Enterprise On-Premises LLM Platform. It runs on port 8081 and every platform request passes through it twice: once before inference (pre-generation) and once after inference (post-generation).

In the pre-generation pass, the service scans the inbound prompt for injection patterns and unsafe content, masks PII, performs a role-based policy check, writes a pre-audit event to the Audit Store, and then forwards the enriched IMF to the Router. In the post-generation pass, it receives the Router's response, masks any PII that leaked into the model output, writes a post-audit event to the Audit Store, and returns the enriched IMF to the caller.

This is a POC implementation. All production-deferred features (OPA, LlamaGuard, ML classifiers, mTLS, HashiCorp Vault, human approval workflow, autoscaling) are explicitly out of scope. The POC demonstrates that governance controls are applied to every request before and after inference using lightweight rule-based alternatives.

---

## Glossary

- **Security_Layer**: The FastAPI microservice implementing Layer 2 of the platform; runs on port 8081.
- **IMF**: Internal Message Format — the canonical JSON envelope defined in the platform master contract that all inter-layer messages must use.
- **Injection_Detector**: The component that scans prompt content against `injection_patterns.yaml` using case-insensitive regex/keyword matching.
- **Content_Safety_Filter**: The component that checks prompt content against a keyword blocklist for unsafe material.
- **PII_Detector**: The Microsoft Presidio `AnalyzerEngine` instance configured to detect `EMAIL_ADDRESS`, `PHONE_NUMBER`, and `PERSON` entity types.
- **PII_Masker**: The Microsoft Presidio `AnonymizerEngine` instance that replaces detected PII entities with `[REDACTED_<ENTITY_TYPE>]` tokens.
- **Policy_Checker**: The component that verifies `user.roles` contains at least one of the allowed roles (`developer`, `analyst`, `admin`) using a static role dict lookup.
- **Audit_Logger**: The component that fires HTTP POST requests to the Audit Store at `AUDIT_STORE_URL` carrying audit event payloads; calls are fire-and-forget with a 2-second HTTP timeout.
- **Audit_Store**: The already-built append-only audit trail service running on port 9200.
- **Router**: The downstream Intelligent Router service running at `DOWNSTREAM_ROUTER_URL` (port 8082) that the Security_Layer forwards enriched IMF to.
- **Injection_Patterns_File**: The YAML file at `INJECTION_PATTERNS_PATH` containing the list of regex/keyword patterns for prompt injection detection.
- **Pre_Audit_Event**: An audit record written to the Audit_Store after the pre-generation security checks complete (outcome `pass` or `block`).
- **Post_Audit_Event**: An audit record written to the Audit_Store after post-generation PII masking completes.
- **Metrics_Endpoint**: The `/metrics` endpoint on port 9090 that exposes Prometheus metrics.
- **Allowed_Roles**: The static set of role strings that satisfy the policy check: `developer`, `analyst`, `admin`.

---

## Requirements

### Requirement 1: Pre-Generation Security Check Endpoint

**User Story:** As the API Gateway, I want to POST an IMF to the Security Layer so that every prompt is scanned for injection, unsafe content, and PII before reaching the inference model.

#### Acceptance Criteria

1. WHEN a POST request is received at `/security/check` with a JSON body containing a valid UUID-v4 `request_id` and a non-empty `request.messages` array, THE Security_Layer SHALL execute the full pre-generation pipeline (injection scan → content safety → PII detection/masking → policy check → pre-audit → forward to Router) and return the Router's response to the caller.
2. WHEN the pre-generation pipeline completes successfully, THE Security_Layer SHALL return HTTP 200 with the Router's IMF response body, with the following `governance` fields updated by the Security_Layer: `injection_score`, `content_safety_passed`, `pii_masked`, `pii_fields_detected`, `policy_decisions`, `human_approval_required`, and `human_approval_status`.
3. IF the request body is not parseable as valid JSON, THEN THE Security_Layer SHALL return HTTP 400 with a structured error body; no downstream calls SHALL be made.
4. IF the `request_id` field is absent or not a valid UUID-v4 in the inbound IMF, THEN THE Security_Layer SHALL return HTTP 422 with a structured error body identifying the failing field.
5. IF the `request.messages` field is absent or not a non-empty JSON array in the inbound IMF, THEN THE Security_Layer SHALL return HTTP 422 with a structured error body identifying the failing field.
6. WHEN a request is blocked at any pipeline stage, THE Security_Layer SHALL return the appropriate HTTP 400 or HTTP 403 error response with a structured error body identifying the blocking stage and reason, and SHALL NOT forward the request to the Router.
7. IF the Router is unreachable or returns no valid response within 5000 ms, THEN THE Security_Layer SHALL return HTTP 502 or HTTP 504 respectively, and the Pre_Audit_Event written before the forwarding attempt SHALL be retained.
8. THE Security_Layer SHALL complete the full pre-generation pipeline — excluding downstream Router latency — within 2000 ms at the p95 percentile under a load of at most 50 concurrent requests with no dependency failures.

---

### Requirement 2: Post-Generation Security Check Endpoint

**User Story:** As the API Gateway, I want to POST a Router IMF response to the Security Layer so that any PII leaked into the model's output is masked before the response reaches the consumer.

#### Acceptance Criteria

1. WHEN a POST request is received at `/security/post-check` with a valid IMF JSON body containing a non-null `response.content` field, THE Security_Layer SHALL run PII masking on `response.content` and return the enriched IMF with the masked `response.content`.
2. WHEN PII masking is applied to `response.content`, THE Security_Layer SHALL update `governance.pii_masked` to `true` and populate `governance.pii_fields_detected` with the distinct list of detected entity types; the `pii_actions` field in the subsequent Post_Audit_Event SHALL list the masked entity types.
3. WHEN a POST request is received at `/security/post-check` with a valid IMF JSON body containing a null or absent `response.content` field, THE Security_Layer SHALL skip PII masking and return the IMF unchanged; the Post_Audit_Event SHALL include `pii_actions: []`.
4. WHEN the post-generation pipeline completes, THE Security_Layer SHALL write a Post_Audit_Event to the Audit_Store containing `request_id`, `layer: "security"`, `event_type: "response_sent"`, `outcome: "pass"`, and `pii_actions`.
5. IF the Presidio `AnonymizerEngine` is unavailable during post-generation PII masking, THEN THE Security_Layer SHALL return HTTP 200 with the unmasked IMF, set `governance.pii_masked` to `false`, and write a Post_Audit_Event with `outcome: "pass"` and a flag indicating masking was skipped.
6. IF the request body is not parseable as valid JSON, THEN THE Security_Layer SHALL return HTTP 400 with a structured error body.
7. IF the `request_id` field is absent or not a valid UUID-v4 in the inbound IMF, THEN THE Security_Layer SHALL return HTTP 422 with a structured error body identifying `request_id` as the failing field.
8. THE Security_Layer SHALL complete the post-generation pipeline — excluding async Audit_Store fire-and-forget latency — within 1000 ms at the p95 percentile under normal operating conditions.

---

### Requirement 3: Prompt Injection Detection

**User Story:** As a security operator, I want every inbound prompt scanned against a configurable injection pattern list so that prompt injection attacks are blocked before they reach the model.

#### Acceptance Criteria

1. WHEN the Injection_Detector scans `request.messages` and finds a case-insensitive match against any pattern in the Injection_Patterns_File, THE Security_Layer SHALL set `governance.injection_score` to `1.0` in the IMF.
2. WHEN the Injection_Detector sets `governance.injection_score` to `1.0`, THE Security_Layer SHALL return HTTP 400 with a structured error body containing the `error` key set to `"injection_detected"` and the `request_id` value.
3. WHEN the Injection_Detector scans `request.messages` and finds no match against any pattern, THE Security_Layer SHALL set `governance.injection_score` to `0.0` in the IMF and continue to the content safety stage.
4. THE Injection_Detector SHALL concatenate the `content` fields of all messages in `request.messages` using a single space as separator, then apply each pattern as a case-insensitive match against the full concatenated string.
5. WHEN the Security_Layer starts and `INJECTION_PATTERNS_PATH` resolves to a readable, parseable YAML file containing a valid `patterns` list, THE Injection_Detector SHALL load all patterns into memory before accepting any requests.
6. IF the `INJECTION_PATTERNS_PATH` environment variable is not set, the file does not exist, the file cannot be read, the YAML is malformed, or any entry is an invalid regex expression at startup, THEN THE Security_Layer SHALL log an ERROR message identifying the specific failure and refuse to start, exiting with a non-zero exit code.
7. IF the Injection_Patterns_File is readable and parseable but contains an empty `patterns` list, THEN THE Security_Layer SHALL log a WARNING at startup and treat every request as a no-match (injection score `0.0`) without refusing to start.
8. THE Injection_Detector SHALL treat plain keyword string entries as literal case-insensitive substring patterns, and SHALL treat entries containing regex metacharacters as compiled regular expression patterns applied via `re.search`.

---

### Requirement 4: Content Safety Filter

**User Story:** As a security operator, I want every inbound prompt checked against a keyword blocklist so that requests containing clearly unsafe content are rejected before reaching the model.

#### Acceptance Criteria

1. WHEN the Content_Safety_Filter checks `request.messages` and finds a case-insensitive substring match against any entry in the configured blocked-word list, THE Security_Layer SHALL set `governance.content_safety_passed` to `false` in the IMF and return HTTP 400 with a structured error body containing the `error` key set to `"content_safety_violation"` and the `request_id` value.
2. WHEN the Content_Safety_Filter checks `request.messages` and finds no blocked-word match, THE Security_Layer SHALL set `governance.content_safety_passed` to `true` in the IMF and continue to the PII detection stage.
3. THE Content_Safety_Filter SHALL apply each blocked-word check as a case-insensitive substring match across the concatenated `content` fields of all messages in `request.messages`.
4. IF a request was blocked by the Injection_Detector, THEN THE Content_Safety_Filter SHALL NOT execute for that request.
5. IF the blocked-word list is empty or unavailable at check time, THEN THE Content_Safety_Filter SHALL set `governance.content_safety_passed` to `true` and continue to the PII detection stage, logging a WARNING to stdout identifying the condition.

---

### Requirement 5: PII Detection and Masking

**User Story:** As a compliance officer, I want all personally identifiable information in prompts and responses automatically masked so that PII is never transmitted to the inference model or returned to consumers in plain text.

#### Acceptance Criteria

1. WHEN the PII_Detector processes `request.messages` and identifies one or more entities of type `EMAIL_ADDRESS`, `PHONE_NUMBER`, or `PERSON` with a confidence score of at least 0.7, THE PII_Masker SHALL replace each detected entity in the `content` field with the token `[REDACTED_<ENTITY_TYPE>]` (e.g., `[REDACTED_EMAIL_ADDRESS]`).
2. WHEN PII masking is applied to `request.messages`, THE Security_Layer SHALL update `governance.pii_masked` to `true` and populate `governance.pii_fields_detected` with the deduplicated list of detected entity types (e.g., `["EMAIL_ADDRESS", "PERSON"]`).
3. WHEN no PII entities meeting the confidence threshold are detected in `request.messages`, THE Security_Layer SHALL set `governance.pii_masked` to `false` and `governance.pii_fields_detected` to an empty array `[]`.
4. WHEN the PII_Masker processes `response.content` in the post-generation pipeline and identifies PII entities with confidence ≥ 0.7, THE PII_Masker SHALL replace each detected entity with the token `[REDACTED_<ENTITY_TYPE>]`, update `response.content` with the masked string, and return the deduplicated list of detected entity types.
5. WHERE `PII_ENABLED` is set to `"false"`, THE Security_Layer SHALL skip all PII detection and masking steps in both the pre-generation and post-generation pipelines, set `governance.pii_masked` to `false`, and set `governance.pii_fields_detected` to `[]`.
6. THE PII_Detector SHALL use the Microsoft Presidio `AnalyzerEngine` running on CPU with default English recognizers restricted to the entity types `EMAIL_ADDRESS`, `PHONE_NUMBER`, and `PERSON`.
7. THE PII_Detector SHALL apply a minimum confidence score threshold of 0.7; entities detected below this threshold SHALL NOT be masked and SHALL NOT appear in `governance.pii_fields_detected`.
8. IF the `PII_Detector` or `PII_Masker` raises an unhandled exception during processing, THEN THE Security_Layer SHALL log an ERROR identifying the `request_id` and the exception, and SHALL return HTTP 500 with a structured error body rather than proceeding with an unmasked prompt.

---

### Requirement 6: Role-Based Policy Check

**User Story:** As a security operator, I want every request validated against a role allowlist so that only users with an authorized role can reach the inference model.

#### Acceptance Criteria

1. WHEN the Policy_Checker evaluates `user.roles` and finds at least one value matching an entry in the Allowed_Roles set (`developer`, `analyst`, `admin`), THE Security_Layer SHALL append a role check pass decision to `governance.policy_decisions` and continue processing to the next pipeline stage.
2. WHEN the Policy_Checker evaluates `user.roles` and finds no value matching any entry in the Allowed_Roles set, THE Security_Layer SHALL append a role check deny decision to `governance.policy_decisions` and return HTTP 403 with a structured error body containing an `error` field set to `"policy_denied"`, a `reason` field set to `"insufficient_role"`, and the `request_id` value.
3. IF the `user.roles` field is absent or null in the inbound IMF, THEN THE Policy_Checker SHALL treat it as an empty roles list and deny the request with HTTP 403 per criterion 2.
4. IF the `user.roles` field is present in the inbound IMF but is not a valid list type, THEN THE Policy_Checker SHALL return HTTP 400 with a structured error body indicating that the roles field is malformed, without appending any decision to `governance.policy_decisions`.
5. THE Policy_Checker SHALL execute only after the PII masking stage has completed; a request blocked by injection detection or content safety SHALL NOT reach the policy check stage.
6. THE Security_Layer SHALL set `governance.human_approval_required` to `false` and `governance.human_approval_status` to `"not_required"` for all requests processed during the POC phase.

---

### Requirement 7: Audit Logging — Pre-Generation Event

**User Story:** As a compliance officer, I want every pre-generation security decision recorded in the Audit Store so that I can trace exactly which checks were applied and what outcome was reached for any request.

#### Acceptance Criteria

1. WHEN the pre-generation pipeline completes (regardless of outcome `pass` or `block`), THE Audit_Logger SHALL fire an HTTP POST to `AUDIT_STORE_URL/audit/events` with a Pre_Audit_Event payload containing at minimum: `request_id`, `user_id` (from `user.user_id`), `layer: "security"`, `timestamp_utc` (current UTC ISO-8601), `latency_ms` (elapsed pipeline time), `event_type: "security_block"` if blocked or `"request_received"` if passed, `outcome: "block"` if blocked or `"pass"` if passed, `pii_actions` (list of masked entity types), and `policy_decisions`.
2. THE Audit_Logger SHALL include the `X-API-Key` header with the value from the `AUDIT_API_KEY` environment variable in every POST to the Audit_Store.
3. THE Audit_Logger SHALL use a 2-second HTTP timeout on every POST to the Audit_Store; if the request does not complete within 2 seconds, the Audit_Logger SHALL cancel the request, log a WARNING to stdout containing `request_id` and the word `"timeout"`, and continue without retrying.
4. THE Audit_Logger SHALL dispatch the POST asynchronously using a background task so that Audit_Store network latency does not block the Security_Layer from returning its response to the caller; the response to the caller SHALL be sent before the Audit_Store POST completes.
5. WHEN the Audit_Store returns a non-2xx HTTP response to the Audit_Logger's POST, THE Audit_Logger SHALL log a WARNING to stdout containing `request_id` and the received HTTP status code, and SHALL continue normal processing without retrying.

---

### Requirement 8: Audit Logging — Post-Generation Event

**User Story:** As a compliance officer, I want every post-generation PII masking action recorded in the Audit Store so that the full request-response governance trail is complete.

#### Acceptance Criteria

1. WHEN the post-generation pipeline completes, THE Audit_Logger SHALL fire an HTTP POST to `AUDIT_STORE_URL/audit/events` with a Post_Audit_Event payload containing at minimum: `request_id`, `user_id` (from `user.user_id` if present, or `null` if absent), `layer: "security"`, `event_type: "response_sent"`, `outcome: "pass"`, `timestamp_utc` (current UTC ISO-8601), and `pii_actions` (list of entity types masked in `response.content`, or `[]` if none).
2. THE Audit_Logger SHALL dispatch the Post_Audit_Event POST asynchronously using a background task so that Audit_Store latency does not block the Security_Layer from returning its response; the response to the caller SHALL be sent before the Audit_Store POST completes.
3. IF the HTTP POST to the Audit_Store times out (exceeds 2 seconds), is refused, or returns a non-2xx status code, THEN THE Audit_Logger SHALL log a WARNING to stdout containing `request_id` and the failure reason, and SHALL NOT retry or propagate the error to the caller.
4. IF `AUDIT_STORE_URL` is not set or is an empty string at startup, THEN THE Security_Layer SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code, so that the absence of the audit store URL is detected at deployment time rather than at runtime.

---

### Requirement 9: Downstream Router Forwarding

**User Story:** As the API Gateway, I want the Security Layer to forward governance-enriched IMF to the Router and relay the Router's response back so that the caller does not need to know about the Router's address.

#### Acceptance Criteria

1. WHEN all pre-generation checks pass, THE Security_Layer SHALL forward the enriched IMF (with updated `governance` fields and masked `request.messages`) via HTTP POST to `DOWNSTREAM_ROUTER_URL/router/route` and relay the Router's response body and HTTP status code back to the caller.
2. IF the Router returns a non-2xx HTTP status code, THE Security_Layer SHALL relay that status code and response body to the caller unchanged and log the failure at WARNING level including `request_id` and the received status code.
3. IF the HTTP connection to the Router times out at either the connect or read/response phase (exceeds 30 seconds total), THEN THE Security_Layer SHALL return HTTP 504 with a structured error body containing an `error` field set to `"router_timeout"` and the `request_id` value.
4. IF the HTTP connection to the Router is refused or the host is unreachable, THEN THE Security_Layer SHALL return HTTP 502 with a structured error body containing an `error` field set to `"router_unavailable"` and the `request_id` value.
5. WHEN forwarding the enriched IMF to the Router, THE Security_Layer SHALL include a `X-Request-Id` header with the value of `request_id` to preserve distributed trace correlation.
6. IF the Router returns a 2xx HTTP status code but the response body is empty or not parseable as valid JSON, THEN THE Security_Layer SHALL return HTTP 502 with a structured error body containing an `error` field set to `"router_invalid_response"` and the `request_id` value.

---

### Requirement 10: Health Check Endpoint

**User Story:** As a Kubernetes liveness probe, I want a lightweight health endpoint so that the orchestrator can detect when the Security Layer is unavailable.

#### Acceptance Criteria

1. WHEN `GET /health` is called and the Security_Layer is operational (Presidio engine loaded and injection patterns loaded with at least one pattern), THE Security_Layer SHALL return HTTP 200 with `Content-Type: application/json` and a JSON body `{"status": "ok", "pii_enabled": <bool>, "patterns_loaded": <int>}`.
2. IF the Presidio `AnalyzerEngine` failed to initialize or the injection pattern list is empty (zero patterns loaded), THEN `GET /health` SHALL return HTTP 503 with `Content-Type: application/json` and a JSON body `{"status": "degraded", "reason": "<presidio_unavailable|no_patterns_loaded>"}`.
3. THE Security_Layer SHALL complete the `GET /health` response within 200 ms, measured from receipt of the request.
4. THE `GET /health` endpoint SHALL NOT require an `X-API-Key` header or any other authentication credential.

---

### Requirement 11: Prometheus Metrics

**User Story:** As a platform SRE, I want the Security Layer to expose Prometheus metrics so that I can monitor throughput, latency, and block rates from the central observability stack.

#### Acceptance Criteria

1. THE Security_Layer SHALL expose a `/metrics` endpoint on port 9090 that returns a Prometheus text exposition format 0.0.4 response with `Content-Type: text/plain; version=0.0.4; charset=utf-8`.
2. WHEN a pre-generation pipeline request terminates (pass, block, or error), THE Security_Layer SHALL increment the `llm_security_requests_total` counter once, labelled by `outcome` (`pass`, `block`, `error`) and `check` (the name of the stage that terminated the request: `injection`, `content_safety`, `policy`, or `full_pipeline` when all checks passed).
3. WHEN a request handler returns, THE Security_Layer SHALL observe the elapsed time from handler entry to response return in the `llm_security_latency_seconds` histogram, labelled by `endpoint` (`pre_check`, `post_check`), using buckets at: 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0 seconds.
4. WHEN PII entities are detected in a request or response, THE Security_Layer SHALL increment the `llm_security_pii_entities_total` counter by the count of each detected entity type, labelled by `entity_type` (`EMAIL_ADDRESS`, `PHONE_NUMBER`, `PERSON`, or `OTHER` for any entity type outside the enumerated set).
5. WHEN a request is blocked at any pipeline stage, THE Security_Layer SHALL increment both `llm_security_blocks_total` (labelled by `reason`: `injection_detected`, `content_safety_violation`, `policy_denied`) and `llm_security_requests_total{outcome="block"}` for the same request.

---

### Requirement 12: Structured JSON Logging

**User Story:** As a platform SRE, I want every significant Security Layer action logged as structured JSON to stdout so that log aggregation pipelines can index and query security decisions.

#### Acceptance Criteria

1. THE Security_Layer SHALL emit all log entries as single-line JSON objects to stdout; no log entry SHALL span multiple lines.
2. WHEN a pre-generation pipeline completes, THE Security_Layer SHALL emit an INFO-level log entry containing at minimum: `request_id`, `injection_detected` (bool), `pii_entities_found` (list — `[]` when none), `outcome` (`pass` or `block`), and `latency_ms`.
3. WHEN a request is blocked, THE Security_Layer SHALL emit an INFO-level log entry at the point of block containing: `request_id`, `block_reason` (one of `injection_detected`, `content_safety_violation`, `policy_denied`), and `latency_ms`.
4. WHEN a post-generation pipeline completes, THE Security_Layer SHALL emit an INFO-level log entry containing: `request_id`, `pii_entities_found` (list of entity types found in response, or `[]` if none), and `latency_ms`.
5. THE Security_Layer SHALL include a `timestamp` field in ISO-8601 UTC format and a `level` field (one of `DEBUG`, `INFO`, `WARNING`, `ERROR`) in every log entry.
6. THE Security_Layer SHALL respect the `LOG_LEVEL` environment variable to set the minimum log level; valid values are `DEBUG`, `INFO`, `WARNING`, `ERROR`; log entries below the configured level SHALL NOT be emitted.
7. IF `LOG_LEVEL` is not set or is set to an unrecognised value, THEN THE Security_Layer SHALL default to `INFO` level logging and SHALL NOT refuse to start.
8. THE Security_Layer SHALL always emit the security-decision log entries defined in criteria 2, 3, and 4 at INFO level regardless of the configured `LOG_LEVEL`, ensuring security pipeline outcomes are never suppressed by log level configuration.
9. WHEN an ERROR-level log entry is emitted, THE Security_Layer SHALL include at minimum the `request_id` (if available from the request context) and an `error` field containing the exception message or failure description.

---

### Requirement 13: Service Configuration via Environment Variables

**User Story:** As a DevOps engineer, I want the Security Layer configured entirely through environment variables so that no secrets or environment-specific values are hardcoded into the container image.

#### Acceptance Criteria

1. THE Security_Layer SHALL read the following environment variables on startup: `LOG_LEVEL`, `DOWNSTREAM_ROUTER_URL`, `AUDIT_STORE_URL`, `AUDIT_API_KEY`, `PII_ENABLED`, and `INJECTION_PATTERNS_PATH`.
2. IF `DOWNSTREAM_ROUTER_URL` is not set or is an empty string at startup, THEN THE Security_Layer SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code.
3. IF `AUDIT_STORE_URL` is not set or is an empty string at startup, THEN THE Security_Layer SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code.
4. IF `AUDIT_API_KEY` is not set or is an empty string at startup, THEN THE Security_Layer SHALL log an ERROR message and refuse to start, exiting with a non-zero exit code.
5. IF `INJECTION_PATTERNS_PATH` is not set, is an empty string, points to a non-existent file, or points to a file that cannot be read at startup, THEN THE Security_Layer SHALL log an ERROR message identifying the specific failure and refuse to start, exiting with a non-zero exit code.
6. WHERE `PII_ENABLED` is not set, THE Security_Layer SHALL default to `true` (PII detection and masking enabled).
7. IF `PII_ENABLED` is set to a value other than `"true"` or `"false"` (case-insensitive), THEN THE Security_Layer SHALL log an ERROR message identifying the invalid value and refuse to start, exiting with a non-zero exit code.
8. IF `LOG_LEVEL` is not set or is set to an unrecognised value, THE Security_Layer SHALL default to `INFO` level logging and SHALL continue to start normally.

---

### Requirement 14: Helm Chart — `llm-platform/charts/security-layer/`

**User Story:** As a platform DevOps engineer, I want a Helm chart for the Security Layer so that it can be deployed consistently to any Kubernetes cluster running the platform.

#### Acceptance Criteria

1. THE Security_Layer Helm chart SHALL include the following files: `Chart.yaml`, `values.yaml`, `templates/deployment.yaml`, `templates/service.yaml`, `templates/networkpolicy.yaml`, `templates/servicemonitor.yaml`, `templates/configmap.yaml`, `templates/hpa.yaml`, `templates/_helpers.tpl`, and `README.md`.
2. THE `Chart.yaml` SHALL declare `apiVersion: v2`, `name: security-layer`, `version: 0.1.0`, and `appVersion: "0.1.0"`.
3. THE `values.yaml` SHALL include the following default values: `replicaCount: 1`, `image.repository: registry.local/security-layer`, `image.tag: ""`, `image.pullPolicy: IfNotPresent`, `service.port: 8081`, `env.LOG_LEVEL: "INFO"`, `env.DOWNSTREAM_ROUTER_URL: "http://router:8082"`, `env.PII_ENABLED: "true"`, `env.INJECTION_PATTERNS_PATH: "/config/injection_patterns.yaml"`, `resources.requests.cpu: "200m"`, `resources.requests.memory: "512Mi"`, `resources.limits.cpu: "1"`, `resources.limits.memory: "1Gi"`, `observability.metrics.enabled: true`, `observability.metrics.port: 9090`, `observability.tracing.enabled: false`, `observability.tracing.endpoint: "http://otel-collector:4317"`, `autoscaling.enabled: false`, `autoscaling.minReplicas: 2`, `autoscaling.maxReplicas: 10`, `autoscaling.targetCPUUtilizationPercentage: 70`, `vault.enabled: false`, `vault.role: "security-layer-role"`, and `vault.secretPath: "secret/llm-platform/security-layer"`.
4. THE `templates/service.yaml` SHALL expose port 8081 (named `http`) for the application API as a ClusterIP Service.
5. THE `templates/service.yaml` SHALL expose port 9090 (named `metrics`) for the Prometheus metrics endpoint on the same ClusterIP Service.
6. THE `templates/networkpolicy.yaml` SHALL allow ingress to port 8081 from the `llm-api-gateway` namespace only; all other ingress to port 8081 SHALL be denied.
7. THE `templates/networkpolicy.yaml` SHALL allow ingress to port 9090 from the `llm-observability` namespace only; all other ingress to port 9090 SHALL be denied.
8. THE `templates/networkpolicy.yaml` SHALL allow egress from the Security_Layer pods to `DOWNSTREAM_ROUTER_URL` (port 8082), `AUDIT_STORE_URL` (port 9200), and the OTel collector (port 4317); all other egress SHALL be denied by default.
9. THE `templates/servicemonitor.yaml` SHALL configure Prometheus to scrape the `/metrics` endpoint on the named port `metrics` at a 30-second interval, using the selector labels defined in `templates/_helpers.tpl`.
10. THE `templates/deployment.yaml` SHALL mount the `injection_patterns.yaml` config file into the container at the path specified by `env.INJECTION_PATTERNS_PATH` via the ConfigMap defined in `templates/configmap.yaml`.
