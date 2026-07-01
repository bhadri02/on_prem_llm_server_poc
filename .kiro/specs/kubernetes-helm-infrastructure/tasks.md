# Implementation Plan: Kubernetes & Helm Infrastructure Layer

## Overview

Convert the design document into an incremental series of Helm chart authoring tasks. Each task produces valid, lint-clean YAML that builds toward a single `helm install` command that deploys the full ten-sub-chart platform stack. Tasks proceed in this order: (1) complete the four existing sub-charts, (2) scaffold the six new sub-charts, (3) create the umbrella chart and values files, (4) write automation scripts, (5) create the property-based and unit test harness.

All charts target the `llm-poc` namespace, single replica, no Istio, no Vault, no HPA.

---

## Tasks

- [x] 1. Set up the Helm test harness and property-test scaffolding
  - Create `llm-platform/tests/helm/` directory with `conftest.py`, `requirements-test.txt` (`pytest`, `hypothesis`, `pyyaml`), and a shared `helm_template` helper function that invokes `helm template` via subprocess and returns parsed YAML documents
  - Add `conftest.py` fixtures: `CHARTS` list of all 10 sub-chart names, `to_camel()` converter, and `umbrella_dir` fixture pointing to `llm-platform/`
  - Add `llm-platform/tests/helm/__init__.py` and `llm-platform/pytest.ini` (testpaths = tests/helm, markers for `pbt` and `unit`)
  - _Requirements: 14.1, 14.2_

- [x] 2. Complete the `audit-store` sub-chart
  - [x] 2.1 Fix `audit-store` values.yaml and deployment.yaml compliance gaps
    - Add missing `secretRef.name: "llm-poc-secrets"`, `serviceAccount.name: "llm-platform"`, `metricsPort: 9090`, `vault.enabled: false` keys to `values.yaml`
    - Update `resources.limits.cpu` to `"1"` and `resources.limits.memory` to `"1Gi"` (from current 500m/512Mi)
    - Replace per-key `secretKeyRef` for `AUDIT_API_KEY` in `deployment.yaml` with `envFrom.secretRef.name: llm-poc-secrets`
    - Add `serviceAccountName: {{ .Values.serviceAccount.name | default "llm-platform" }}` to pod spec
    - Fix image tag expression from `default .Chart.AppVersion` to `default "latest"`
    - Add `emptyDir` fallback volume when `persistence.enabled: false` using `{{- if .Values.persistence.enabled }}` / `{{- else }}` pattern
    - Add liveness/readiness probe fields (`periodSeconds`, `timeoutSeconds`, `failureThreshold`, `successThreshold`) to `values.yaml` matching the standard schema
    - _Requirements: 2.1, 2.2, 2.3, 3.2, 3.3, 4.2, 6.5, 6.6, 11.1, 11.2, 12.1_

  - [x] 2.2 Add `pvc.yaml` to `audit-store` templates
    - Create `llm-platform/charts/audit-store/templates/pvc.yaml` using the canonical PVC pattern conditioned on `persistence.enabled`
    - Verify `persistence.size: 5Gi`, `accessMode: ReadWriteOnce`, `storageClass: ""`
    - _Requirements: 6.1, 6.5_

  - [x] 2.3 Write unit tests for `audit-store` chart compliance
    - Test that `helm lint llm-platform/charts/audit-store` exits zero with zero warnings
    - Test that rendered Deployment contains `envFrom.secretRef.name: llm-poc-secrets` and not a bare `secretKeyRef`
    - Test that `persistence.enabled=false` renders `emptyDir` and no PVC resource
    - Test that rendered image uses `:latest` when `image.tag: ""`
    - _Requirements: 2.7, 4.2, 6.6, 16.1_

