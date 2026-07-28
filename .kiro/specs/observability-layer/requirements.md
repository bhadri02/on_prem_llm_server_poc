# Requirements Document

## Introduction

The Observability Layer is a cross-cutting platform service that provides metrics collection, structured logging standards, dashboard visualization, and optional distributed tracing for the Enterprise On-Premises LLM Platform POC. It gives operators real-time visibility into request flow, error rates, latency, cache efficiency, and security activity across all platform layers (API Gateway, Security & Governance, Intelligent Router, Cache, Inference, and Agent Framework). The layer is deployed via the `kube-prometheus-stack` Helm chart under `llm-platform/charts/observability/` and auto-discovers scrape targets through Kubernetes `ServiceMonitor` resources.

---

## Glossary

- **Observability_Stack**: The deployed set of Prometheus, Grafana, and optionally OTel Collector and Jaeger, deployed under `llm-platform/charts/observability/`.
- **Prometheus**: The time-series metrics collection and storage service, deployed via `kube-prometheus-stack`.
- **Grafana**: The dashboard visualization service rendering the "LLM Platform POC Overview" dashboard.
- **ServiceMonitor**: A `monitoring.coreos.com/v1` Kubernetes custom resource in each platform layer's Helm chart that tells Prometheus where to scrape metrics.
- **Metrics_Endpoint**: The HTTP `/metrics` endpoint exposed on port `9090` by each platform layer service.
- **Structured_Log**: A single-line JSON object written to stdout by a platform layer service, conforming to the mandatory log schema.
- **Log_Schema**: The mandatory JSON structure `{ timestamp, level, service, request_id, event, message, latency_ms, data }`.
- **OTel_Collector**: The OpenTelemetry Collector service that receives OTLP spans on gRPC port `4317` and forwards them to Jaeger (optional for POC).
- **Jaeger**: The distributed trace visualization backend, receiving spans from the OTel_Collector (optional for POC).
- **POC_Dashboard**: The single Grafana dashboard named "LLM Platform POC Overview" with the seven defined panels.
- **Dashboard_ConfigMap**: The Kubernetes ConfigMap `grafana-poc-dashboards` that mounts the dashboard JSON into Grafana.
- **kube-prometheus-stack**: The Helm chart dependency that bundles Prometheus, Grafana, and the Prometheus Operator.
- **request_id**: The UUID-v4 field present in every IMF message and every Structured_Log entry, used for cross-layer correlation.
- **Platform_Layer**: Any of the six instrumented services: `api_gateway`, `security`, `router`, `cache`, `inference`, `agent`.
- **Sensitive_Data**: Raw LLM prompt content, personally identifiable information (PII), or API keys.

---

## Requirements

### Requirement 1: Prometheus Deployment

**User Story:** As a platform operator, I want Prometheus deployed and running inside the cluster, so that I can collect and store metrics from all platform layers.

#### Acceptance Criteria

1. THE Observability_Stack SHALL deploy Prometheus using `kube-prometheus-stack` as a Helm chart dependency in `llm-platform/charts/observability/`.
2. THE Observability_Stack SHALL configure Prometheus with a metric retention period of 7 days.
3. THE Observability_Stack SHALL configure Prometheus storage with a `PersistentVolumeClaim` of at least 10Gi using `ReadWriteOnce` access mode.
4. THE Observability_Stack SHALL expose the Prometheus UI via a `ClusterIP` Service on port `9090`, accessible only within the cluster network and not reachable from outside the cluster.
5. IF the Prometheus pod has not reached the `Running` state within 5 minutes of the Helm install completing, THEN the deployment SHALL surface an error message indicating the pod failed to start.
6. THE Observability_Stack SHALL configure the Prometheus Operator to discover all `ServiceMonitor` resources carrying the label `release: observability` when the Helm release is named `observability`.
7. IF the Prometheus Operator cannot be configured to discover `ServiceMonitor` resources with the label `release: observability`, THEN THE Observability_Stack SHALL fail the Helm deployment with an error message indicating that the ServiceMonitor selector configuration is invalid or missing.

