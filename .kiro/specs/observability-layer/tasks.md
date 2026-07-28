# Implementation Plan: Observability Layer

## Overview

Implement the POC observability stack as three parallel tracks that converge at
integration: (1) the `shared/observability` Python instrumentation module, (2) the
`llm-platform/charts/observability/` Helm chart with its Grafana dashboard, and (3)
per-layer adoption of the shared module across all six platform services. Property-based
tests (Hypothesis) validate invariants throughout. The optional OTel/Jaeger path is
scaffolded but disabled by default.

---

## Tasks

- [x] 1. Bootstrap shared observability package and test infrastructure
  - Create `shared/observability/__init__.py`, `shared/observability/metrics.py`,
    `shared/observability/logging.py`, and `shared/observability/middleware.py` as
    empty module stubs with `__all__` declarations
  - Create `shared/requirements.txt` (or update existing) to pin `structlog==24.4.0`,
    `prometheus-client==0.20.0`, `hypothesis==6.108.5`, `pytest==8.2.2`
  - Create `llm-platform/pytest.ini` (or update if present) with `[pytest]` section,
    `--tb=short`, and markers `pbt`, `unit`, `integration`, `smoke`
  - Create `llm-platform/tests/helm/conftest.py` with Hypothesis `ci` settings profile
    (`max_examples=100`, `deadline=None`, `suppress_health_check=[HealthCheck.too_slow]`)
  - Create empty test file stubs: `tests/property/test_observability.py`,
    `tests/unit/test_observability_logging.py`,
    `llm-platform/tests/helm/test_observability.py`
  - _Requirements: 2.1–2.21, 6.1–6.6, 8.1–8.7_

- [x] 2. Implement shared metrics module
  - [x] 2.1 Implement `make_layer_metrics()` and `LayerMetrics` in `shared/observability/metrics.py`
    - Define `VALID_LAYERS = ("api_gateway", "security", "router", "cache", "inference", "agent")`
    - Implement `make_layer_metrics(layer: str) -> LayerMetrics` raising `ValueError`
      for invalid layer names; creates `llm_{layer}_requests_total` Counter with
      labels `["status", "department", "model"]`; `llm_{layer}_latency_seconds`
      Histogram with label `["department"]` and buckets
      `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]`; `llm_{layer}_errors_total` Counter
      with labels `["error_code", "department"]`
    - Implement `LayerMetrics.record_request(status, department, model, latency_s)`
      and `LayerMetrics.record_error(error_code, department)` methods
    - Implement `validate_scrape_interval(s: str) -> None` raising `ValueError` when
      the numeric value in seconds is outside `[5, 300]`
    - Handle cache layer's additional `outcome` label on `llm_cache_requests_total`
    - _Requirements: 2.1–2.20, 3.4_

  - [ ]* 2.2 Write property test for metric registration correctness (Property 1)
    - **Property 1: Metric Registration Correctness** — for every valid layer,
      `make_layer_metrics(layer)` returns a `LayerMetrics` with correct metric names,
      label names, and histogram buckets
    - Use `st.sampled_from(VALID_LAYERS)` over 100 examples
    - Assert `requests_total._name`, `latency_seconds._name`, `errors_total._name`,
      and `latency_seconds._upper_bounds` match the contract
    - **Validates: Requirements 2.1–2.18**

  - [ ]* 2.3 Write property test for metric recording invariant (Property 2)
    - **Property 2: Metric Recording Invariant** — for any valid `(status, department,
      model, latency_s)`, `record_request()` increments counter by exactly 1 and
      increases histogram sum by exactly `latency_s`
    - Use a fresh `CollectorRegistry` per test invocation to avoid cross-test pollution
    - **Validates: Requirements 2.19, 2.20**

  - [ ]* 2.4 Write property test for scrape interval validation (Property 4)
    - **Property 4: Scrape Interval Validation** — `validate_scrape_interval("{n}s")`
      accepts `5 ≤ n ≤ 300` and raises `ValueError` outside that range
    - Generate integers via `st.integers(min_value=-1000, max_value=1000)`
    - **Validates: Requirement 3.4**

