# agent-framework

Agent Framework sub-chart for the Enterprise On-Premises LLM Platform.

## Purpose

The agent-framework handles agentic multi-step tasks dispatched by the router. It orchestrates tool calls and multi-turn reasoning sequences, allowing the platform to fulfill complex requests that require planning, iterative execution, or external tool use:

- **Multi-step reasoning** — decomposes complex prompts into sub-tasks and executes them sequentially
- **Tool call orchestration** — manages calls to external tools and incorporates results back into the reasoning chain
- **Agentic task dispatch** — receives routed requests from `router:8082` and forwards completed responses upstream
- **Inference delegation** — forwards individual inference calls to `inference-ollama:8087` as needed during task execution

## Service Details

| Property | Value |
|---|---|
| Port | `8083` |
| Metrics port | `9090` |
| Health endpoint | `GET /health` |
| Metrics endpoint | `GET /metrics` |
| Cluster URL | `http://agent-framework:8083` |

## Docker Build

```bash
docker build -t registry.local/agent-framework:dev ./agent_framework
docker push registry.local/agent-framework:dev
```

## Installation

```bash
helm install agent-framework ./charts/agent-framework \
  --namespace llm-poc \
  --set image.tag=dev
```

## Values Reference

| Key | Default | Description |
|---|---|---|
| `replicaCount` | `1` | Number of pod replicas |
| `image.repository` | `registry.local/agent-framework` | Container image repository |
| `image.tag` | `""` | Image tag; empty string falls back to `latest` at render time |
| `image.pullPolicy` | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `8083` | HTTP service port |
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
| `livenessProbe.httpGet.port` | `8083` | Liveness probe port |
| `livenessProbe.initialDelaySeconds` | `15` | Seconds before first liveness check |
| `livenessProbe.periodSeconds` | `15` | Liveness check interval |
| `livenessProbe.timeoutSeconds` | `5` | Liveness check timeout |
| `livenessProbe.failureThreshold` | `3` | Consecutive failures before restart |
| `livenessProbe.successThreshold` | `1` | Successes required to mark live |
| `readinessProbe.httpGet.path` | `/health` | Readiness probe HTTP path |
| `readinessProbe.httpGet.port` | `8083` | Readiness probe port |
| `readinessProbe.initialDelaySeconds` | `15` | Seconds before first readiness check |
| `readinessProbe.periodSeconds` | `15` | Readiness check interval |
| `readinessProbe.timeoutSeconds` | `5` | Readiness check timeout |
| `readinessProbe.failureThreshold` | `3` | Consecutive failures before marking not-ready |
| `readinessProbe.successThreshold` | `1` | Successes required to mark ready |
| `env` | `{}` | Additional environment variables as key/value map |
| `persistence.enabled` | `false` | Mount a PersistentVolumeClaim (agent-framework is stateless) |
| `persistence.size` | `""` | PVC size (e.g. `1Gi`) |
| `persistence.storageClass` | `""` | StorageClass; empty uses cluster default |
| `persistence.mountPath` | `/data` | Container mount path for PVC |
| `ingress.enabled` | `false` | Expose agent-framework via NGINX Ingress (not needed for internal service) |
| `ingress.host` | `""` | Ingress hostname |
| `ingress.servicePort` | `8083` | Backend service port for Ingress rule |

## Architecture

```
router:8082
        │
        ▼
agent-framework:8083
   ┌──────────────────────────┐
   │  multi-step reasoning    │
   │  tool call orchestration │
   └──────────┬───────────────┘
              │
              ▼
    inference-ollama:8087
```

## Observability

- Metrics exposed at `:9090/metrics` — scraped by Prometheus via the bundled `ServiceMonitor`
- ServiceMonitor targets the `metrics` named port at a 30-second interval
- Health endpoint at `:8083/health` used by liveness and readiness probes

## Network Policy

The bundled `NetworkPolicy` restricts ingress and egress to pods within the same namespace (`llm-poc`), plus unrestricted DNS on UDP/TCP port 53. Istio mTLS enforcement is deferred to Phase 2.
