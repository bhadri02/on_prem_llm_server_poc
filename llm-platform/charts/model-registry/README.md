# model-registry Helm Chart

A lightweight model metadata registry for the Enterprise On-Prem LLM Platform POC. The registry stores model descriptors in a JSON file on a PersistentVolume and exposes a REST API so the Intelligent Router and other platform services can discover model capabilities, endpoints, and operational status without hardcoding that information in any individual service.

The service is a single FastAPI pod backed by a `PersistentVolumeClaim`. It runs on port `5000` as a `ClusterIP` service and is only reachable from within the `llm-platform` namespace (enforced by the `NetworkPolicy`).

---

## Prerequisites

Before deploying this chart, create the required Kubernetes secret that holds the registry API key:

```bash
kubectl create secret generic model-registry-secret \
  --from-literal=registry-api-key=<your-key> \
  -n llm-platform
```

Replace `<your-key>` with the pre-shared API key that mutating callers (e.g. platform operators) will supply via the `X-API-Key` header.

---

## Installing the Chart

```bash
helm upgrade --install model-registry ./llm-platform/charts/model-registry \
  --namespace llm-platform \
  --create-namespace \
  --set image.tag=<sha>
```

---

## Values Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `replicaCount` | int | `1` | Number of pod replicas. Must stay `1` for the POC (single-writer PVC). |
| `image.repository` | string | `registry.local/model-registry` | Container image repository. |
| `image.tag` | string | `""` | Image tag. Defaults to `appVersion` when empty. Set via CI `--set image.tag=<sha>`. |
| `image.pullPolicy` | string | `IfNotPresent` | Kubernetes image pull policy. |
| `service.type` | string | `ClusterIP` | Kubernetes Service type. Internal-only; do not change for POC. |
| `service.port` | int | `5000` | Service port exposed inside the cluster. |
| `env.LOG_LEVEL` | string | `"INFO"` | Minimum log level for the application (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `env.STORAGE_PATH` | string | `"/data/models.json"` | Absolute path inside the container where `models.json` is stored. |
| `apiKeySecret.name` | string | `model-registry-secret` | Name of the Kubernetes Secret containing the registry API key. |
| `apiKeySecret.key` | string | `registry-api-key` | Key within the Secret whose value is injected as `REGISTRY_API_KEY`. |
| `persistence.enabled` | bool | `true` | Enables PVC mount. Always `true`; ephemeral operation is not supported. |
| `persistence.size` | string | `1Gi` | Requested PVC storage size. |
| `persistence.accessMode` | string | `ReadWriteOnce` | PVC access mode. |
| `persistence.storageClass` | string | `""` | StorageClass name. Empty string uses the cluster default. |
| `resources.requests.cpu` | string | `"100m"` | CPU request for the container. |
| `resources.requests.memory` | string | `"128Mi"` | Memory request for the container. |
| `resources.limits.cpu` | string | `"300m"` | CPU limit for the container. |
| `resources.limits.memory` | string | `"256Mi"` | Memory limit for the container. |
| `autoscaling.enabled` | bool | `false` | HPA disabled for POC. Must remain `false` (single-writer PVC constraint). |
| `livenessProbe.httpGet.path` | string | `/health` | Liveness probe HTTP path. |
| `livenessProbe.httpGet.port` | int | `5000` | Liveness probe port. |
| `livenessProbe.initialDelaySeconds` | int | `10` | Seconds before the first liveness check. |
| `livenessProbe.periodSeconds` | int | `15` | Interval between liveness checks. |
| `livenessProbe.timeoutSeconds` | int | `2` | Probe timeout in seconds. |
| `livenessProbe.failureThreshold` | int | `3` | Consecutive failures before the pod is restarted. |
| `readinessProbe.httpGet.path` | string | `/health` | Readiness probe HTTP path. |
| `readinessProbe.httpGet.port` | int | `5000` | Readiness probe port. |
| `readinessProbe.initialDelaySeconds` | int | `10` | Seconds before the first readiness check. |
| `readinessProbe.periodSeconds` | int | `15` | Interval between readiness checks. |
| `readinessProbe.timeoutSeconds` | int | `2` | Probe timeout in seconds. |
| `readinessProbe.failureThreshold` | int | `3` | Consecutive failures before the pod is marked not-ready. |
| `vault.enabled` | bool | `false` | Vault Agent sidecar injection. Deferred to Phase 2. |

---

## API Endpoints

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| `GET` | `/models` | No | List all registered models. |
| `GET` | `/models/{name}` | No | Retrieve a model by exact name. |
| `POST` | `/models` | Yes (`X-API-Key`) | Register a new model. |
| `PATCH` | `/models/{name}/status` | Yes (`X-API-Key`) | Update a model's status. |
| `GET` | `/models/by-task/{task_type}` | No | List active models for a given task type. |
| `GET` | `/health` | No | Liveness / readiness health check. |

---

## Seed Data

The chart does not auto-load seed data. To pre-populate the registry on first deployment, copy `seed/models.json` from the repository into the PVC before starting the pod:

```bash
kubectl cp seed/models.json llm-platform/<pod-name>:/data/models.json
```

Or use an init container / Job to copy the seed file at deployment time.

---

## Notes

- `hpa.yaml` is intentionally omitted for the POC phase (`autoscaling.enabled: false`, `replicaCount: 1`).
- The `NetworkPolicy` restricts ingress to pods originating from the `llm-platform` namespace only; all other ingress is implicitly denied.
- The `ServiceMonitor` scrapes the `/health` endpoint every 30 s. A dedicated `/metrics` endpoint (Prometheus exposition format) is deferred to Phase 2.
- Vault secret injection (`vault.enabled: false`) is deferred to Phase 2.