- [x] 3. Implement shared logging module
  - [x] 3.1 Implement `configure_structlog()`, `get_logger()`, and `emit()` in `shared/observability/logging.py`
    - Implement `configure_structlog(service: str, log_level: str = "INFO")`:
      reads `LOG_LEVEL` env var if `log_level` not supplied; falls back to `INFO`
      and emits a `WARN` log with `event="invalid_log_level"` for unrecognised values;
      configures `structlog` with `JSONRenderer` for single-line JSON output
    - Implement `get_logger(request_id: str = "none") -> structlog.BoundLogger`
      pre-bound with `request_id` and UTC `timestamp`
    - Implement `emit(logger, level, event, message, latency_ms=None, **data)`:
      validates `message` length ≤ 256 (truncates to 255 + `"..."` suffix if over);
      omits `latency_ms` from output when `None`; outputs fields in Log_Schema order;
      never raises — logging must not crash a service
    - _Requirements: 6.1–6.6, 7.1–7.4_

  - [ ]* 3.2 Write property test for log schema completeness (Property 5)
    - **Property 5: Log Schema Completeness** — for any valid
      `(level, service, request_id, event, message, data)`, `emit()` produces valid
      single-line JSON with all required fields and ISO-8601 UTC `timestamp`
    - Assert output parses as JSON, contains no `\n` within the value, and has
      all required fields: `timestamp`, `level`, `service`, `request_id`,
      `event`, `message`, `data`
    - **Validates: Requirements 6.1, 6.2, 6.3**

  - [ ]* 3.3 Write property test for log level filtering (Property 6)
    - **Property 6: Log Level Filtering** — a log entry is emitted iff
      `numeric(entry_level) >= numeric(configured_level)` using DEBUG < INFO < WARN < ERROR
    - Generate pairs `(configured_level, entry_level)` from
      `st.sampled_from(["DEBUG", "INFO", "WARN", "ERROR"])`
    - **Validates: Requirement 6.4**

  - [ ]* 3.4 Write property test for sensitive data exclusion (Property 7)
    - **Property 7: Sensitive Data Exclusion** — the `LoggingMiddleware` never extracts
      `imf.request.messages[].content` or PII values to pass to `emit()`; serialised
      output does not contain any generated sensitive string
    - Generate `sensitive_string` via `st.text(min_size=5, max_size=200)`; verify
      middleware source does not access `.messages` items' `.content`; verify
      `emit()` output does not contain the sensitive string
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4**

  - [ ]* 3.5 Write unit tests for logging edge cases
    - `test_invalid_log_level_falls_back_to_info` — `LOG_LEVEL=VERBOSE` falls back
      to INFO and emits a WARN with `event="invalid_log_level"`
    - `test_message_truncated_at_256_chars` — 300-char message truncated to 256
    - `test_request_scoped_latency_ms_present` — request log includes `latency_ms`
    - `test_non_request_log_omits_latency_ms` — startup log has no `latency_ms` key
    - `test_request_id_none_for_startup` — `request_id == "none"` for startup event
    - _Requirements: 6.2, 6.3, 6.4_

- [x] 4. Checkpoint — Verify shared module tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement shared logging middleware
  - [x] 5.1 Implement `LoggingMiddleware` and `PrometheusMiddleware` in `shared/observability/middleware.py`
    - `LoggingMiddleware`: Starlette `BaseHTTPMiddleware` that extracts `request_id`
      from the `X-Request-ID` header (or `"none"` if absent), calls `get_logger()`,
      calls `dispatch()` timing the response, then calls `emit()` with `event`,
      `level`, `latency_ms`, and safe fields only — never passing `request.body()`
      or any IMF content fields to `emit()`
    - `PrometheusMiddleware`: wraps the existing per-layer prometheus middleware
      pattern; records `record_request()` on the layer's `LayerMetrics` after
      `dispatch()` completes; handles `status` mapping from HTTP status codes to
      `success | error | blocked`
    - Export `LoggingMiddleware` and `PrometheusMiddleware` from
      `shared/observability/__init__.py`
    - _Requirements: 2.19, 2.20, 6.1, 6.3, 7.1–7.4_

