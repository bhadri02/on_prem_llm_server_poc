# security-layer Helm Chart

## Purpose

This chart deploys the **Security & Governance Layer** (Layer 2) of the Enterprise On-Premises LLM Platform. It is a FastAPI microservice that enforces all platform governance controls on every request, sitting between the API Gateway (Layer 1) and the Intelligent Router (Layer 3).

Every platform request passes through this service **twice**:
- **Pre-generation:** before the prompt reaches the inference model
- **Post-generation:** before the model response reaches the consumer

## Pipeline Overview

### Pre-generation pipeline (`POST /security/check`)

```
Injection scan → Content safety → PII masking → Policy check → Pre-audit (async) → Forward to Router
```

1. **Injection scan** — scans `request.messages` against patterns in the mounted `injection_patterns.yaml` using case-insensitive regex. Blocks with HTTP 400 (`injection_detected`) on match.
2. **Content safety** — checks messages against a keyword blocklist. Blocks with HTTP 400 (`content_safety_violation`) on match.
3. **PII masking** — uses Microsoft Presidio to detect and replace `EMAIL_ADDRESS`, `PHONE_NUMBER`, and `PERSON` entities in messages with `[REDACTED_<ENTITY_TYPE>]` tokens.
4. **Policy check** — verifies `user.roles` contains at least one of `developer`, `analyst`, `admin`. Blocks with HTTP 403 (`policy_denied`) otherwise.
5. **Pre-audit** — fires a background HTTP POST to the Audit Store (fire-and-forget, 2-second timeout).
6. **Forward** — POSTs the enriched IMF to the Intelligent Router and relays its response to the caller.

### Post-generation pipeline (`POST /security/post-check`)

```
PII masking on response.content → Post-audit (async)
```

1. **PII masking** — masks any PII that leaked into the model response.
2. **Post-audit** — fires a background HTTP POST to the Audit Store.

## Port Layout

| Port | Purpose |
|------|---------|
| `8081` | Application API (`/security/check`, `/security/post-check`, `/health`) |
| `9090` | Prometheus metrics (`/metrics`) |

## Required Secrets

`AUDIT_STORE_URL` and `AUDIT_API_KEY` are **not** included in `values.yaml` and must be provided at deploy time via a Kubernetes Secret named **`security-layer-secrets`**.

Create the secret before installing the chart:

```bash
kubectl create secret generic security-layer-secrets \
  --namespace <your-namespace> \
  --from-literal=AUDIT_STORE_URL=http://audit-store:9200 \
  --from-literal=AUDIT_API_KEY=<your-api-key>
```

## Values Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `replicaCount` | int | `1` | Number of pod replicas (POC: single replica) |
| `image.repository` | string | `registry.local/security-layer` | Container image repository |
| `image.tag` | string | `""` | Image tag; defaults to `appVersion` when empty |
| `image.pullPolicy` | string | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | string | `ClusterIP` | Kubernetes Service type |
| `service.port` | int | `8081` | Service port for the application API |
| `env.LOG_LEVEL` | string | `"INFO"` | Minimum log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `env.DOWNSTREAM_ROUTER_URL` | string | `"http://router:8082"` | Base URL of the Intelligent Router |
| `env.PII_ENABLED` | string | `"true"` | Enable PII detection and masking (`"true"` or `"false"`) |
| `env.INJECTION_PATTERNS_PATH` | string | `"/config/injection_patterns.yaml"` | Path to the injection patterns file inside the container |
| `resources.requests.cpu` | string | `"200m"` | CPU request |
| `resources.requests.memory` | string | `"512Mi"` | Memory request |
| `resources.limits.cpu` | string | `"1"` | CPU limit |
| `resources.limits.memory` | string | `"1Gi"` | Memory limit |
| `observability.metrics.enabled` | bool | `true` | Create a Prometheus `ServiceMonitor` resource |
| `observability.metrics.port` | int | `9090` | Port on which Prometheus metrics are exposed |
| `observability.tracing.enabled` | bool | `false` | Enable OpenTelemetry tracing (deferred to Phase 2) |
| `observability.tracing.endpoint` | string | `"http://otel-collector:4317"` | OTel collector gRPC endpoint |
| `autoscaling.enabled` | bool | `false` | Enable Horizontal Pod Autoscaler (disabled for POC) |
| `autoscaling.minReplicas` | int | `2` | HPA minimum replica count |
| `autoscaling.maxReplicas` | int | `10` | HPA maximum replica count |
| `autoscaling.targetCPUUtilizationPercentage` | int | `70` | HPA CPU utilisation target |
| `vault.enabled` | bool | `false` | Enable HashiCorp Vault Agent sidecar (disabled for POC) |
| `vault.role` | string | `"security-layer-role"` | Vault role for the security layer |
| `vault.secretPath` | string | `"secret/llm-platform/security-layer"` | Vault KV path for layer secrets |

## ConfigMap — Injection Patterns

The chart creates a ConfigMap named `<release-name>-security-layer-patterns` containing the seed injection detection patterns in `injection_patterns.yaml`. This ConfigMap is mounted **read-only** at `/config/injection_patterns.yaml` inside the container.

The default seed patterns cover common prompt injection and jailbreak phrases, including both plain keyword strings and regex patterns (e.g., `\\{\\{.*\\}\\}` for template injection and `<\\?.*\\?>` for server-side template injection markers).

To supply custom patterns, override the ConfigMap after install or provide a custom `configmap.yaml` overlay.

## Example Installation

```bash
# Create the required secret first
kubectl create secret generic security-layer-secrets \
  --namespace llm-platform \
  --from-literal=AUDIT_STORE_URL=http://audit-store.llm-platform.svc.cluster.local:9200 \
  --from-literal=AUDIT_API_KEY=changeme-poc-key

# Install the chart
helm install security-layer ./llm-platform/charts/security-layer \
  --namespace llm-platform \
  --create-namespace \
  --set image.tag=0.1.0 \
  --set env.DOWNSTREAM_ROUTER_URL=http://router.llm-platform.svc.cluster.local:8082
```

To override the audit store URL and API key directly via `--set` (not recommended for production — use the Secret approach above):

```bash
helm install security-layer ./llm-platform/charts/security-layer \
  --namespace llm-platform \
  --set image.tag=0.1.0 \
  --set env.DOWNSTREAM_ROUTER_URL=http://router:8082
```

> **Note:** `AUDIT_STORE_URL` and `AUDIT_API_KEY` are always read from the `security-layer-secrets` Kubernetes Secret, never from `values.yaml`, to prevent credentials from being committed to source control.