---

### Requirement 2: Per-Layer Metrics Instrumentation

**User Story:** As a platform operator, I want each platform layer to expose a standard set of Prometheus metrics, so that I can monitor requests, latency, and errors consistently across all layers.

#### Acceptance Criteria

1. THE api_gateway service SHALL expose the counter metric `llm_api_gateway_requests_total` with labels `status`, `department`, and `model` on its `/metrics` endpoint at port `9090`.
2. THE api_gateway service SHALL expose the histogram metric `llm_api_gateway_latency_seconds` with label `department` and buckets `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]` on its `/metrics` endpoint at port `9090`.
3. THE api_gateway service SHALL expose the counter metric `llm_api_gateway_errors_total` with labels `error_code` and `department` on its `/metrics` endpoint at port `9090`.
4. THE security service SHALL expose the counter metric `llm_security_requests_total` with labels `status`, `department`, and `model` on its `/metrics` endpoint at port `9090`.
5. THE security service SHALL expose the histogram metric `llm_security_latency_seconds` with label `department` and buckets `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]` on its `/metrics` endpoint at port `9090`.
6. THE security service SHALL expose the counter metric `llm_security_errors_total` with labels `error_code` and `department` on its `/metrics` endpoint at port `9090`.
7. THE router service SHALL expose the counter metric `llm_router_requests_total` with labels `status`, `department`, and `model` on its `/metrics` endpoint at port `9090`.
8. THE router service SHALL expose the histogram metric `llm_router_latency_seconds` with label `department` and buckets `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]` on its `/metrics` endpoint at port `9090`.
9. THE router service SHALL expose the counter metric `llm_router_errors_total` with labels `error_code` and `department` on its `/metrics` endpoint at port `9090`.
10. THE cache service SHALL expose the counter metric `llm_cache_requests_total` with labels `status`, `department`, `model`, and `outcome` (values: `hit`, `miss`) on its `/metrics` endpoint at port `9090`.
11. THE cache service SHALL expose the histogram metric `llm_cache_latency_seconds` with label `department` and buckets `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]` on its `/metrics` endpoint at port `9090`.
12. THE cache service SHALL expose the counter metric `llm_cache_errors_total` with labels `error_code` and `department` on its `/metrics` endpoint at port `9090`.
13. THE inference service SHALL expose the counter metric `llm_inference_requests_total` with labels `status`, `department`, and `model` on its `/metrics` endpoint at port `9090`.
14. THE inference service SHALL expose the histogram metric `llm_inference_latency_seconds` with label `department` and buckets `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]` on its `/metrics` endpoint at port `9090`.
15. THE inference service SHALL expose the counter metric `llm_inference_errors_total` with labels `error_code` and `department` on its `/metrics` endpoint at port `9090`.
16. THE agent service SHALL expose the counter metric `llm_agent_requests_total` with labels `status`, `department`, and `model` on its `/metrics` endpoint at port `9090`.
17. THE agent service SHALL expose the histogram metric `llm_agent_latency_seconds` with label `department` and buckets `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]` on its `/metrics` endpoint at port `9090`.
18. THE agent service SHALL expose the counter metric `llm_agent_errors_total` with labels `error_code` and `department` on its `/metrics` endpoint at port `9090`.
19. WHEN a platform layer receives a request and reaches a terminal outcome, THE Platform_Layer SHALL increment the appropriate `llm_{layer}_requests_total` counter with a `status` label value of `success`, `error`, or `blocked` corresponding to the outcome, and with the corresponding `department` and `model` label values.
20. WHEN a platform layer completes processing a request, THE Platform_Layer SHALL record the duration from request receipt to response sent (inclusive) in the appropriate `llm_{layer}_latency_seconds` histogram.
21. THE `error_code` label on all `llm_{layer}_errors_total` counters SHALL reflect the platform-level outcome error code matching the `error_code` field in the audit record schema.