- [x] 6. Build the Helm chart skeleton
  - [x] 6.1 Create Helm chart files for `llm-platform/charts/observability/`
    - Create `Chart.yaml` with `apiVersion: v2`, `name: observability`,
      `version: 0.1.0`, `appVersion: "0.1.0"`, and `kube-prometheus-stack`
      dependency pinned to `58.3.3` with `condition: kubePrometheusStack.enabled`
    - Create `values.yaml` with all POC defaults: `replicaCount: 1`,
      `kubePrometheusStack.enabled: true`, Prometheus 7d retention, 10Gi PVC,
      single-replica Grafana with `adminPassword: "poc-admin"`, sidecar dashboard
      discovery enabled, `alertmanager.enabled: false`,
      `opentelemetry-collector.enabled: false`, `jaeger.enabled: false`,
      `ingress.enabled: false`, `observability.metrics.port: 9090`
    - Create `templates/_helpers.tpl` with `observability.fullname`,
      `observability.labels`, and `observability.selectorLabels` helper templates
    - Create `README.md` with deployment instructions and `helm dep update` note
    - _Requirements: 1.1–1.7, 8.1–8.7, 11.2, 11.3, 11.6, 11.7_

  - [x] 6.2 Create Helm chart templates for ConfigMap, Jaeger, and Ingress
    - Create `templates/configmap.yaml` with the `grafana-poc-dashboards` ConfigMap
      conditional on `kubePrometheusStack.enabled`, with label
      `grafana_dashboard: "1"`, embedding `poc-overview.json` via
      `{{ .Files.Get "dashboards/poc-overview.json" | nindent 4 }}`
    - Create `templates/jaeger-deployment.yaml` conditional on `jaeger.enabled`
      deploying Jaeger all-in-one with ClusterIP ports 16686 and 14250
    - Create `templates/jaeger-service.yaml` conditional on `jaeger.enabled`
    - Create `templates/ingress.yaml` conditional on `ingress.enabled` (disabled by default)
    - _Requirements: 4.2, 4.3, 4.4, 8.5, 8.6, 9.4_

- [x] 7. Author the Grafana dashboard JSON
  - [x] 7.1 Create `llm-platform/charts/observability/dashboards/poc-overview.json`
    - Define dashboard root with `"title": "LLM Platform POC Overview"` and a 12-column
      grid layout
    - Panel 1 — "Request Rate" (`timeseries`): `rate(llm_api_gateway_requests_total[1m])`
    - Panel 2 — "Error Rate" (`timeseries`): sum of
      `rate(llm_api_gateway_requests_total{status="error"}[1m])` +
      `rate(llm_security_requests_total{status="error"}[1m])` +
      `rate(llm_router_requests_total{status="error"}[1m])` +
      `rate(llm_cache_requests_total{status="error"}[1m])` +
      `rate(llm_inference_requests_total{status="error"}[1m])` +
      `rate(llm_agent_requests_total{status="error"}[1m])`
    - Panel 3 — "End-to-End Latency P95" (`timeseries`): one target per layer using
      `histogram_quantile(0.95, sum(rate(llm_{layer}_latency_seconds_bucket[1m])) by (le))`
    - Panel 4 — "Cache Hit Rate" (`gauge`): `rate(llm_cache_requests_total{outcome="hit"}[1m]) / rate(llm_cache_requests_total[1m])`
    - Panel 5 — "Security Blocks" (`timeseries`): `rate(llm_security_requests_total{outcome="block"}[1m])`
    - Panel 6 — "Inference Requests" (`timeseries`): `rate(llm_inference_requests_total[1m])` with `by (model)` legend
    - Panel 7 — "Active Agent Sessions" (`stat`): `llm_agent_requests_total` as fallback
    - _Requirements: 5.1–5.8_

  - [ ]* 7.2 Write property test for dashboard panel structural invariant (Property 9)
    - **Property 9: Dashboard Panel Structural Invariant** — every panel in
      `poc-overview.json` has non-empty `title`, `type`, and `targets`, and every
      target has a non-empty `expr`; dashboard root `title == "LLM Platform POC Overview"`
    - Use `st.sampled_from(panels)` over 100 examples
    - **Validates: Requirements 5.1–5.8**

  - [ ]* 7.3 Write unit tests for dashboard structure
    - `test_dashboard_json_is_valid` — `poc-overview.json` parses as valid JSON
    - `test_dashboard_title` — root title equals `"LLM Platform POC Overview"`
    - `test_seven_panels_present` — `len(dashboard["panels"]) == 7`
    - _Requirements: 5.1_