- [x] 3. Complete the `cache` sub-chart
  - [x] 3.1 Fix `cache` values.yaml and deployment.yaml compliance gaps
    - Add missing `secretRef.name: "llm-poc-secrets"`, `serviceAccount.name: "llm-platform"`, `vault.enabled: false` keys to `values.yaml`
    - Add standard liveness/readiness probe fields to `values.yaml` with `initialDelaySeconds: 15`, `periodSeconds: 15`, `timeoutSeconds: 5`, `failureThreshold: 3`, `successThreshold: 1`
    - Update `cache/values.yaml` Redis sub-dependency block to add `master.persistence.storageClass: ""` explicitly
    - Confirm `deployment.yaml` uses `{{ .Values.image.tag | default "latest" }}` (not `default .Chart.AppVersion`)
    - Add `serviceAccountName` and `envFrom.secretRef` to `deployment.yaml`; remove any per-key `secretKeyRef` entries
    - _Requirements: 2.2, 2.3, 3.3, 4.2, 6.3, 11.2, 12.3_

  - [x] 3.2 Write unit tests for `cache` chart compliance
    - Test that `helm lint llm-platform/charts/cache` exits zero
    - Test that `redis.master.persistence.storageClass` is empty string in rendered values
    - Test that Deployment uses `envFrom.secretRef`
    - _Requirements: 2.7, 6.3_

- [x] 4. Complete the `inference-ollama` sub-chart
  - [x] 4.1 Fix `inference-ollama` values.yaml and deployment compliance gaps
    - Update `adapter.image.repository` from `registry.internal/inference-adapter` to `registry.local/inference-adapter`
    - Add `secretRef.name: "llm-poc-secrets"`, `serviceAccount.name: "llm-platform"` to `values.yaml`
    - Add `secretRef` to `values.yaml` top-level (not nested under `adapter`) so umbrella chart can override `secretRef.name`
    - Verify Ollama container probe in `deployment.yaml` targets `path: /api/tags`, `port: 11434`, `initialDelaySeconds: 30`, `timeoutSeconds: 30`, `failureThreshold: 5`
    - Verify `init-job.yaml` is conditioned on `initJob.enabled` and contains `activeDeadlineSeconds: 6000` and `backoffLimit: 2`
    - Verify `init-job.yaml` handles empty `models.preload` list gracefully (exits 0)
    - Add `serviceAccountName` to both Ollama and adapter pod specs
    - _Requirements: 2.2, 2.3, 3.3, 9.1, 9.4, 9.5, 9.6, 11.3, 15.1_

  - [x] 4.2 Write unit tests for `inference-ollama` chart compliance
    - Test that `helm lint llm-platform/charts/inference-ollama` exits zero
    - Test that Ollama container probe path is `/api/tags` and `initialDelaySeconds: 30`
    - Test that init Job has `activeDeadlineSeconds: 6000` and `backoffLimit: 2`
    - Test that empty `models.preload` produces a Job that exits 0 (script guard present)
    - _Requirements: 9.1, 9.4, 9.5, 9.6, 11.3_

- [x] 5. Complete the `model-registry` sub-chart
  - [x] 5.1 Fix `model-registry` values.yaml and deployment.yaml compliance gaps
    - Update `resources` to: `requests.cpu: "100m"`, `requests.memory: "256Mi"`, `limits.cpu: "1"`, `limits.memory: "1Gi"` (from current 300m/256Mi limits)
    - Update `persistence.size` to `"2Gi"` (from current `1Gi`)
    - Add `secretRef.name: "llm-poc-secrets"`, `serviceAccount.name: "llm-platform"` to `values.yaml`
    - Replace `apiKeySecret` secretKeyRef pattern in `deployment.yaml` with `envFrom.secretRef.name: {{ .Values.secretRef.name }}`; remove the `apiKeySecret` block from `values.yaml`
    - Add `metricsPort: 9090` to `values.yaml` and add metrics port to the Service template
    - Fix image tag expression to `default "latest"` (not `default .Chart.AppVersion`)
    - Add `serviceAccountName` to pod spec
    - Add `emptyDir` fallback for `persistence.enabled: false`
    - Update probe `initialDelaySeconds` to `15` and `timeoutSeconds` to `5` to match standard schema
    - _Requirements: 2.2, 2.3, 3.3, 4.2, 6.4, 6.5, 6.6, 11.1, 11.2, 12.2_

  - [x] 5.2 Write unit tests for `model-registry` chart compliance
    - Test that `helm lint llm-platform/charts/model-registry` exits zero
    - Test that PVC size is `2Gi` in rendered manifest
    - Test that Deployment uses `envFrom.secretRef` not `secretKeyRef`
    - _Requirements: 2.7, 6.4, 4.2_