---

### Requirement 3: ServiceMonitor Auto-Discovery

**User Story:** As a platform operator, I want Prometheus to automatically discover each layer's metrics endpoint without manual scrape configuration, so that adding or redeploying a layer does not require changes to the Prometheus configuration.

#### Acceptance Criteria

1. THE Helm chart for each of the six Platform_Layer services (`api_gateway`, `security`, `router`, `cache`, `inference`, `agent`) SHALL include a `ServiceMonitor` resource in `templates/servicemonitor.yaml`.
2. THE ServiceMonitor resource SHALL carry the label `release: observability` so that the Prometheus Operator deployed by `kube-prometheus-stack` auto-discovers it.
3. THE ServiceMonitor resource SHALL configure the scrape path as `/metrics`, the named port as `metrics`, and a default scrape interval of `15s`.
4. WHEN the scrape interval is overridden per ServiceMonitor, THE configured interval SHALL be between `5s` and `300s` inclusive; values outside this range SHALL cause the Helm chart to fail validation.
5. THE ServiceMonitor resource SHALL use a `selector.matchLabels` with the key `app` targeting only the Service resource of the same Platform_Layer, using the value of the layer's `app` label.
6. WHEN Prometheus scrapes a `/metrics` endpoint at the configured scrape interval, THE Prometheus SHALL store the collected samples in its time-series database and retain them for a minimum of 7 days.

---

### Requirement 4: Grafana Deployment and Dashboard Provisioning

**User Story:** As a platform operator, I want a Grafana instance pre-provisioned with the POC overview dashboard, so that I can visualize platform health immediately after deployment without manual dashboard import.

#### Acceptance Criteria

1. THE Observability_Stack SHALL deploy Grafana as part of the `kube-prometheus-stack` Helm dependency with Grafana enabled.
2. WHILE Grafana is in running and ready state (Kubernetes Ready condition is `True`), THE Observability_Stack SHALL configure Grafana to load dashboards from the Kubernetes ConfigMap named `grafana-poc-dashboards` into the folder named `LLM Platform POC`.
3. THE Observability_Stack SHALL store the POC_Dashboard JSON definition at `charts/observability/dashboards/poc-overview.json` in the repository.
4. WHILE Grafana is in running and ready state, THE Observability_Stack SHALL provision the POC_Dashboard from `poc-overview.json` into Grafana automatically within 60 seconds of the ready state being reached, without manual import.
5. WHILE Grafana is in running and ready state, THE Observability_Stack SHALL expose the Grafana UI via a `ClusterIP` Service on port `3000` within the cluster.
6. WHEN a platform operator accesses the Grafana UI, THE Grafana SHALL display the "LLM Platform POC Overview" dashboard in the "LLM Platform POC" folder within 5 seconds of the page loading.
7. WHILE Grafana is in running and ready state, THE Observability_Stack SHALL provision Prometheus as a default datasource in Grafana automatically, so that all POC_Dashboard panels can query Prometheus without manual datasource configuration.

---

### Requirement 5: POC Dashboard Panels

**User Story:** As a platform operator, I want the POC dashboard to show the seven key signal panels, so that I can assess platform health from a single screen.

#### Acceptance Criteria

