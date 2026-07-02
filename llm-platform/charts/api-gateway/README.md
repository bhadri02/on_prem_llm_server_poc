# api-gateway

**Layer 1 — API Gateway** for the Enterprise On-Prem LLM Platform (POC).

The API Gateway is the single ingress point for all LLM traffic from enterprise consumer applications. It accepts OpenAI-compatible HTTP requests, authenticates callers via a static API key, applies in-memory sliding-window rate limiting, normalizes payloads into the Internal Message Format (IMF), and forwards them to the Security & Governance Layer. Responses from downstream are serialized back to OpenAI-compatible JSON before being returned to the caller.

This chart targets the **POC phase**. Production concerns (TLS, Vault, HPA, mTLS, OIDC) are explicitly deferred to Phase 2.

---

## Middleware Execution Order

Middleware is registered in reverse order in FastAPI/Starlette. The chart deploys a single container with the following execution order:

```
Prometheus → Logging → Auth → RateLimit → Router
```

---

## Prerequisites

- Kubernetes **1.24+**
- Helm **3.x**
- NGINX ingress controller installed and managing the `nginx` IngressClass
- Prometheus Operator (for `ServiceMonitor` CRD) — required only if `observability.metrics.enabled: true`

---

## ⚠️ Security Warnings

> **Read before deploying.**

### image.tag MUST be set

`image.tag` defaults to an **empty string**. Deploying with an empty tag is **invalid** — it will either cause the deployment to fail or pull an unintended (potentially stale or non-existent) image from the registry.

You **must** override `image.tag` at every deploy:

```
--set image.tag=1.0.0
```

### GATEWAY_API_KEY MUST be replaced

`env.GATEWAY_API_KEY` defaults to `poc-secret-key`. This value is **insecure** and will expose the gateway to unauthorized access if used outside a local development environment.

You **must** replace this value before any deployment outside a local dev cluster:

```
--set env.GATEWAY_API_KEY=<your-secret-key>
```

---

## Quick Start

```bash
helm install api-gateway ./llm-platform/charts/api-gateway \
  --set image.tag=1.0.0 \
  --set env.GATEWAY_API_KEY=<your-secret-key> \
  --set env.DOWNSTREAM_SECURITY_URL=http://security-layer:8081
```

To upgrade an existing release:

```bash
helm upgrade api-gateway ./llm-platform/charts/api-gateway \
  --set image.tag=1.1.0 \
  --set env.GATEWAY_API_KEY=<your-secret-key>
```

---

## Configuration Reference

| Parameter | Description | Default |
|---|---|---|
| `replicaCount` | Number of pod replicas | `1` |
| `image.repository` | Container image repository | `registry.local/api-gateway` |
| `image.tag` | Image tag — **must be overridden at deploy time** | `""` (empty — invalid) |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `service.type` | Kubernetes Service type | `ClusterIP` |
| `service.port` | Service port | `8080` |
| `ingress.enabled` | Enable Ingress resource | `true` |
| `ingress.className` | IngressClass name | `nginx` |
| `ingress.hosts` | Ingress host/path rules | `llm-poc.local` with `/v1` and `/health` |
| `env.GATEWAY_API_KEY` | Static API key for authentication — **replace before non-dev use** | `poc-secret-key` |
| `env.DOWNSTREAM_SECURITY_URL` | Base URL of the Security & Governance Layer | `http://security-layer:8081` |
| `env.LOG_LEVEL` | Minimum log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `env.PORT` | HTTP port the application listens on | `8080` |
| `env.METRICS_PORT` | Prometheus metrics port | `9090` |
| `env.DOWNSTREAM_TIMEOUT` | Timeout (seconds) for downstream HTTP calls | `10.0` |
| `env.RATE_LIMIT_REQUESTS` | Maximum requests per window per API key | `60` |
| `env.RATE_LIMIT_WINDOW_SECONDS` | Sliding window size in seconds | `60` |
| `resources.requests.cpu` | CPU request | `100m` |
| `resources.requests.memory` | Memory request | `256Mi` |
| `resources.limits.cpu` | CPU limit | `500m` |
| `resources.limits.memory` | Memory limit | `512Mi` |
| `autoscaling.enabled` | Enable HorizontalPodAutoscaler | `false` (POC) |
| `vault.enabled` | Enable HashiCorp Vault agent sidecar | `false` (POC) |
| `observability.metrics.enabled` | Enable Prometheus ServiceMonitor | `true` |
| `observability.metrics.port` | Metrics scrape port | `9090` |

---

## Network Policy

The chart deploys a `NetworkPolicy` that restricts traffic:

- **Ingress:** only from pods with label `app.kubernetes.io/name: ingress-nginx`
- **Egress:**
  - TCP port `8081` to pods with label `app.kubernetes.io/name: security-layer`
  - UDP/TCP port `53` for DNS resolution

---

## POC Non-Goals (Phase 2)

The following are explicitly out of scope for this chart revision:

- TLS/HTTPS termination
- HashiCorp Vault secret injection (`vault.enabled: false`)
- Horizontal Pod Autoscaling (`autoscaling.enabled: false`)
- mTLS between services
- OIDC/OAuth2 authentication
- Redis-backed rate limiting