- [x] 6. Checkpoint — existing sub-charts pass lint
  - Run `helm lint` on all four completed sub-charts; confirm zero errors and zero warnings before proceeding to new chart scaffolding.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Scaffold the `api-gateway` sub-chart
  - [x] 7.1 Create `api-gateway` chart files
    - Create `llm-platform/charts/api-gateway/Chart.yaml` with `apiVersion: v2`, `name: api-gateway`, `version: 0.1.0`, `appVersion: "0.1.0"`
    - Create `llm-platform/charts/api-gateway/values.yaml` with: `service.port: 8080`, `metricsPort: 9090`, `image.repository: registry.local/api-gateway`, `image.tag: ""`, `image.pullPolicy: IfNotPresent`, `secretRef.name: "llm-poc-secrets"`, `serviceAccount.name: "llm-platform"`, standard resources (cpu:100m/mem:256Mi requests, cpu:1/mem:1Gi limits), standard probes, `autoscaling.enabled: false`, `vault.enabled: false`, `ingress.enabled: false`, `ingress.host: "llm-poc.local"`, `ingress.servicePort: 8080`, `env: {}`, `persistence.enabled: false`
    - _Requirements: 2.2, 5.2, 12.2, 15.1, 16.1, 16.3_

  - [x] 7.2 Create `api-gateway` Helm templates
    - Create `templates/_helpers.tpl` with `api-gateway.fullname`, `api-gateway.labels`, `api-gateway.selectorLabels`, `api-gateway.chart` using canonical pattern
    - Create `templates/deployment.yaml` using canonical pattern: `envFrom.secretRef`, `serviceAccountName`, both probes from values, `{{ .Values.image.tag | default "latest" }}`, metrics port
    - Create `templates/service.yaml` exposing `http` port 8080 and `metrics` port 9090
    - Create `templates/servicemonitor.yaml` selecting on `app.kubernetes.io/name: api-gateway`, `port: metrics`, `path: /metrics`, `interval: 30s`
    - Create `templates/networkpolicy.yaml` permitting unrestricted ingress/egress within namespace via `namespaceSelector`
    - Create `templates/ingress.yaml` routing `llm-poc.local /` → service port 8080, conditioned on `ingress.enabled`, `ingressClassName: nginx`, no TLS
    - Create `README.md` documenting service purpose, port 8080, cluster URL `http://api-gateway:8080`, all values keys, and Docker build command
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 5.2, 5.5, 15.1, 15.7, 18.1, 18.6_

  - [ ]* 7.3 Write unit tests for `api-gateway` chart
    - Test that `helm lint llm-platform/charts/api-gateway` exits zero
    - Test that Ingress is absent when `ingress.enabled=false` and present with host `llm-poc.local` when `ingress.enabled=true`
    - Test no `HorizontalPodAutoscaler`, `VirtualService`, or `DestinationRule` in rendered manifest
    - _Requirements: 2.7, 5.2, 18.1, 18.2_

- [x] 8. Scaffold the `security-layer` sub-chart
  - [x] 8.1 Create `security-layer` chart files
    - Create `Chart.yaml` (`name: security-layer`, `version: 0.1.0`)
    - Create `values.yaml` with: `service.port: 8081`, `metricsPort: 9090`, `image.repository: registry.local/security-layer`, standard defaults, and probes with `initialDelaySeconds: 60` (both liveness and readiness) to accommodate spaCy model load
    - All other fields use standard schema defaults (autoscaling/vault false, secretRef, serviceAccount)
    - _Requirements: 2.2, 11.4, 15.2_

  - [x] 8.2 Create `security-layer` Helm templates
    - Create `_helpers.tpl`, `deployment.yaml`, `service.yaml`, `servicemonitor.yaml`, `networkpolicy.yaml` using canonical patterns with `service.port: 8081`
    - No `ingress.yaml` needed for `security-layer`
    - Create `README.md` documenting service purpose, port, cluster URL, and values reference
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 15.2, 15.7_

  - [ ]* 8.3 Write unit tests for `security-layer` chart
    - Test that `helm lint llm-platform/charts/security-layer` exits zero
    - Test that rendered Deployment has `livenessProbe.initialDelaySeconds: 60` and `readinessProbe.initialDelaySeconds: 60`
    - _Requirements: 2.7, 11.4_