1. THE POC_Dashboard SHALL be named "LLM Platform POC Overview" and its JSON definition SHALL be stored at `charts/observability/dashboards/poc-overview.json` in the repository.
2. THE POC_Dashboard SHALL include a "Request Rate" panel displaying `rate(llm_api_gateway_requests_total[1m])` in requests per second.
3. THE POC_Dashboard SHALL include an "Error Rate" panel displaying the sum of `rate(llm_api_gateway_requests_total{status="error"}[1m])`, `rate(llm_security_requests_total{status="error"}[1m])`, `rate(llm_router_requests_total{status="error"}[1m])`, `rate(llm_cache_requests_total{status="error"}[1m])`, `rate(llm_inference_requests_total{status="error"}[1m])`, and `rate(llm_agent_requests_total{status="error"}[1m])` in errors per second.
4. THE POC_Dashboard SHALL include an "End-to-End Latency P95" panel displaying `histogram_quantile(0.95, sum(rate(llm_{layer}_latency_seconds_bucket[1m])) by (le))` computed separately for each of the six Platform_Layer services (`api_gateway`, `security`, `router`, `cache`, `inference`, `agent`).
5. THE POC_Dashboard SHALL include a "Cache Hit Rate" panel displaying the ratio of `rate(llm_cache_requests_total{outcome="hit"}[1m])` to `rate(llm_cache_requests_total[1m])`, expressed as a value between 0 and 1.
6. THE POC_Dashboard SHALL include a "Security Blocks" panel displaying `rate(llm_security_requests_total{outcome="block"}[1m])`.
7. THE POC_Dashboard SHALL include an "Inference Requests" panel displaying `rate(llm_inference_requests_total[1m])` broken down by the `model` label.
8. WHERE the agent layer is deployed, THE POC_Dashboard SHALL include an "Active Agent Sessions" panel; WHEN a dedicated gauge metric for active agent sessions is present, THE panel SHALL use that gauge metric as the authoritative source; IF no dedicated gauge metric is present, THEN THE panel SHALL use `llm_agent_requests_total` as a fallback approximation.

---

### Requirement 6: Structured JSON Logging Standard

**User Story:** As a platform operator, I want every platform layer to emit structured JSON logs to stdout using a consistent schema, so that I can correlate log entries across layers using `request_id` and view them with `kubectl logs`.

#### Acceptance Criteria

1. THE Platform_Layer SHALL write every log entry to stdout as a single-line JSON object conforming to the Log_Schema.
2. THE Log_Schema SHALL contain the fields: `timestamp` (ISO-8601 string), `level` (one of `INFO`, `WARN`, or `ERROR`), `service` (the layer name string), `request_id` (UUID-v4 string, or the literal string `"none"` for non-request-scoped entries such as startup and shutdown events), `event` (snake_case machine-readable event name string), `message` (human-readable string, max 256 characters), `latency_ms` (integer milliseconds, present for request processing events, omitted for non-request-scoped entries), and `data` (object for additional structured context).
3. THE Platform_Layer SHALL include the `request_id` field in every Structured_Log entry; for log entries not associated with a specific request (e.g., startup, shutdown, configuration load), THE Platform_Layer SHALL set `request_id` to the literal string `"none"`.
4. THE Platform_Layer SHALL set the log verbosity level based on the `LOG_LEVEL` environment variable, accepting values `DEBUG`, `INFO`, `WARN`, and `ERROR`; the default level SHALL be `INFO`; IF `LOG_LEVEL` is set to an unrecognized value, THEN THE Platform_Layer SHALL default to `INFO` and emit a `WARN`-level log entry indicating the invalid value.
5. IF a platform layer processes a request that contains Sensitive_Data, THEN THE Platform_Layer SHALL omit the Sensitive_Data fields entirely from all Structured_Log entries (fields are absent, not redacted or replaced with placeholder values).
6. THE Platform_Layer SHALL use Python `structlog` or a JSON formatter that produces output that is valid JSON on a single line per entry and conforms to the Log_Schema; the choice of library SHALL be applied consistently across all layers.

---

### Requirement 7: Log Content Safety

**User Story:** As a compliance officer, I want to guarantee that LLM prompt content, PII, and API keys are never written to logs, so that observability data does not create a data leakage or compliance risk.

#### Acceptance Criteria

