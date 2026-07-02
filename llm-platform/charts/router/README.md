# router Helm Chart

Deploys the **Intelligent Router** (Layer 3) of the Enterprise On-Premises LLM Platform. This is a FastAPI microservice that classifies every platform request by task type, selects the correct inference model, health-checks backends, consults the Cache Layer, dispatches to the Inference Adapter, and writes audit events — all in a deterministic six-stage pipeline.

---

## Purpose

The Intelligent Router sits between the Security & Governance Layer (Layer 2) and the downstream inference backends. It is the only component that performs model selection and fallback orchestration. Every request from the Security Layer arrives at `POST /route` as a governance-enriched Internal Message Format (IMF) envelope. The router returns the same IMF with the `routing`, `cache`, and `response` blocks populated.

A second endpoint, `POST /v1/chat/completions`, accepts OpenAI-compatible request bodies so LangChain's `ChatOpenAI` client can communicate with the platform without custom integration code.

### Six-stage routing pipeline

```
Governance gate → Task Classification → Model Selection
    → Health Check → Cache Lookup → Inference Dispatch
    → (async) Cache Write + Audit Log
```

1. **Governance gate** — rejects the request immediately (HTTP 400) if `governance.content_safety_passed` is `false` or absent; no downstream calls are made.
2. **Task Classification** — scans `request.messages` against keyword rules loaded from `task_classifier_rules.yaml`; always overwrites any inbound `request.task_type`. Priority order: `code` → `reasoning` → `summarization` → `translation` → `chat`.
3. **Model Selection** — looks up the primary model in `model_matrix.yaml` for the classified task type (`auto` mode) or validates the caller-pinned model (`pinned` mode).
4. **Health Check** — issues `GET <health_url>` with a 5-second timeout; treats any non-200, redirect, timeout, or connection error as a backend failure and advances the fallback chain.
5. **Cache Lookup** — POSTs to the Cache Layer; on a HIT with valid `response.content`, returns immediately without calling inference.
6. **Inference Dispatch** — POSTs the full IMF to the Inference Adapter; on success triggers background cache-write and audit tasks and returns the completed IMF to the caller.

---

## Port Layout

| Port | Container Port Name | Description |
|------|---------------------|-------------|
| `8082` | `http` | Primary API — `POST /route`, `POST /v1/chat/completions`, `GET /health` |
| `9090` | `metrics` | Prometheus metrics scrape endpoint — `GET /metrics` |

Both ports are exposed on the Kubernetes Service (`ClusterIP`). Both are started in the container via `uvicorn ... & uvicorn ... & wait` in the Dockerfile `CMD`.

### Liveness and Readiness Probes

Both probes call `GET /health` on port `8082`. The `/health` endpoint returns HTTP 200 only when both `model_matrix.yaml` and `task_classifier_rules.yaml` have been successfully loaded into memory. If either file failed to load at startup, the router exits with a non-zero code and the probe never succeeds.

| Probe | Initial Delay | Period | Timeout | Failure Threshold |
|-------|---------------|--------|---------|-------------------|
| Liveness | 15 s | 15 s | 5 s | 3 |
| Readiness | 15 s | 15 s | 5 s | 3 |

---

## ConfigMap — Configuration Files

The chart creates a ConfigMap named `<release-name>-router-config` containing both YAML configuration files. This ConfigMap is mounted **read-only** at `/config` inside the container, making both files available at `/config/model_matrix.yaml` and `/config/task_classifier_rules.yaml`.

### `model_matrix.yaml`

Defines the registry of available inference backends and the default model mapping per task type. The router loads this once at startup; a load failure causes the router to refuse to start.

| Field | Description |
|-------|-------------|
| `models.<name>.backend` | Inference engine type (e.g. `ollama`) |
| `models.<name>.endpoint` | Base URL of the inference backend |
| `models.<name>.tasks` | Task types this model supports |
| `models.<name>.health_url` | URL the Health Checker polls before dispatching inference |
| `models.<name>.fallback` | Name of the next model to try on failure, or `null` |
| `task_defaults.<task_type>` | Primary model selected in `auto` routing mode |

Default ConfigMap content (POC — single Ollama backend):

```yaml
models:
  llama3.2-3b:
    backend: ollama
    endpoint: http://inference-ollama:11434
    tasks: [chat, code, reasoning, summarization, translation]
    health_url: http://inference-ollama:11434/api/tags
    fallback: null

task_defaults:
  chat: llama3.2-3b
  code: llama3.2-3b
  reasoning: llama3.2-3b
  summarization: llama3.2-3b
  translation: llama3.2-3b
```

