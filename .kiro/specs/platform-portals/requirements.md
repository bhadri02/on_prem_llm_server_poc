# Requirements Document

## Introduction

This document defines requirements for Layer 10 — Platform Portals of the Enterprise On-Premises LLM Platform (POC scope). The Platform Portal is a single combined web application that merges the Admin and Developer Portal roles into one lightweight service. It provides a React-based single-page UI backed by a FastAPI proxy API, giving platform operators and developers three core capabilities: a Playground to send test chat requests, an Audit Viewer to inspect recent audit events, and a Model Viewer to manage registered model status. A Grafana dashboard is embedded for live observability metrics. The portal communicates with other platform layers (API Gateway, Audit Store, Model Registry, Prometheus) via plain HTTP using a static API key for POC.

---

## Glossary

- **Portal**: The combined Admin + Developer Portal web application (POC scope).
- **Portal_API**: The FastAPI backend service that proxies requests from the Portal UI to downstream platform layer APIs.
- **Portal_UI**: The React (Vite) single-page application served by the Portal.
- **Playground**: The interactive chat component within the Portal_UI for sending test requests to the API Gateway.
- **Audit_Viewer**: The Portal_UI component that displays audit event records from the Audit Store.
- **Model_Viewer**: The Portal_UI component that displays registered models and allows status changes.
- **API_Gateway**: The platform's Layer 1 service, reachable at `http://api-gateway:8080`.
- **Audit_Store**: The platform's audit persistence service, reachable at `http://audit-store:9200`.
- **Model_Registry**: The platform's model lifecycle service, reachable at `http://model-registry:5000`.
- **Grafana**: The platform's dashboard service, reachable at `http://grafana:3000`.
- **IMF**: Internal Message Format — the canonical JSON schema used for inter-layer communication.
- **Audit_Record**: A record conforming to the canonical audit schema defined in the Platform Master Contract.
- **Request_ID**: A UUID v4 that uniquely identifies a single chat request across all platform layers.
- **Model_Status**: The lifecycle state of a registered model: `active`, `retired`, or `staging`.

---

## Requirements

### Requirement 1: Portal API Health Check

**User Story:** As a platform operator, I want a health endpoint for the Portal API, so that Kubernetes liveness and readiness probes can verify the service is running.

#### Acceptance Criteria

1. THE Portal_API SHALL expose a `GET /portal/health` endpoint that returns HTTP 200 with a JSON body containing a `status` field set to `"ok"` when the service is healthy.
2. WHEN the `GET /portal/health` endpoint is called, THE Portal_API SHALL respond within 500ms under normal operating conditions.
3. IF the Portal_API is unable to serve requests (e.g., dependency initialization failure), THEN `GET /portal/health` SHALL return HTTP 503 with a JSON body containing a `status` field set to `"degraded"` and a `reason` field describing the failure.
4. THE `GET /portal/health` endpoint SHALL NOT require any authentication headers and SHALL be accessible without an API key.
5. THE Portal_API SHALL expose a `/metrics` endpoint on port 9090 that returns Prometheus text format (content-type `text/plain; version=0.0.4`) and SHALL NOT require authentication.

---

### Requirement 2: Playground — Chat Request Submission

**User Story:** As a developer, I want to send a chat message through the Playground and see the response, so that I can test model behavior directly from the portal.

#### Acceptance Criteria