- [x] 8. Implement ServiceMonitor templates and Helm structural tests
  - [x] 8.1 Create reusable ServiceMonitor template for each platform layer chart
    - Add `templates/servicemonitor.yaml` to each of the six layer Helm charts
      (`api-gateway`, `security-layer`, `router`, `cache`, `inference-ollama`,
      `agent-framework`) conditional on `observability.metrics.enabled`
    - Each template must carry `release: observability` label, set `spec.endpoints[0].path: /metrics`,
      `spec.endpoints[0].port: metrics`, and `spec.endpoints[0].interval` defaulting
      to `"15s"` via `| default "15s"`
    - Update each layer's `service.yaml` to declare the metrics port by name:
      `- name: metrics`, `port: 9090`, `targetPort: 9090`, `protocol: TCP`
    - _Requirements: 3.1, 3.2, 3.3, 3.5_

  - [ ]* 8.2 Write property test for ServiceMonitor structural invariant (Property 3)
    - **Property 3: ServiceMonitor Structural Invariant** — for each layer chart
      rendered with `observability.metrics.enabled=true`, the output contains
      exactly one ServiceMonitor with label `release: observability`,
      `spec.endpoints[0].path == "/metrics"`, `spec.endpoints[0].port == "metrics"`,
      and a valid interval string
    - Use `st.sampled_from(LAYER_CHARTS)` over 100 examples; render via `helm template`
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [ ]* 8.3 Write property test for dependency version pinning (Property 8)
    - **Property 8: Dependency Version Pinning** — every `Chart.yaml` under
      `llm-platform/charts/` with `dependencies` must have version strings that
      are exact semver (no `*`, `x`, `^`, `~x.x`) 
    - Parse all charts via `st.sampled_from(CHARTS)` over 100 examples
    - **Validates: Requirements 8.1, 8.4**

  - [ ]* 8.4 Write property test for POC scope enforcement (Property 10)
    - **Property 10: POC Scope Enforcement** — rendering the observability chart
      with default values must not produce any resource referencing `docker.elastic.co`,
      `dcgm-exporter`, kind `Alertmanager`, OTel `filter` processor,
      `replicas > 1` for Prometheus or Grafana, or Jaeger/OTel Deployments
    - Deterministic check tagged `@pytest.mark.unit`
    - **Validates: Requirements 11.1–11.7**

- [x] 9. Checkpoint — Verify Helm chart and dashboard tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Refactor api_gateway layer to use shared observability module
  - [x] 10.1 Refactor `api_gateway/metrics.py` to use `make_layer_metrics()`
    - Replace ad-hoc `Counter` and `Histogram` definitions with a single
      `make_layer_metrics("api_gateway")` call at module level
    - Align labels from existing `status_code`, `path` to the contract labels
      `status`, `department`, `model` on `requests_total` and `department` on
      `latency_seconds`
    - Export `LAYER_METRICS: LayerMetrics` for use by middleware and routers
    - _Requirements: 2.1, 2.2, 2.3, 2.19, 2.20_

  - [x] 10.2 Refactor `api_gateway/middleware/logging.py` to use shared logging
    - Replace `print(json.dumps(...))` with `configure_structlog("api_gateway")`
      in `api_gateway/main.py` startup; replace inline JSON logging calls in the
      middleware with `get_logger(request_id)` and `emit()`
    - Ensure `request_id` is extracted from `X-Request-ID` header and propagated
    - Ensure no `imf.request.messages[].content` or auth header values are passed
      to `emit()`
    - _Requirements: 6.1–6.6, 7.1–7.4_

  - [x] 10.3 Refactor `api_gateway/middleware/prometheus.py` to use shared PrometheusMiddleware
    - Replace the existing prometheus middleware with `PrometheusMiddleware` from
      `shared.observability.middleware`
    - Verify that `LAYER_METRICS.record_request()` is called with correct
      `status`, `department`, and `model` values extracted from the IMF
    - _Requirements: 2.19, 2.20_

- [x] 11. Refactor security, router, cache, inference, and agent layers
  - [x] 11.1 Refactor `security` layer metrics and logging
    - Apply same `make_layer_metrics("security")` and `configure_structlog`
      pattern as api_gateway; update `security/metrics.py` and
      `security/middleware/logging.py`
    - _Requirements: 2.4, 2.5, 2.6, 2.19, 2.20, 6.1–6.6_

  - [x] 11.2 Refactor `router` layer metrics and logging
    - Apply `make_layer_metrics("router")` and shared logging middleware;
      update `router/metrics.py` if present
    - _Requirements: 2.7, 2.8, 2.9, 2.19, 2.20, 6.1–6.6_

  - [x] 11.3 Refactor `cache` layer metrics and logging
    - Apply `make_layer_metrics("cache")` — note that `llm_cache_requests_total`
      requires the additional `outcome` label (`hit`|`miss`); update `cache/metrics.py`
    - _Requirements: 2.10, 2.11, 2.12, 2.19, 2.20, 6.1–6.6_

  - [x] 11.4 Refactor `inference` layer metrics and logging
    - Apply `make_layer_metrics("inference")` and shared logging middleware;
      update `inference/metrics.py` if present
    - _Requirements: 2.13, 2.14, 2.15, 2.19, 2.20, 6.1–6.6_

  - [x] 11.5 Refactor `agent` layer metrics and logging
    - Apply `make_layer_metrics("agent")` and shared logging middleware;
      update `agent/metrics.py` if present
    - _Requirements: 2.16, 2.17, 2.18, 2.19, 2.20, 6.1–6.6_