### `task_classifier_rules.yaml`

Defines keyword lists per task type used by the keyword-based classifier. Rules are evaluated as case-insensitive substring matches against the concatenated content of all request messages.

| Field | Description |
|-------|-------------|
| `rules.<task_type>` | List of keyword strings triggering that task type |
| `default` | Task type returned when no keyword matches (always `chat`) |

Default ConfigMap content (POC — covers common code, reasoning, summarization, and translation signals):

```yaml
rules:
  code:       [code, function, python, javascript, debug, write a script, implement, ...]
  reasoning:  [reason, analyze, logic, deduce, think step by step, chain of thought, ...]
  summarization: [summarize, summary, tldr, brief, overview, recap, condense, ...]
  translation: [translate, in french, in spanish, in german, in chinese, ...]
default: chat
```

To customise either file, override the ConfigMap after install or supply your own values via `--set-file`:

```bash
helm upgrade router ./llm-platform/charts/router \
  --namespace llm-poc \
  --set-file 'configmap.modelMatrix=./my_model_matrix.yaml' \
  --set-file 'configmap.taskRules=./my_task_rules.yaml'
```

---

## Configurable Values

All values are in `values.yaml`. Override using `--set key=value` on the command line or by supplying a custom values file with `-f`.

### Deployment

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `replicaCount` | int | `1` | Number of pod replicas (POC: single replica) |
| `image.repository` | string | `registry.local/router` | Container image repository |
| `image.tag` | string | `""` | Image tag; defaults to `appVersion` (`0.1.0`) when empty |
| `image.pullPolicy` | string | `IfNotPresent` | Kubernetes image pull policy |

### Service

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `service.type` | string | `ClusterIP` | Kubernetes Service type |
| `service.port` | int | `8082` | Service port for the HTTP API |

### Environment Variables (Router Configuration)

The following environment variables are injected directly into the container from `values.yaml`. The three marked **required** cause the router to refuse to start if absent or empty.

| Value | Env Var | Type | Default | Required | Description |
|-------|---------|------|---------|----------|-------------|
| `env.MODEL_MATRIX_PATH` | `MODEL_MATRIX_PATH` | string | `/config/model_matrix.yaml` | **Yes** | Path to model matrix YAML inside container |
| `env.TASK_RULES_PATH` | `TASK_RULES_PATH` | string | `/config/task_classifier_rules.yaml` | **Yes** | Path to classifier rules YAML inside container |
| `env.AUDIT_STORE_URL` | `AUDIT_STORE_URL` | string | `""` | **Yes** | Full base URL of the Audit Store — must be set at deploy time |
| `env.CACHE_URL` | `CACHE_URL` | string | `http://cache:8086` | No | Cache Layer base URL |
| `env.INFERENCE_ADAPTER_URL` | `INFERENCE_ADAPTER_URL` | string | `http://inference-adapter:8087` | No | Inference Adapter base URL |
| `env.LOG_LEVEL` | `LOG_LEVEL` | string | `INFO` | No | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `env.INFERENCE_TIMEOUT_SECONDS` | `INFERENCE_TIMEOUT_SECONDS` | string | `"120"` | No | Inference request timeout in seconds; valid range `[1, 600]` |
| `env.HEALTH_CHECK_TIMEOUT_SECONDS` | `HEALTH_CHECK_TIMEOUT_SECONDS` | string | `"5"` | No | Backend health check timeout in seconds; valid range `[1, 30]` |

> `AUDIT_STORE_URL` has no safe default. The router lifespan handler validates it is non-empty before accepting connections; a missing value causes an immediate `sys.exit(1)` at startup.

### Resources

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `resources.requests.cpu` | string | `200m` | CPU request |
| `resources.requests.memory` | string | `256Mi` | Memory request |
| `resources.limits.cpu` | string | `1` | CPU limit |
| `resources.limits.memory` | string | `1Gi` | Memory limit |

### Observability

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `observability.metrics.enabled` | bool | `true` | Create a Prometheus `ServiceMonitor` resource targeting port `9090` |
| `observability.metrics.port` | int | `9090` | Port on which Prometheus metrics are exposed |
| `observability.tracing.enabled` | bool | `false` | Enable OpenTelemetry tracing (deferred to Phase 2) |
| `observability.tracing.endpoint` | string | `http://otel-collector:4317` | OTel collector gRPC endpoint (used when tracing is enabled) |