1. WHEN the Playground view loads, THE Playground SHALL populate the model selector dropdown with the list of models returned by `GET /portal/models`; IF the fetch fails, THE Playground SHALL display an error message and disable the Send button.
2. THE Playground SHALL display a temperature input field that accepts float values between `0.0` and `2.0` inclusive, with a default value of `0.7`.
3. THE Playground SHALL display a message input field that accepts up to 4000 characters of text, and a Send button.
4. WHEN the Send button is activated, THE Portal_API SHALL forward a request to `POST http://api-gateway:8080/v1/chat/completions` with the `X-API-Key` header set to the value of the `GATEWAY_API_KEY` environment variable, and a JSON body containing `model` (selected model name), `temperature` (current temperature value), and `messages` (array with at least one `{"role": "user", "content": "<input text>"}` entry).
5. WHILE a request is in progress, THE Playground SHALL disable the Send button and display a loading indicator.
6. WHEN the API Gateway returns a successful response, THE Playground SHALL display the assistant's response text and the `request_id` value from the response body.
7. WHEN the API Gateway returns a successful response, THE Playground SHALL display a "View Audit Trail" button; WHEN that button is clicked, THE Portal_UI SHALL navigate to the Audit Viewer view and pre-populate its filter with the `request_id` from the response body.
8. IF the API Gateway returns an HTTP error response, THEN THE Playground SHALL display the HTTP status code and the error detail text from the response body to the user.
9. IF the `GATEWAY_API_KEY` environment variable is not set at startup, THEN THE Portal_API SHALL log an error message to stdout and exit with a non-zero status code.

---

### Requirement 3: Playground — Chat Proxy API

**User Story:** As a developer, I want the Portal API to proxy chat requests to the API Gateway, so that the browser does not need direct access to internal platform services.

#### Acceptance Criteria

1. THE Portal_API SHALL expose a `POST /portal/playground/chat` endpoint that accepts a JSON body containing at minimum `model` (string, required), `messages` (array of at least 1 object, required), and `temperature` (float between 0.0 and 2.0, required); IF any required field is missing or invalid, THE Portal_API SHALL return HTTP 422 with a JSON body describing the validation error.
2. WHEN a valid `POST /portal/playground/chat` request is received, THE Portal_API SHALL forward the body unchanged to `POST http://api-gateway:8080/v1/chat/completions` within a 30-second timeout, and return the upstream response to the caller.
3. WHEN the API Gateway returns any HTTP status code, THE Portal_API SHALL propagate that status code and body to the caller without modification.
4. IF the API Gateway is unreachable or the 30-second timeout is exceeded, THEN THE Portal_API SHALL return HTTP 502 with a JSON error body indicating upstream unavailability and identifying `"api-gateway"` as the upstream.
5. THE Portal_API SHALL set the `X-API-Key` header to the value of `GATEWAY_API_KEY` on every forwarded request to the API Gateway.
6. WHEN a `POST /portal/playground/chat` request completes (success or error), THE Portal_API SHALL emit a single-line JSON log entry to stdout containing `endpoint`, `status_code`, and `latency_ms` measured from request receipt to response sent.

---

### Requirement 4: Audit Viewer — Display Audit Events

**User Story:** As a platform operator, I want to view recent audit events in a table, so that I can monitor platform activity and investigate specific requests.

#### Acceptance Criteria

1. THE Audit_Viewer SHALL display audit events in a table with columns: `timestamp_utc`, `request_id`, `layer`, `event_type`, `user_id`, `outcome`, and `latency_ms`.
2. THE Audit_Viewer SHALL display filter controls for: time range (from/to datetime pickers), layer (dropdown), and outcome (dropdown with values `pass` and `block`).
3. WHEN the Audit_Viewer loads, THE Portal_API SHALL fetch the 50 most recent audit events (sorted descending by `timestamp_utc`, defaulting to the last 24 hours) from the Audit Store and return them to the UI.
4. WHEN a filter control value changes, THE Audit_Viewer SHALL re-fetch the filtered event list from `GET /portal/audit/events` with the updated query parameters; results SHALL be sorted descending by `timestamp_utc`.
5. WHEN a `request_id` cell is clicked, THE Audit_Viewer SHALL display an overlay panel within the same view showing all Audit_Records for that Request_ID, fetched from `GET /portal/audit/requests/{request_id}`.
6. IF the Audit_Store is unreachable, THEN THE Portal_API SHALL return HTTP 502 identifying `"audit-store"` as the unavailable upstream, and THE Audit_Viewer SHALL display an error message to the user.
7. WHEN `GET /portal/audit/events` or `GET /portal/audit/requests/{request_id}` returns an error, THE Audit_Viewer SHALL display a dismissible error banner containing the HTTP status code and error detail.
8. WHEN the audit event list is empty (no records match the current filters), THE Audit_Viewer SHALL display an empty-state message indicating no records were found.

---