- [x] 9. Scaffold the `router` sub-chart
  - [x] 9.1 Create `router` chart files and templates
    - Create `Chart.yaml` (`name: router`, `version: 0.1.0`)
    - Create `values.yaml` with `service.port: 8082`, `metricsPort: 9090`, `image.repository: registry.local/router`, all standard schema fields
    - Create `_helpers.tpl`, `deployment.yaml`, `service.yaml`, `servicemonitor.yaml`, `networkpolicy.yaml` using canonical patterns
    - Create `README.md`
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 15.3, 15.7_

  - [ ]* 9.2 Write unit tests for `router` chart
    - Test that `helm lint llm-platform/charts/router` exits zero
    - Test both probes are present in rendered Deployment
    - _Requirements: 2.7, 11.1_

- [x] 10. Scaffold the `agent-framework` sub-chart
  - [x] 10.1 Create `agent-framework` chart files and templates
    - Create `Chart.yaml` (`name: agent-framework`, `version: 0.1.0`)
    - Create `values.yaml` with `service.port: 8083`, `metricsPort: 9090`, `image.repository: registry.local/agent-framework`, all standard schema fields
    - Create `_helpers.tpl`, `deployment.yaml`, `service.yaml`, `servicemonitor.yaml`, `networkpolicy.yaml` using canonical patterns
    - Create `README.md`
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 15.4, 15.7_

  - [ ]* 10.2 Write unit tests for `agent-framework` chart
    - Test that `helm lint llm-platform/charts/agent-framework` exits zero
    - Test both probes are present and use standard delay values
    - _Requirements: 2.7, 11.1, 11.2_

- [x] 11. Scaffold the `admin-portal` sub-chart
  - [x] 11.1 Create `admin-portal` chart files
    - Create `Chart.yaml` (`name: admin-portal`, `version: 0.1.0`)
    - Create `values.yaml` with `service.port: 8084`, `metricsPort: 9090`, `image.repository: registry.local/admin-portal`, `ingress.enabled: false`, `ingress.host: "llm-portal.local"`, `ingress.servicePort: 8084`, all standard schema fields
    - _Requirements: 2.2, 5.3, 15.5_

  - [x] 11.2 Create `admin-portal` Helm templates
    - Create `_helpers.tpl`, `deployment.yaml`, `service.yaml`, `servicemonitor.yaml`, `networkpolicy.yaml` using canonical patterns with port 8084
    - Create `templates/ingress.yaml` routing `llm-portal.local /` → port 8084, conditioned on `ingress.enabled`, `ingressClassName: nginx`, no TLS
    - Create `README.md`
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 5.3, 5.5, 15.5, 15.7, 18.6_

  - [ ]* 11.3 Write unit tests for `admin-portal` chart
    - Test that `helm lint llm-platform/charts/admin-portal` exits zero
    - Test Ingress host is `llm-portal.local` when `ingress.enabled=true`
    - _Requirements: 2.7, 5.3_

