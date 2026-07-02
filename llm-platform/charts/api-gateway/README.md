# api-gateway

**Service purpose:** API Gateway — entry point for all external LLM platform requests. Receives inbound HTTP traffic from the NGINX Ingress Controller, authenticates callers via the shared `llm-poc-secrets` API key, and forwards requests downstream to the security layer.

## Ports

| Name    | Port | Description            |
|---------|------|------------------------|
| http    | 8080 | Primary HTTP API port  |
| metrics | 9090 | Prometheus metrics     |

## Cluster URL

```
http://api-gateway:8080
```

(Valid within the `llm-poc` namespace using Kubernetes short-form DNS.)

## Docker Build

```bash
docker build -t registry.local/api-gateway:dev .
```

## Values Reference

| Key                          | Type    | Default                        | Description                                                      |
|------------------------------|---------|--------------------------------|------------------------------------------------------------------|
| `replicaCount`               | int     | `1`                            | Number of pod replicas                                           |
| `image.repository`           | string  | `registry.local/api-gateway`   | Container image repository                                       |
| `image.tag`                  | string  | `""`                           | Image tag; empty string falls back to `latest` in the template   |
| `image.pullPolicy`           | string  | `IfNotPresent`                 | Kubernetes image pull policy                                     |
| `service.type`               | string  | `ClusterIP`                    | Kubernetes Service type                                          |
| `service.port`               | int     | `8080`                         | Service and container HTTP port                                  |
| `metricsPort`                | int     | `9090`                         | Port on which `/metrics` is exposed for Prometheus scraping      |
| `secretRef.name`             | string  | `llm-poc-secrets`              | Name of the Kubernetes Secret injected via `envFrom.secretRef`   |
| `serviceAccount.name`        | string  | `llm-platform`                 | ServiceAccount the pod runs under                                |
| `resources.requests.cpu`     | string  | `100m`                         | Minimum CPU requested from the scheduler                         |
| `resources.requests.memory`  | string  | `256Mi`                        | Minimum memory requested from the scheduler                      |
| `resources.limits.cpu`       | string  | `1`                            | Maximum CPU the container may consume                            |
| `resources.limits.memory`    | string  | `1Gi`                          | Maximum memory the container may consume                         |
| `autoscaling.enabled`        | bool    | `false`                        | Enable HorizontalPodAutoscaler (Phase 2)                         |
| `vault.enabled`              | bool    | `false`                        | Enable HashiCorp Vault Agent sidecar injection (Phase 2)         |
| `livenessProbe`              | object  | see values.yaml                | Liveness probe config (`httpGet /health:8080`, 15s delay)        |
| `readinessProbe`             | object  | see values.yaml                | Readiness probe config (`httpGet /health:8080`, 15s delay)       |
| `ingress.enabled`            | bool    | `false`                        | Create an Ingress resource for external access                   |
| `ingress.host`               | string  | `llm-poc.local`                | Hostname for the Ingress rule                                    |
| `ingress.servicePort`        | int     | `8080`                         | Backend service port referenced by the Ingress                   |
| `env`                        | map     | `{}`                           | Additional environment variables injected as key/value pairs     |
| `persistence.enabled`        | bool    | `false`                        | Mount a PersistentVolumeClaim (api-gateway is stateless)         |

## Ingress

When `ingress.enabled: true` the chart creates an Ingress resource routing
`llm-poc.local` → `api-gateway:8080` via `ingressClassName: nginx`.

Add the following entry to `/etc/hosts` (or local DNS) to reach the gateway:

```
<cluster-ip>  llm-poc.local
```

## Secret Injection

All pods mount the `llm-poc-secrets` Kubernetes Secret via `envFrom.secretRef`.
Create it before installing the chart:

```bash
kubectl create secret generic llm-poc-secrets \
  --namespace llm-poc \
  --from-literal=GATEWAY_API_KEY=poc-secret-key \
  --from-literal=REDIS_PASSWORD=""
```