### Requirement 5: Audit Viewer — Audit Proxy API

**User Story:** As a developer, I want the Portal API to expose audit query endpoints, so that the Portal UI can retrieve audit data without direct access to the Audit Store.

#### Acceptance Criteria

1. THE Portal_API SHALL expose `GET /portal/audit/events` accepting optional query parameters `from` (ISO-8601 datetime), `to` (ISO-8601 datetime), and `limit` (integer, minimum 1, default 50, maximum 200).
2. THE Portal_API SHALL expose `GET /portal/audit/requests/{request_id}` that returns all Audit_Records associated with the given Request_ID.
3. WHEN `GET /portal/audit/events` is called with a `limit` value below 1 or above 200, THE Portal_API SHALL return HTTP 400 with a JSON body describing the out-of-range error and the allowed bounds.
4. IF `{request_id}` in `GET /portal/audit/requests/{request_id}` does not match the UUID v4 format, THEN THE Portal_API SHALL return HTTP 400 with a JSON body indicating the parameter is invalid.
5. WHEN no audit events are found for a given Request_ID, THE Portal_API SHALL return HTTP 200 with an empty list body.
6. IF `from` or `to` are provided but do not conform to ISO-8601 format, OR if `from` is later than `to`, THEN THE Portal_API SHALL return HTTP 400 with a JSON body describing the validation error.
7. IF the Audit_Store is unreachable when processing any audit proxy request, THEN THE Portal_API SHALL return HTTP 502 with a JSON error body identifying `"audit-store"` as the unavailable upstream.

---

### Requirement 6: Model Viewer — Display Registered Models

**User Story:** As a platform operator, I want to view all registered models and their current status, so that I can understand which models are available for inference.

#### Acceptance Criteria

1. THE Model_Viewer SHALL display models in a table with columns: `name`, `version`, `backend`, `tasks`, and `status`.
2. THE Model_Viewer SHALL display the `tasks` field as a comma-separated list of task type strings.
3. THE Model_Viewer SHALL display status values with distinct badge labels: `active` as a green badge, `retired` as a grey badge, and `staging` as a yellow badge.
4. WHEN the Model_Viewer view loads, THE Portal_UI SHALL request the model list from `GET /portal/models`; THE Portal_API SHALL proxy this to `GET http://model-registry:5000` (models endpoint) within a 5-second timeout and return the result; IF the timeout is exceeded or the Model_Registry is unreachable, THE Portal_API SHALL return HTTP 502 identifying `"model-registry"` as the unavailable upstream.
5. IF a model row has `status` of `active` or `staging`, THEN THE Model_Viewer SHALL display a [Retire] button for that row.
6. IF a model row has `status` of `retired` or `staging`, THEN THE Model_Viewer SHALL display an [Activate] button for that row.
7. WHILE the model list is being fetched, THE Model_Viewer SHALL display a loading indicator and disable action buttons.
8. WHEN the [Retire] button for a model row is clicked, THE Portal_UI SHALL call `PATCH /portal/models/{name}/status` with `{"status": "retired"}`; IF the response is successful, THE model row SHALL update its status badge and button set immediately; IF the response is an error, THE Model_Viewer SHALL display a dismissible error message and leave the row unchanged.
9. WHEN the [Activate] button for a model row is clicked, THE Portal_UI SHALL call `PATCH /portal/models/{name}/status` with `{"status": "active"}`; IF the response is successful, THE model row SHALL update its status badge and button set immediately; IF the response is an error, THE Model_Viewer SHALL display a dismissible error message and leave the row unchanged.
10. WHEN `GET /portal/models` returns an empty list or an error, THE Model_Viewer SHALL display an appropriate empty-state or error-state message instead of the table.

---

### Requirement 7: Model Viewer — Model Status Management

**User Story:** As a platform operator, I want to activate or retire a model from the Portal, so that I can manage model lifecycle without direct access to the Model Registry API.

#### Acceptance Criteria