- [ ] 12. Checkpoint — Verify per-layer refactoring and integration tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Write Helm integration and smoke tests
  - [ ] 13.1 Implement integration tests in `llm-platform/tests/helm/test_observability.py`
    - `test_grafana_poc_dashboards_configmap_rendered` — `helm template` output
      contains ConfigMap `grafana-poc-dashboards` with `data["poc-overview.json"]`
    - `test_prometheus_service_clusterip_port_9090` — rendered manifests contain
      a ClusterIP Service on port `9090` for Prometheus
    - `test_grafana_datasource_sidecar_enabled` — `values.yaml` has
      `grafana.sidecar.datasources.enabled: true`
    - `test_jaeger_enabled_renders_deployment` — rendering with `jaeger.enabled=true`
      produces a Deployment with `jaeger` in its name
    - `test_otel_wrong_port_fails` — rendering with `otlp.grpc.port=4318` returns
      a non-zero exit code with an error referencing port `4317`
    - _Requirements: 1.6, 4.2, 4.4, 4.7, 8.6, 9.1, 9.2_

  - [ ]* 13.2 Write smoke tests for values.yaml POC scope guards
    - `test_alertmanager_disabled_by_default` — default values have `alertmanager.enabled: false`
    - `test_tracing_disabled_by_default` — default values have `opentelemetry-collector.enabled: false` and `jaeger.enabled: false`
    - `test_prometheus_retention_7d` — default values set `prometheusSpec.retention: "7d"`
    - `test_pvc_10gi` — default values request `10Gi` storage
    - `test_single_replica_prometheus_grafana` — `replicaCount: 1` for both
    - _Requirements: 8.2, 8.3, 11.3, 11.5, 11.6, 11.7_

- [x] 14. Scaffold optional OTel/Jaeger tracing (disabled by default)
  - [x] 14.1 Wire OTel instrumentation into shared middleware (feature-flagged)
    - Add `configure_tracing(service: str, otel_endpoint: str) -> None` to
      `shared/observability/middleware.py`; wrap in `try/except ImportError` so the
      service starts even if `opentelemetry-instrumentation-fastapi` is not installed
    - When tracing is enabled, set span attributes `llm.request_id`, `llm.user_id`,
      `llm.department`, `llm.layer`, `llm.model`, `llm.task_type`,
      `http.status_code`, `llm.latency_ms`; propagate `traceparent` header on
      all outbound `httpx` calls; never set span attributes from
      `imf.request.messages[].content` or PII values
    - Guard the call in each layer's `main.py` behind
      `if settings.observability.tracing.enabled`
    - _Requirements: 9.1, 9.3, 9.5_

- [ ] 15. Final checkpoint — Full test suite passes
  - Ensure all tests pass (property, unit, integration, smoke). Ask the user if
    questions arise before closing.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP delivery
- Each task references specific requirements for traceability
- The shared module (`shared/observability/`) must be importable by all six layers;
  add it to each layer's `PYTHONPATH` or install as an editable local package
- The existing `api_gateway/metrics.py` and `api_gateway/middleware/logging.py`
  are the reference implementation for the refactoring pattern applied in tasks 10–11
- Use a fresh `prometheus_client.CollectorRegistry()` in every property test to
  prevent `Duplicated timeseries` `ValueError` from cross-test pollution
- OTel/Jaeger (task 14) is opt-in; it is scaffolded but disabled in `values.yaml`
  by default and only exercised when `jaeger.enabled=true`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "6.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.2", "3.3", "3.4", "3.5", "5.1", "6.2"] },
    { "id": 3, "tasks": ["7.1"] },
    { "id": 4, "tasks": ["7.2", "7.3", "8.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "10.1"] },
    { "id": 6, "tasks": ["10.2", "10.3"] },
    { "id": 7, "tasks": ["11.1", "11.2", "11.3", "11.4", "11.5"] },
    { "id": 8, "tasks": ["13.1", "13.2"] },
    { "id": 9, "tasks": ["14.1"] }
  ]
}
```