### Autoscaling (Phase 2 — disabled for POC)

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `autoscaling.enabled` | bool | `false` | Enable Horizontal Pod Autoscaler |
| `autoscaling.minReplicas` | int | `2` | HPA minimum replica count (when enabled) |
| `autoscaling.maxReplicas` | int | `10` | HPA maximum replica count (when enabled) |
| `autoscaling.targetCPUUtilizationPercentage` | int | `70` | HPA CPU utilisation target (when enabled) |

### Vault (Phase 2 — disabled for POC)

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `vault.enabled` | bool | `false` | Enable HashiCorp Vault Agent sidecar |
| `vault.role` | string | `router-role` | Vault role scoped to this layer's secrets |
| `vault.secretPath` | string | `secret/llm-platform/router` | Vault KV path for layer secrets |

### Network Policy (disabled for POC)

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `networkPolicy.enabled` | bool | `false` | Enable Kubernetes `NetworkPolicy` restricting pod-to-pod traffic |

---

## Example `helm install` Commands

### Minimal POC installation

```bash
# Install into the llm-poc namespace with the Audit Store URL set
helm install router ./llm-platform/charts/router \
  --namespace llm-poc \
  --create-namespace \
  --set image.tag=0.1.0 \
  --set env.AUDIT_STORE_URL=http://audit-store:9200
```

### Full POC installation (all service URLs explicit)

```bash
helm install router ./llm-platform/charts/router \
  --namespace llm-poc \
  --create-namespace \
  --set image.tag=0.1.0 \
  --set env.AUDIT_STORE_URL=http://audit-store:9200 \
  --set env.CACHE_URL=http://cache:8086 \
  --set env.INFERENCE_ADAPTER_URL=http://inference-adapter:8087 \
  --set env.LOG_LEVEL=DEBUG
```

### Upgrade an existing release

```bash
helm upgrade router ./llm-platform/charts/router \
  --namespace llm-poc \
  --set image.tag=0.1.1 \
  --set env.AUDIT_STORE_URL=http://audit-store:9200
```

### Install from a custom values file

```bash
# my-router-values.yaml:
#   image:
#     tag: "0.1.0"
#   env:
#     AUDIT_STORE_URL: "http://audit-store.llm-poc.svc.cluster.local:9200"
#     LOG_LEVEL: "DEBUG"
#     INFERENCE_TIMEOUT_SECONDS: "60"

helm install router ./llm-platform/charts/router \
  --namespace llm-poc \
  --create-namespace \
  -f my-router-values.yaml
```

---

## Lint and Template Validation

```bash
# Lint the chart
helm lint ./llm-platform/charts/router

# Render all templates without installing (useful for CI diff)
helm template router ./llm-platform/charts/router \
  --namespace llm-poc \
  --set image.tag=test \
  --set env.AUDIT_STORE_URL=http://audit-store:9200
```

---

## Prometheus Metrics

The router exposes five metrics on port `9090` at `GET /metrics`:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_router_requests_total` | Counter | `outcome`, `task_type`, `routing_mode` | Total routing pipeline invocations; `outcome` ∈ `{cache_hit, inference_success, fallback_success, error}` |
| `llm_router_latency_seconds` | Histogram | `task_type`, `routing_mode` | End-to-end pipeline wall-clock latency; buckets: `0.05–120 s` |
| `llm_router_cache_hits_total` | Counter | `task_type`, `model` | Cache HITs that bypassed inference |
| `llm_router_fallbacks_total` | Counter | `task_type`, `reason` | Fallback advances; `reason` ∈ `{health_check_failed, inference_error}` |
| `llm_router_errors_total` | Counter | `error_code` | Pipeline errors; `error_code` ∈ `{governance_check_failed, all_backends_exhausted, invalid_pinned_model, internal_error}` |

---

## POC Constraints

The following production features are intentionally disabled for POC and deferred to Phase 2:

- `autoscaling.enabled: false` — HPA template (`hpa.yaml`) exists but is inactive.
- `vault.enabled: false` — `AUDIT_STORE_URL` and other config are injected via plain `env` values; no Vault Agent sidecar.
- `networkPolicy.enabled: false` — `NetworkPolicy` template (`networkpolicy.yaml`) exists but is inactive; all pods in the namespace communicate freely.
- No Istio `VirtualService` / `DestinationRule` — mTLS service mesh is deferred to Phase 2.
- No OpenTelemetry tracing — `observability.tracing.enabled: false`; the OTel endpoint value is present for Phase 2 activation.
- `replicaCount: 1` — single replica; HA requires Phase 2 HPA enablement.