1. THE Portal_API SHALL expose `GET /portal/models` that fetches and returns the list of registered models from the Model_Registry.
2. THE Portal_API SHALL expose `PATCH /portal/models/{name}/status` accepting a JSON body with field `status` (string, one of: `active`, `retired`, `staging`).
3. WHEN `PATCH /portal/models/{name}/status` is called with a valid status value, THE Portal_API SHALL forward the status update to the Model_Registry and propagate the Model_Registry's response (status code and body) back to the caller unchanged.
4. WHEN the Portal_UI receives a successful response from `PATCH /portal/models/{name}/status`, THE Model_Viewer SHALL re-fetch the model list from `GET /portal/models` and refresh the displayed table within 2 seconds of receiving the response.
5. IF `status` in `PATCH /portal/models/{name}/status` is not one of `active`, `retired`, or `staging`, THEN THE Portal_API SHALL return HTTP 422 with a JSON body containing an error indicator and the list of allowed values.
6. IF the model `{name}` does not exist in the Model_Registry (registry returns 404), THEN THE Portal_API SHALL return HTTP 404 with a JSON body identifying the model name that was not found.
7. IF the Model_Registry is unreachable when processing any model management request, THEN THE Portal_API SHALL return HTTP 502 with a JSON error body identifying `"model-registry"` as the unavailable upstream.

---

### Requirement 8: Metrics Summary

**User Story:** As a platform operator, I want to see a summary of key platform metrics in the Portal, so that I have a quick operational health snapshot without opening Grafana.

#### Acceptance Criteria

1. WHEN `GET /portal/metrics/summary` is called, THE Portal_API SHALL query Prometheus using a 60-second range window and return a JSON object containing: `request_rate` (requests per second derived from `rate(llm_api_gateway_requests_total[60s])`), `error_rate` (fraction derived from `rate(llm_api_gateway_errors_total[60s]) / rate(llm_api_gateway_requests_total[60s])`), and `cache_hit_rate` (fraction derived from cache hit count over total cache lookups from `llm_cache_requests_total`); all fractional values SHALL be in the range 0.0–1.0.
2. IF the denominator for `error_rate` or `cache_hit_rate` is zero (no requests recorded in the window), THEN THE Portal_API SHALL return `null` for that field rather than computing a division by zero.
3. IF Prometheus is unreachable or returns a non-2xx response within 5 seconds, THEN THE Portal_API SHALL return HTTP 502 with a JSON error body identifying `"prometheus"` as the unavailable upstream.

---

### Requirement 9: Grafana Dashboard Embed

**User Story:** As a platform operator, I want to see the Grafana POC overview dashboard embedded in the Portal, so that I can monitor live metrics without switching to a separate browser tab.

#### Acceptance Criteria

1. THE Portal_UI SHALL embed the Grafana POC overview dashboard using an `<iframe>` with `src` set to `{GRAFANA_URL}/d/poc-overview/llm-platform-poc?orgId=1&kiosk`, where `GRAFANA_URL` is obtained from `GET /portal/config` (or equivalent Portal_API config endpoint).
2. THE Portal_UI SHALL set the `<iframe>` dimensions to full container width and a minimum height of 600px.
3. IF the `GRAFANA_URL` environment variable is not set, THEN THE Portal_API SHALL default the Grafana base URL to `http://grafana:3000`.
4. THE Portal_API SHALL expose a `GET /portal/config` endpoint (or include Grafana URL in an existing config response) so the Portal_UI can resolve the Grafana embed URL at runtime without the URL being hardcoded in the frontend bundle.
5. WHEN the Grafana iframe fails to load (e.g., Grafana is unreachable), THE Portal_UI SHALL display a fallback message indicating the dashboard is unavailable, replacing the iframe area.

---

### Requirement 10: Portal Observability

**User Story:** As a platform operator, I want the Portal API to emit structured logs and Prometheus metrics, so that it participates in the platform's observability stack.

#### Acceptance Criteria

