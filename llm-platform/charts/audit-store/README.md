# audit-store Helm Chart

Deploys the **Audit Store** — the append-only audit trail service for the Enterprise On-Premises LLM Platform (POC). The service persists every significant platform event to a SQLite database and exposes a REST query API for operators.

## Purpose

The Audit Store receives fire-and-forget HTTP POST requests from all platform layers (API Gateway, Security, Router, Cache, Inference, Agent Framework, Governance). It stores them in an append-only SQLite database and provides query endpoints so operators can trace the complete lifecycle of any individual request across all layers.

## Port Layout

| Port | Name    | Description                                      |
|------|---------|--------------------------------------------------|
| 9200 | `http`  | Application API — write (`POST /audit/events`, `POST /audit/events/batch`), query (`GET /audit/requests/{id}`, `GET /audit/events`, `GET /audit/summary`), and health (`GET /health`) |
| 9090 | `metrics` | Prometheus metrics scrape endpoint (`GET /metrics`) |

## Required Secret

`AUDIT_API_KEY` **must never be committed to `values.yaml`**. It is read from a Kubernetes Secret named `audit-store-secrets` at runtime.

Create the secret before deploying:

```bash
kubectl create secret generic audit-store-secrets \
  --namespace <your-namespace> \
  --from-literal=AUDIT_API_KEY=<your-api-key>
```

The deployment mounts this secret via `secretKeyRef`:

```yaml
- name: AUDIT_API_KEY
  valueFrom:
    secretKeyRef:
      name: audit-store-secrets
      key: AUDIT_API_KEY
```

## Configurable Values

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `replicaCount` | int | `1` | Number of pod replicas (single-instance POC) |
| `image.repository` | string | `registry.local/audit-store` | Container image repository |
| `image.tag` | string | `""` | Image tag; defaults to `appVersion` when empty |
| `image.pullPolicy` | string | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | string | `ClusterIP` | Kubernetes service type |
| `service.port` | int | `9200` | Service port for the application API |
| `env.DB_PATH` | string | `"/data/audit.db"` | Path inside the container where SQLite database file is stored |
| `env.LOG_LEVEL` | string | `"INFO"` | Minimum log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `persistence.enabled` | bool | `true` | Whether to create and mount a PersistentVolumeClaim for the SQLite database |
| `persistence.size` | string | `5Gi` | PVC storage request size |
| `persistence.storageClass` | string | `""` | StorageClass name; empty string uses the cluster default |
| `persistence.accessMode` | string | `ReadWriteOnce` | PVC access mode |
| `resources.requests.cpu` | string | `"100m"` | CPU request |
| `resources.requests.memory` | string | `"256Mi"` | Memory request |
| `resources.limits.cpu` | string | `"500m"` | CPU limit |
| `resources.limits.memory` | string | `"512Mi"` | Memory limit |
| `observability.metrics.enabled` | bool | `true` | Enable Prometheus metrics exposure |
| `observability.metrics.port` | int | `9090` | Port on which Prometheus metrics are served |
| `autoscaling.enabled` | bool | `false` | Enable HPA (deferred to Phase 2 — stateful single-instance POC) |
| `vault.enabled` | bool | `false` | Enable HashiCorp Vault Agent sidecar (deferred to Phase 2) |

## Example `helm install` Command

Supply `AUDIT_API_KEY` at deploy time. **Never pass it via `--set` in production** (it appears in shell history); use the `kubectl create secret` approach above instead. The `--set` form is shown here for local/dev convenience only:

```bash
helm install audit-store ./llm-platform/charts/audit-store \
  --namespace llm-audit \
  --create-namespace \
  --set image.tag=0.1.0
```

With the required secret already created (recommended):

```bash
# 1. Create the secret (one-time)
kubectl create secret generic audit-store-secrets \
  --namespace llm-audit \
  --from-literal=AUDIT_API_KEY=<your-secure-api-key>

# 2. Install the chart
helm install audit-store ./llm-platform/charts/audit-store \
  --namespace llm-audit \
  --create-namespace \
  --set image.tag=0.1.0
```

For a persistence-disabled test deployment (data lost on pod restart):

```bash
helm install audit-store ./llm-platform/charts/audit-store \
  --namespace llm-audit \
  --create-namespace \
  --set image.tag=0.1.0 \
  --set persistence.enabled=false
```

## Network Policy

The chart deploys a `NetworkPolicy` that restricts ingress to:

- **Port 9200**: allowed from namespaces `llm-api-gateway`, `llm-security`, `llm-router`, `llm-cache`, `llm-inference`, `llm-agent-framework`, `llm-governance`
- **Port 9090**: allowed from namespace `llm-observability` only
- All other ingress is denied by default

## POC Constraints

The following production features are intentionally omitted from this chart and deferred to Phase 2:

- No `hpa.yaml` — the Audit Store is a stateful single-instance service; HPA requires a StatefulSet and shared storage
- `vault.enabled: false` — secrets are provided via Kubernetes Secrets rather than Vault Agent sidecar
- `replicaCount: 1` — HA requires a StatefulSet or external DB backend
- No Istio `VirtualService` / `DestinationRule` — mTLS service mesh is deferred to Phase 2
