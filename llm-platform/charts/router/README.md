# router

Intelligent Router sub-chart for the Enterprise On-Premises LLM Platform.

## Purpose

The router selects the appropriate backend for each incoming request based on model selection and routing policy. It acts as the dispatch layer between the security/governance layer and the execution backends:

- **Cache lookup** — forwards to `cache:8086` for semantic/exact match lookup; returns early on a cache hit
- **Inference** — forwards to `inference-ollama:11434` (via adapter on `8087`) for LLM inference
- **Agent framework** — forwards to `agent-framework:8083` for agentic/multi-step tasks

## Service Details

| Property | Value |
|---|---|
| Port | `8082` |
| Metrics port | `9090` |
| Health endpoint | `GET /health` |
| Metrics endpoint | `GET /metrics` |
| Cluster URL | `http://router:8082` |

## Docker Build

```bash
docker build -t registry.local/router:dev ./router
docker push registry.local/router:dev
```

## Installation

```bash
helm install router ./charts/router \
  --namespace llm-poc \
  --set image.tag=dev
```

## Values Reference

| Key | Default | Description |
|---|---|---|
| `replicaCount` | `1` | Number of pod replicas |
| `image.repository` | `registry.local/router` | Container image repository |
| `image.tag` | `""` | Image tag; empty string falls back to `latest` at render time |
| `image.pullPolicy` | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `8082` | HTTP service port |
| `metricsPort` | `9090` | Prometheus metrics scrape port |
| `resources.requests.cpu` | `100m` | CPU request |
| `resources.requests.memory` | `256Mi` | Memory request |
| `resources.limits.cpu` | `1` | CPU limit |
| `resources.limits.memory` | `1Gi` | Memory limit |
| `autoscaling.enabled` | `false` | Enable HPA (Phase 2) |
| `vault.enabled` | `false` | Enable Vault secret injection (Phase 2) |
| `secretRef.name` | `llm-poc-secrets` | Name of the Kubernetes Secret injected via `envFrom` |
| `serviceAccount.name` | `llm-platform` | ServiceAccount name for the pod |
| `livenessProbe.httpGet.path` | `/health` | Liveness probe HTTP path |
| `livenessProbe.httpGet.port` | `8082` | Liveness probe port |
| `livenessProbe.initialDelaySeconds` | `15` | Seconds before first liveness check |
| `livenessProbe.periodSeconds` | `15` | Liveness check interval |
| `livenessProbe.timeoutSeconds` | `5` | Liveness check timeout |
| `livenessProbe.failureThreshold` | `3` | Consecutive failures before restart |
| `livenessProbe.successThreshold` | `1` | Successes required to mark live |
| `readinessProbe.httpGet.path` | `/health` | Readiness probe HTTP path |
| `readinessProbe.httpGet.port` | `8082` | Readiness probe port |
| `readinessProbe.initialDelaySeconds` | `15` | Seconds before first readiness check |
| `readinessProbe.periodSeconds` | `15` | Readiness check interval |
| `readinessProbe.timeoutSeconds` | `5` | Readiness check timeout |
| `readinessProbe.failureThreshold` | `3` | Consecutive failures before marking not-ready |
| `readinessProbe.successThreshold` | `1` | Successes required to mark ready |
| `env` | `{}` | Additional environment variables as key/value map |
| `persistence.enabled` | `false` | Mount a PersistentVolumeClaim (router is stateless) |
| `persistence.size` | `""` | PVC size (e.g. `1Gi`) |
| `persistence.storageClass` | `""` | StorageClass; empty uses cluster default |
| `persistence.mountPath` | `/data` | Container mount path for PVC |
| `ingress.enabled` | `false` | Expose router via NGINX Ingress (not needed for internal service) |
| `ingress.host` | `""` | Ingress hostname |
| `ingress.servicePort` | `8082` | Backend service port for Ingress rule |

## Architecture

```
security-layer:8081
        │
        ▼
   router:8082
   ┌─────────────────────┐
   │  routing policy     │
   └──┬──────────┬───────┘
      │          │
      ▼          ▼
cache:8086   inference-ollama:8087
                  │
                  ▼
         agent-framework:8083
```

## Observability

- Metrics exposed at `:9090/metrics` — scraped by Prometheus via the bundled `ServiceMonitor`
- ServiceMonitor targets the `metrics` named port at a 30-second interval
- Health endpoint at `:8082/health` used by liveness and readiness probes

## Network Policy

The bundled `NetworkPolicy` restricts ingress and egress to pods within the same namespace (`llm-poc`), plus unrestricted DNS on UDP/TCP port 53. Istio mTLS enforcement is deferred to Phase 2.