- [x] 12. Scaffold the `observability` sub-chart
  - [x] 12.1 Create `observability` chart files
    - Create `Chart.yaml` with `kube-prometheus-stack ~58.x` dependency from `https://prometheus-community.github.io/helm-charts`, conditioned on `kubePrometheusStack.enabled`
    - Create `values.yaml` with kube-prometheus-stack overrides: `grafana.adminPassword: "poc-admin"`, `grafana.service.type: ClusterIP`, `grafana.sidecar.datasources.enabled: true`, `alertmanager.enabled: false`, `prometheus.prometheusSpec.retention: "7d"`, `prometheus.prometheusSpec.serviceMonitorNamespaceSelector: {}`
    - Add `jaeger.enabled: false` toggle; when true, deploys `jaegertracing/all-in-one:latest` Deployment with ports 16686, 6831/UDP, 14268
    - Add `ingress.enabled: false`, `ingress.host: "grafana-poc.local"`, `ingress.servicePort: 3000`
    - _Requirements: 10.1, 10.2, 10.3, 10.5, 15.6_

  - [x] 12.2 Create `observability` Helm templates
    - Create `templates/_helpers.tpl` with `observability.fullname`, `observability.labels`, `observability.selectorLabels`
    - Create `templates/ingress.yaml` routing `grafana-poc.local /` → port 3000, conditioned on `ingress.enabled`, `ingressClassName: nginx`, no TLS
    - Create `templates/jaeger-deployment.yaml` conditioned on `jaeger.enabled` with all-in-one container image, named ports 16686/6831/14268
    - Create `templates/jaeger-service.yaml` conditioned on `jaeger.enabled`
    - Create `README.md` documenting Grafana URL, Prometheus URL, Jaeger URL, Grafana admin password, values keys
    - _Requirements: 5.4, 10.4, 10.5, 15.6, 15.7, 18.6_

  - [ ]* 12.3 Write unit tests for `observability` chart
    - Test that `helm lint llm-platform/charts/observability` exits zero (requires `helm dependency update` first)
    - Test that Jaeger resources are absent when `jaeger.enabled=false` and present when `jaeger.enabled=true`
    - Test Ingress host is `grafana-poc.local` when `ingress.enabled=true`
    - _Requirements: 2.7, 10.4, 10.5_

- [x] 13. Checkpoint — all ten sub-charts pass lint
  - Run `helm lint` on all 10 sub-charts; confirm zero errors and zero warnings before creating the umbrella chart.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Create the umbrella chart
  - [x] 14.1 Create `llm-platform/Chart.yaml`
    - Write `llm-platform/Chart.yaml` with `apiVersion: v2`, `name: llm-platform-poc`, `version: 0.1.0`, `appVersion: "0.1.0"`, `description: "Enterprise On-Prem LLM Platform — POC"`
    - Declare exactly 10 dependencies (api-gateway, security-layer, router, cache, inference-ollama, agent-framework, model-registry, audit-store, admin-portal, observability), each at `version: "0.1.0"`, `repository: "file://charts/<name>"`, and `condition: <camelCase>.enabled`
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 14.2 Create `llm-platform/values.yaml`
    - Write umbrella-level `values.yaml` setting baseline defaults: `replicaCount: 1`, `image.pullPolicy: IfNotPresent`, `autoscaling.enabled: false`, `vault.enabled: false`, `secretRef.name: "llm-poc-secrets"`, `serviceAccount.name: "llm-platform"`
    - Set all ten sub-chart condition keys to `enabled: false` as safe defaults (opt-in via values-poc.yaml)
    - _Requirements: 1.4_

  - [x] 14.3 Create `llm-platform/values-poc.yaml`
    - Write `values-poc.yaml` enabling all 10 sub-charts (`enabled: true` for each), setting `replicaCount: 1`, `autoscaling.enabled: false`, `vault.enabled: false`
    - Declare `services` block with all 10 DNS URLs: apiGateway, securityLayer, router, cache, inferenceOllama, inferenceAdapter, agentFramework, modelRegistry, auditStore, adminPortal
    - Set `ingress.enabled: true` for api-gateway, admin-portal, observability
    - Set `inferenceOllama.models.preload: ["llama3.2:3b"]`
    - Set `global.imageRegistry: registry.local`
    - Confirm file contains NO literal secret values (`poc-secret-key`, passwords, tokens)
    - _Requirements: 1.5, 4.3, 7.1, 9.7, 18.2, 18.3, 18.4_

  - [ ]* 14.4 Write unit tests for umbrella chart structure
    - Test `llm-platform/Chart.yaml` has exactly 10 dependencies with correct condition keys
    - Test `values-poc.yaml` has all 10 `enabled: true` flags
    - Test `values-poc.yaml` `services` block has all 10 DNS URLs in correct format
    - Test no literal secret values in `values.yaml` or `values-poc.yaml`
    - _Requirements: 1.2, 1.5, 4.3, 7.1_