1. THE Platform_Layer SHALL never include the content of IMF `request.messages[].content` or `response.content` fields in any Structured_Log field, including the `data` object and the `message` field.
2. THE Platform_Layer SHALL never include personally identifiable information in any Structured_Log field, where personally identifiable information is defined as any value present in the IMF `governance.pii_fields_detected` list or any value from the IMF `user.user_id` field beyond its opaque identifier form.
3. THE Platform_Layer SHALL never include API keys, bearer tokens, or credential values in any Structured_Log field, including values sourced from request headers, the IMF `user.auth_method` context, or any runtime secret.
4. WHEN a request processing event occurs, THE Platform_Layer SHALL emit a Structured_Log entry containing `request_id`, `event`, `level`, and `latency_ms`, where none of these fields are populated with values derived from IMF `request.messages[].content`, `response.content`, `governance.pii_fields_detected` values, or credential values.

---

### Requirement 8: Helm Chart for the Observability Stack

**User Story:** As a platform engineer, I want the entire observability stack deployable via a single Helm chart, so that the POC environment can be set up and torn down repeatably.

#### Acceptance Criteria

1. THE Observability_Stack SHALL be packaged as a Helm chart at `llm-platform/charts/observability/` with `kube-prometheus-stack` declared as a chart dependency using a pinned semantic version (no wildcards or version ranges).
2. THE Observability_Stack SHALL include a `values.yaml` that sets `alertmanager.enabled: false` for the POC scope.
3. THE Observability_Stack SHALL include a `values.yaml` that sets `opentelemetry-collector.enabled: false` and `jaeger.enabled: false` as the default POC configuration.
4. THE Observability_Stack SHALL provide a `Chart.yaml` that declares the chart `name`, `version`, `appVersion`, and all external dependency versions as pinned semantic versions without wildcards or version ranges.
5. THE Observability_Stack SHALL include a `templates/` directory containing `deployment.yaml`, `service.yaml`, `configmap.yaml`, `servicemonitor.yaml`, `networkpolicy.yaml`, and `_helpers.tpl`; the `values.yaml` SHALL include `replicaCount`, `image.repository`, `image.tag`, `image.pullPolicy`, `resources.requests`, `resources.limits`, and `observability.metrics.port` fields.
6. THE Observability_Stack SHALL include a Kubernetes ConfigMap in `templates/configmap.yaml` that provisions the `poc-overview.json` dashboard into Grafana under the `LLM Platform POC` folder via `grafana.dashboardsConfigMaps`, so that dashboard availability is independently verifiable without accessing Grafana's internal state.
7. WHEN a platform engineer runs `helm install observability llm-platform/charts/observability/` against a cluster with sufficient resources, THEN after all pods reach `Running` and `Ready` state, THE platform engineer SHALL be able to retrieve the "LLM Platform POC Overview" dashboard by querying the Grafana API at `GET /api/dashboards/db/llm-platform-poc-overview` using the default admin credentials defined in `values.yaml`, receiving an HTTP `200` response.

---

### Requirement 9: Optional Distributed Tracing

**User Story:** As a platform engineer, I want the option to enable distributed tracing via OpenTelemetry and Jaeger, so that end-to-end request traces across all layers can be inspected when time permits during the POC.

#### Acceptance Criteria

1. WHERE distributed tracing is enabled (`opentelemetry-collector.enabled: true`), THE Observability_Stack SHALL deploy an OTel_Collector configured to receive OTLP spans on gRPC port `4317`.
2. IF the OTel_Collector is configured with a port other than `4317`, THEN THE Observability_Stack SHALL fail the Helm deployment with an error message indicating the invalid port value and the required value of `4317`.
3. WHERE distributed tracing is enabled, THE OTel_Collector SHALL forward all received spans to the Jaeger collector endpoint at `jaeger-collector:14250` using plaintext gRPC with TLS disabled (`tls.insecure: true`).
4. WHERE distributed tracing is enabled (`jaeger.enabled: true`), THE Observability_Stack SHALL deploy Jaeger and expose the Jaeger UI via a `ClusterIP` Service on port `16686`, accessible within the cluster namespace without external ingress.
5. WHERE distributed tracing is enabled, THE Platform_Layer SHALL instrument its FastAPI application using `opentelemetry-instrumentation-fastapi` and emit spans containing the mandatory attributes: `llm.request_id`, `llm.user_id`, `llm.department`, `llm.layer`, `llm.model`, `llm.task_type`, `http.status_code`, and `llm.latency_ms`; THE Platform_Layer SHALL propagate the `traceparent` header on all outbound inter-service HTTP calls.
6. WHERE distributed tracing is enabled, WHEN a request traverses at least two Platform_Layer services, THE OTel_Collector SHALL receive a span from each traversed layer all sharing the same `trace_id`, and all spans SHALL be visible as a single trace in the Jaeger UI.

