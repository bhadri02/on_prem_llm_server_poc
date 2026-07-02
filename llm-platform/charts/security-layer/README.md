# security-layer

Security & Governance layer — handles API key authentication, injection detection, content safety checks, and PII detection.

## Overview

This sub-chart deploys the `security-layer` service, which sits between the API Gateway and the Intelligent Router. Every request passes through this layer for:

- **API key authentication** — validates `X-Api-Key` against the shared secret
- **Injection detection** — rule-based prompt injection and jailbreak detection (spaCy-powered)
- **Content safety checks** — scans request and response content for policy violations
- **PII detection** — identifies and masks personally identifiable information using spaCy NER models

## Ports

| Port | Name    | Description                        |
|------|---------|------------------------------------|
| 8081 | http    | Main service HTTP API              |
| 9090 | metrics | Prometheus `/metrics` scrape port  |

## Cluster URL

```
http://security-layer:8081
```

## Probe Note

Both `livenessProbe` and `readinessProbe` use `initialDelaySeconds: 60` to accommodate the spaCy NLP model load time at container startup (Requirement 11.4). Reducing this value below 60 seconds on resource-constrained nodes will cause false pod restarts.

## Values Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `replicaCount` | int | `1` | Number of pod replicas |
| `image.repository` | string | `registry.local/security-layer` | Container image repository |
| `image.tag` | string | `""` | Image tag — empty falls back to `latest` |
| `image.pullPolicy` | string | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | string | `ClusterIP` | Kubernetes service type |
| `service.port` | int | `8081` | HTTP service port |
| `metricsPort` | int | `9090` | Prometheus metrics port |
| `resources.requests.cpu` | string | `"100m"` | CPU request |
| `resources.requests.memory` | string | `"256Mi"` | Memory request |
| `resources.limits.cpu` | string | `"1"` | CPU limit |
| `resources.limits.memory` | string | `"1Gi"` | Memory limit |
| `autoscaling.enabled` | bool | `false` | Enable HPA (Phase 2) |
| `vault.enabled` | bool | `false` | Enable Vault secret injection (Phase 2) |
| `secretRef.name` | string | `"llm-poc-secrets"` | Kubernetes Secret name for `envFrom.secretRef` |
| `serviceAccount.name` | string | `"llm-platform"` | ServiceAccount for pod identity |
| `livenessProbe.httpGet.path` | string | `/health` | Liveness probe HTTP path |
| `livenessProbe.httpGet.port` | int | `8081` | Liveness probe port |
| `livenessProbe.initialDelaySeconds` | int | `60` | Delay before first liveness check (spaCy load) |
| `livenessProbe.periodSeconds` | int | `15` | Interval between liveness checks |
| `livenessProbe.timeoutSeconds` | int | `5` | Liveness probe timeout |
| `livenessProbe.failureThreshold` | int | `3` | Consecutive failures before restart |
| `livenessProbe.successThreshold` | int | `1` | Successes required to mark healthy |
| `readinessProbe.httpGet.path` | string | `/health` | Readiness probe HTTP path |
| `readinessProbe.httpGet.port` | int | `8081` | Readiness probe port |
| `readinessProbe.initialDelaySeconds` | int | `60` | Delay before first readiness check (spaCy load) |
| `readinessProbe.periodSeconds` | int | `15` | Interval between readiness checks |
| `readinessProbe.timeoutSeconds` | int | `5` | Readiness probe timeout |
| `readinessProbe.failureThreshold` | int | `3` | Consecutive failures before removing from LB |
| `readinessProbe.successThreshold` | int | `1` | Successes required to mark ready |
| `env` | map | `{}` | Additional environment variables as key/value pairs |
| `persistence.enabled` | bool | `false` | Enable PVC for data storage |
| `persistence.size` | string | `""` | PVC size (e.g. `"5Gi"`) |
| `persistence.storageClass` | string | `""` | StorageClass — empty uses cluster default |
| `persistence.mountPath` | string | `"/data"` | Container mount path for the data volume |
| `ingress.enabled` | bool | `false` | Enable Ingress (not applicable — internal service only) |
| `ingress.host` | string | `""` | Ingress hostname |
| `ingress.servicePort` | int | `8081` | Backend service port for Ingress |

## Notes

- `security-layer` is an internal service — no `ingress.yaml` is provided. It is not externally exposed.
- The NetworkPolicy allows unrestricted ingress/egress within the `llm-poc` namespace and permits DNS resolution on port 53.
- The ServiceMonitor targets port `metrics` (9090) for Prometheus scraping at 30-second intervals.