- [x] 15. Validate umbrella chart renders cleanly
  - [x] 15.1 Run `helm dependency update` and full template render
    - Run `helm dependency update ./llm-platform` to resolve all 10 sub-chart `.tgz` archives from `llm-platform/charts/`
    - Run `helm template llm-poc ./llm-platform --values ./llm-platform/values-poc.yaml` and verify exit code 0 and valid YAML output
    - Run `helm lint ./llm-platform` and verify zero errors and zero warnings
    - _Requirements: 1.3, 1.6, 14.1, 14.2, 14.3_

  - [ ]* 15.2 Write property test: Property 1 — Condition gating (zero resources when disabled)
    - **Property 1: For any sub-chart, setting `<camelCase>.enabled=false` produces zero resources for that chart in the rendered umbrella manifest**
    - Use `hypothesis.given(chart=st.sampled_from(CHARTS))` with `max_examples=100`
    - Run `helm template` with `--set <camelCase>.enabled=false` and assert no resource `metadata.name` contains the chart name
    - **Validates: Requirements 1.7**

  - [ ]* 15.3 Write property test: Property 2 — Full render produces expected resource set
    - **Property 2: For any combination of enabled sub-charts, `helm template` exits code 0 and produces Deployment, Service, ServiceMonitor, NetworkPolicy for each enabled chart**
    - Use `hypothesis` to generate random subsets of the 10 charts, enable only those, assert correct resource kinds exist
    - **Validates: Requirements 1.6, 14.2**

- [x] 16. Write the property-based tests for structural invariants
  - [ ] 16.1 Write property test: Property 3 — Every sub-chart has required files
    - **Property 3: For any sub-chart directory, all required files exist (Chart.yaml, values.yaml, README.md, templates/_helpers.tpl, deployment.yaml, service.yaml, servicemonitor.yaml, networkpolicy.yaml)**
    - Parameterize over all 10 chart names using `@pytest.mark.parametrize`; no hypothesis needed (deterministic file check)
    - **Validates: Requirements 2.1, 15.7**

  - [ ] 16.2 Write property test: Property 4 — Every sub-chart values schema is compliant
    - **Property 4: For any sub-chart values.yaml, required top-level keys are present with correct types and POC-appropriate defaults**
    - Parameterize over all 10 charts; parse `values.yaml` with PyYAML and assert all required keys exist with correct types
    - **Validates: Requirements 2.2, 12.1**

  - [ ] 16.3 Write property test: Property 5 — Every rendered Deployment has both probes
    - **Property 5: For any sub-chart rendered with default values, the Deployment has livenessProbe and readinessProbe both configured with httpGet**
    - Use `hypothesis.given(chart=st.sampled_from(CHARTS))` with `max_examples=50`; render each chart standalone, assert both probes present
    - **Validates: Requirements 11.1, 11.2**

  - [ ] 16.4 Write property test: Property 6 — No literal secret values in any values file
    - **Property 6: For any values file across all sub-charts and umbrella chart, scanning the content finds no literal secret values (poc-secret-key, non-empty passwords, tokens)**
    - Parameterize over all values files; read and scan for known forbidden strings and non-empty password patterns
    - **Validates: Requirements 4.3**

  - [ ] 16.5 Write property test: Property 7 — Persistence toggle replaces PVC with emptyDir
    - **Property 7: For any sub-chart declaring persistence.enabled, rendering with persistence.enabled=false produces emptyDir volume and no PVC resource**
    - Parameterize over stateful charts (audit-store, model-registry, inference-ollama); render with flag false; assert emptyDir present, no PVC kind in documents
    - **Validates: Requirements 6.5, 6.6**

  - [ ] 16.6 Write property test: Property 8 — No prohibited resources in any rendered chart
    - **Property 8: For any rendered manifest from any sub-chart, no VirtualService, DestinationRule, AuthorizationPolicy, PeerAuthentication, or HorizontalPodAutoscaler resources appear**
    - Use `hypothesis.given(chart=st.sampled_from(CHARTS))` with `max_examples=100`; render and assert no document has a prohibited `kind`
    - **Validates: Requirements 18.1, 18.2**

  - [ ] 16.7 Write property test: Property 9 — Service URL consistency
    - **Property 9: Every entry in the services block of values-poc.yaml corresponds to a Service resource in the rendered umbrella manifest when all sub-charts are enabled**
    - Parse `services` block from `values-poc.yaml`; render full umbrella chart; assert each service hostname maps to a Service `metadata.name`
    - **Validates: Requirements 7.1**

  - [ ] 16.8 Write property test: Property 10 — Image tag fallback behavior
    - **Property 10: Any sub-chart rendered with image.tag="" uses `:latest`; any sub-chart rendered with image.tag set to a non-empty value uses that exact tag**
    - Use `hypothesis.given(chart=st.sampled_from(CHARTS), tag=st.one_of(st.just(""), st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll","Nd")))))` with `max_examples=100`
    - **Validates: Requirements 16.1, 16.5**