1. WHEN any API request completes, THE Portal_API SHALL emit a single-line JSON log entry to stdout containing at minimum the fields `endpoint`, `status_code`, and `latency_ms`; each log entry SHALL be a single-line JSON object with no embedded newlines.
2. THE Portal_API SHALL expose Prometheus metrics at `GET /metrics` on port 9090 in Prometheus text exposition format.
3. THE Portal_API SHALL expose the metric `llm_portal_requests_total` as a counter labeled with `endpoint` and `status` (label values: `2xx`, `4xx`, `5xx`).
4. THE Portal_API SHALL expose the metric `llm_portal_latency_seconds` as a histogram labeled with `endpoint`, measured from when the request is received to when the response is fully sent.
5. THE Portal_API SHALL expose the metric `llm_portal_errors_total` as a counter labeled with `endpoint` and `error_code` (label values aligned with the audit schema error codes: `upstream_unavailable`, `validation_error`, `not_found`, `internal_error`).
6. THE Portal_API SHALL apply the log level specified by the `LOG_LEVEL` environment variable (accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`) to all log output at startup.
7. IF `LOG_LEVEL` is not set or is set to an unrecognized value, THEN THE Portal_API SHALL default to `INFO` log level.

---

### Requirement 11: Portal Deployment (Helm)

**User Story:** As a platform operator, I want the Portal packaged as a Helm chart, so that it can be deployed consistently to the Kubernetes cluster alongside other platform layers.

#### Acceptance Criteria

1. THE Portal SHALL be packaged as a Helm chart at `llm-platform/charts/admin-portal/` containing at minimum: `Chart.yaml`, `values.yaml`, `templates/deployment.yaml`, `templates/service.yaml`, `templates/ingress.yaml`, `templates/servicemonitor.yaml`, and `templates/_helpers.tpl`.
2. THE Portal's Helm chart SHALL expose a ClusterIP service on port 8084.
3. THE Portal's Helm chart SHALL configure an NGINX Ingress resource with host `llm-portal.local` routing all paths to the portal service.
4. THE Portal's Helm chart `values.yaml` SHALL include configurable environment variables: `API_GATEWAY_URL`, `AUDIT_STORE_URL`, `MODEL_REGISTRY_URL`, `GRAFANA_URL`, `GATEWAY_API_KEY`, and `LOG_LEVEL`; all values SHALL be overridable via `--set` flag or a custom values file.
5. THE Portal's Helm chart SHALL set `replicaCount: 1` and `autoscaling.enabled: false` for POC.
6. THE Portal's Helm chart SHALL set resource requests of `cpu: 100m` and `memory: 256Mi`, and resource limits of `cpu: 500m` and `memory: 512Mi`.
7. THE Portal's Kubernetes deployment SHALL set `securityContext.runAsNonRoot: true`, run the container as a UID of 1000 or greater, and SHALL NOT set `hostNetwork`, `hostPID`, `hostIPC`, or `privileged: true`.
8. THE Portal's Helm chart SHALL define a readiness probe and liveness probe both targeting `GET /portal/health`, with `initialDelaySeconds: 5`, `periodSeconds: 10`, and `failureThreshold: 3`.
9. THE Portal's Helm chart SHALL include a `ServiceMonitor` resource targeting the `/metrics` endpoint on port 9090 so that Prometheus can scrape portal metrics automatically.

---

### Requirement 12: Portal UI Single-Page Application

**User Story:** As a developer or operator, I want the Portal to be a single-page application, so that navigation between Playground, Audit Viewer, Model Viewer, and Metrics is fluid without full page reloads.

#### Acceptance Criteria

1. THE Portal_UI SHALL be a single-page application with distinct views for: Playground, Audit Viewer, Model Viewer, and Metrics (Grafana embed).
2. THE Portal_UI SHALL allow navigation between views without requiring a browser page reload; WHEN a view is navigated to, THE browser URL SHALL update to reflect the active view so that each view is directly addressable via a URL.
3. WHEN any Portal_API call returns a non-2xx HTTP status code, THE Portal_UI SHALL display a dismissible error banner containing the HTTP status code and the `message` field from the response body (or the full response body if no `message` field is present); the banner SHALL persist until dismissed by the user or replaced by a subsequent error.
4. THE Portal_UI SHALL require no authentication or login screen for POC.
5. WHEN the Grafana iframe within the Metrics view fails to load, THE Portal_UI SHALL replace the iframe area with a static message indicating the Grafana dashboard is currently unavailable, without affecting other views.