---

### Requirement 10: Observability Stack Health Endpoint

**User Story:** As a platform engineer, I want the observability stack itself to expose a health check endpoint, so that Kubernetes liveness and readiness probes can confirm the stack is operational.

#### Acceptance Criteria

1. WHEN Prometheus is ready to accept queries, THE Prometheus SHALL respond to `GET /-/healthy` with HTTP `200`.
2. IF Prometheus is not ready to accept queries, THEN THE Prometheus SHALL respond to `GET /-/healthy` with HTTP `503`.
3. WHEN Grafana has an active database connection and its backend process is running, THE Grafana SHALL respond to `GET /api/health` with HTTP `200`.
4. IF Grafana does not have an active database connection or its backend process is not running, THEN THE Grafana SHALL respond to `GET /api/health` with a non-`200` HTTP status code.
5. WHEN the Prometheus `/-/healthy` endpoint returns a non-`200` response for 3 consecutive checks with a period of `10s` and a timeout of `5s`, THE Kubernetes liveness probe SHALL restart the Prometheus pod.
6. WHEN the Grafana `/api/health` endpoint returns a non-`200` response for 3 consecutive checks with a period of `10s` and a timeout of `5s`, THE Kubernetes liveness probe SHALL restart the Grafana pod.
7. WHEN the Prometheus `/-/healthy` endpoint returns HTTP `200`, THE Kubernetes readiness probe SHALL mark the Prometheus pod as ready to receive traffic.
8. WHEN the Grafana `/api/health` endpoint returns HTTP `200`, THE Kubernetes readiness probe SHALL mark the Grafana pod as ready to receive traffic.

---

### Requirement 11: POC Scope Exclusions

**User Story:** As a project manager, I want the POC scope boundaries explicitly documented, so that the team does not invest time building features that are deferred to Phase 2.

#### Acceptance Criteria

1. THE Observability_Stack SHALL not deploy Elasticsearch or Kibana for log aggregation in the POC; the absence is verifiable by confirming no container image from the `docker.elastic.co` registry is running in the `observability` namespace.
2. THE Observability_Stack SHALL not deploy a DCGM GPU metrics exporter in the POC; the absence is verifiable by confirming no container with the `dcgm-exporter` image is running in the `observability` namespace.
3. THE Observability_Stack SHALL not configure Alertmanager notification routing (PagerDuty, Slack, or email) in the POC; the absence is verifiable by confirming `alertmanager.enabled: false` is set in `values.yaml` and no Alertmanager pod is running in the `observability` namespace.
4. THE Observability_Stack SHALL not implement OTel sensitive data filtering pipelines in the POC; the absence is verifiable by confirming no `filter` processor is defined in the OTel Collector configuration.
5. THE Observability_Stack SHALL not retain Prometheus metrics for longer than 7 days in the POC and SHALL not configure any remote storage backend that would extend retention beyond 7 days.
6. THE Observability_Stack SHALL deploy exactly one replica each of Prometheus and Grafana in the POC, with `replicaCount: 1` set in `values.yaml` and no sharding, HA, or multi-instance configuration enabled.
7. THE Observability_Stack SHALL deploy OTel Collector and Jaeger with `opentelemetry-collector.enabled: false` and `jaeger.enabled: false` as the default values in `values.yaml`; these components are opt-in only and SHALL NOT be deployed unless explicitly overridden at install time.