- [x] 17. Checkpoint — all property and unit tests pass
  - Run full test suite: `cd llm-platform && pytest tests/helm/ -v --tb=short`
  - Fix any template defects surfaced by property tests before proceeding to scripts.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Write `scripts/deploy.sh`
  - [x] 18.1 Implement deploy script preflight and namespace setup (Steps 1–5)
    - Create `scripts/deploy.sh` as an executable bash script with `set -euo pipefail`
    - Implement `--dry-run` flag (prints commands without executing) and `--uninstall` flag (`helm uninstall llm-poc` + delete namespace)
    - Step 1: Verify `kubectl` and `helm ≥3` are on PATH; exit with descriptive error and install instructions if missing (_Requirements: 17.4, 17.5_)
    - Step 2: Query `kubectl get nodes -o json` to validate ≥8 allocatable CPU and ≥16Gi RAM; if below minimum, print warning with detected vs required capacity and prompt user to confirm before continuing (_Requirements: 17.1, 17.2, 17.3_)
    - Step 3: Install NGINX Ingress Controller via upstream static manifest; wait for controller pod `Ready` (_Requirements: 5.1_)
    - Step 4: Create `llm-poc` namespace idempotently via `--dry-run=client | kubectl apply -f -`; create label `kubernetes.io/metadata.name: llm-poc` (_Requirements: 3.1, 3.5_)
    - Step 5: Create `llm-poc-secrets` Secret idempotently with `GATEWAY_API_KEY=poc-secret-key` and `REDIS_PASSWORD=""`; create `llm-platform` ServiceAccount idempotently (_Requirements: 4.1, 4.4, 3.2_)
    - Print elapsed time per step using `date +%s` diff
    - _Requirements: 8.1, 8.3, 8.4_

  - [x] 18.2 Implement deploy script Helm install and rollout wait (Steps 6–11)
    - Step 6: Run `helm dependency update ./llm-platform` (_Requirements: 1.3_)
    - Step 7: Run `helm upgrade --install llm-poc ./llm-platform --namespace llm-poc --values ./llm-platform/values-poc.yaml` (_Requirements: 8.2_)
    - Step 8: Run `helm upgrade --install observability ./llm-platform/charts/observability --namespace monitoring --create-namespace` (_Requirements: 3.6_)
    - Step 9: Run `kubectl rollout status deployment --namespace llm-poc` for all Deployments; exit non-zero on timeout (_Requirements: 8.2_)
    - Step 10: Check for Pending pods; print names of resource-constrained pods as warning (not failure) (_Requirements: 8.2, 12.7_)
    - Step 11: Wait for Ollama init Job completion; print model pull progress (_Requirements: 9.1_)
    - Print total elapsed time on success; wrap every step with `|| { echo "[FAIL] Step N: ..."; exit 1; }` (_Requirements: 8.3, 8.4_)
    - _Requirements: 8.2, 8.3, 8.4, 8.5_

  - [ ]* 18.3 Write unit tests for deploy script logic
    - Test `--dry-run` flag prints kubectl/helm commands and exits 0 without executing them (use mock)
    - Test that missing `kubectl` or `helm` triggers exit with descriptive error
    - Test that insufficient cluster capacity triggers the confirmation prompt
    - _Requirements: 8.1, 8.5, 17.4, 17.5_

- [ ] 19. Write `scripts/smoke-test.sh`
  - [ ] 19.1 Implement smoke test health checks and E2E chat (Checks 1–2)
    - Create `scripts/smoke-test.sh` as executable bash; accept `--namespace` (default: `llm-poc`) and `--host` (default: `llm-poc.local`) flags
    - Check 1: `GET /health` on all 9 services via `kubectl exec` into api-gateway pod; assert HTTP 200 and `"status": "ok"` in JSON body; print `[PASS]` or `[FAIL]` with elapsed ms
    - Check 2: `POST /v1/chat/completions` with `X-Api-Key: poc-secret-key` and `{"model": "llama3.2:3b", "messages": [...]}`; assert HTTP 200, non-empty `choices[0].message.content`, non-null `id`; capture `id` for Check 3
    - _Requirements: 13.2, 13.3, 13.4_

  - [ ] 19.2 Implement smoke test audit, cache, and injection checks (Checks 3–5)
    - Check 3: `GET /audit/requests/<id>` from Check 2; assert ≥3 audit events covering distinct `layer` values
    - Check 4: Repeat identical chat POST from Check 2; assert `cache.lookup_hit: true` in response
    - Check 5: POST with injection prompt; assert HTTP 400 and security block field in response body
    - Print per-check `[PASS]`/`[FAIL]` and elapsed ms; print summary table and `All X checks passed` or `X of Y checks failed` at end; exit 0 on all pass, exit 1 on any fail
    - _Requirements: 13.1, 13.2, 13.5, 13.6, 13.7, 13.8, 13.9_

  - [ ]* 19.3 Write unit tests for smoke-test script argument parsing
    - Test that `--namespace` and `--host` flags correctly override defaults in generated curl commands
    - Test that any single check failure causes exit code 1 and prints failure count in summary
    - _Requirements: 13.1, 13.9_

- [x] 20. Write `scripts/README.md`
  - Create `scripts/README.md` documenting: prerequisites (kubectl, helm ≥3, cluster access), `deploy.sh` flags (`--dry-run`, `--uninstall`), expected output for a successful deploy, `/etc/hosts` entries required (`<cluster-ip> llm-poc.local`, `<cluster-ip> llm-portal.local`, `<cluster-ip> grafana-poc.local`), instructions for determining `<cluster-ip>` on k3s / kind / minikube, and `smoke-test.sh` flags (`--namespace`, `--host`)
  - _Requirements: 5.7, 8.7_

- [x] 21. Final checkpoint — full umbrella render and dry-run deploy
  - Run `helm lint ./llm-platform` confirming zero errors and zero warnings across all charts
  - Run `helm template llm-poc ./llm-platform --values ./llm-platform/values-poc.yaml | kubectl apply --dry-run=client -f -` and confirm exit code 0
  - Run full pytest suite: `cd llm-platform && pytest tests/helm/ -v --tb=short`
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP path; core chart authoring tasks are never optional
- Each task references specific requirements for traceability
- Checkpoints at tasks 6, 13, 17, and 21 ensure incremental validation before moving to the next phase
- The design uses Python (pytest + hypothesis) as the test framework — all property test code is Python
- `helm dependency update` must be run before any `helm lint` or `helm template` on the umbrella chart
- The observability sub-chart requires `helm repo add prometheus-community https://prometheus-community.github.io/helm-charts` before `helm dependency update`
- Property tests 1, 2, 5, 6, 8, and 10 use hypothesis for random input generation; properties 3, 4, 7, and 9 are deterministic parametrized tests
- The `inference-ollama` model-pull init Job uses `curlimages/curl:latest` to call the Ollama pull API; it is not a `kubectl exec` approach


## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "4.2", "5.2"] },
    { "id": 3, "tasks": ["7.1", "8.1", "9.1", "10.1", "11.1", "12.1"] },
    { "id": 4, "tasks": ["7.2", "8.2", "9.2", "10.2", "11.2", "12.2"] },
    { "id": 5, "tasks": ["7.3", "8.3", "11.3", "12.3"] },
    { "id": 6, "tasks": ["14.1"] },
    { "id": 7, "tasks": ["14.2", "14.3"] },
    { "id": 8, "tasks": ["14.4", "15.1"] },
    { "id": 9, "tasks": ["15.2", "15.3", "16.1", "16.2", "16.3", "16.4", "16.5", "16.6", "16.7", "16.8"] },
    { "id": 10, "tasks": ["18.1"] },
    { "id": 11, "tasks": ["18.2", "19.1"] },
    { "id": 12, "tasks": ["18.3", "19.2"] },
    { "id": 13, "tasks": ["19.3", "20.1"] }
  ]
}
```
