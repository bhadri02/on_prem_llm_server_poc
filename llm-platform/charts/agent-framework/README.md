# agent-framework Helm Chart

Packages the Agent Framework (Layer 6 — POC) as a Kubernetes Deployment. The service
runs on port 8083 (main API) and port 9090 (Prometheus metrics).

## Overview

The Agent Framework handles agentic requests routed from the Intelligent Router. It runs
a LangGraph ReAct loop with three bound tools (`calculator`, `get_current_time`,
`web_search`), routes all LLM sub-calls through the Router's `/v1/chat/completions`
endpoint, and returns a fully-populated IMF response.

## ⚠️ Mandatory Deploy-Time Overrides

### 1. Image tag

The chart defaults `image.tag` to an empty string. **You must set a real tag at deploy
time.** Using an empty tag will fall back to `appVersion` from `Chart.yaml` which may
not match any available image.

```bash
helm upgrade --install agent-framework ./charts/agent-framework \
  --set image.tag=v1.2.3
```

### 2. GATEWAY_API_KEY

The default value `poc-secret-key` is a **placeholder**. It must be replaced with the
real API key before deploying to any environment outside a local developer workstation.

```bash
helm upgrade --install agent-framework ./charts/agent-framework \
  --set image.tag=v1.2.3 \
  --set env.GATEWAY_API_KEY="your-real-secret-key"
```

For production use a proper secret management solution (HashiCorp Vault — Phase 2).

---

## Values Reference

| Key | Default | Description |
|-----|---------|-------------|
| `replicaCount` | `1` | Number of pod replicas (POC: single instance) |
| `image.repository` | `registry.local/agent-framework` | OCI image repository |
| `image.tag` | `""` | **Must be overridden at deploy time** |
| `image.pullPolicy` | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `8083` | Service port (main API) |
| `env.ROUTER_URL` | `http://router:8082` | Base URL of the Intelligent Router |
| `env.LOG_LEVEL` | `INFO` | Structured log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `env.MAX_AGENT_STEPS` | `10` | Max ReAct loop iterations (range: 1–50) |
| `env.TOOL_CATALOG_PATH` | `/config/tools/catalog.yaml` | Path to the mounted tool catalog |
| `env.GATEWAY_API_KEY` | `poc-secret-key` | **Must be overridden at deploy time** |
| `env.PORT` | `8083` | Uvicorn listen port (main app) |
| `env.METRICS_PORT` | `9090` | Uvicorn listen port (metrics app) |
| `resources.requests.cpu` | `200m` | CPU request |
| `resources.requests.memory` | `512Mi` | Memory request |
| `resources.limits.cpu` | `1` | CPU limit |
| `resources.limits.memory` | `1Gi` | Memory limit |
| `autoscaling.enabled` | `false` | HPA disabled for POC (enable in Phase 2) |
| `vault.enabled` | `false` | Vault injection disabled for POC (enable in Phase 2) |
| `observability.metrics.enabled` | `true` | Create a Prometheus `ServiceMonitor` |
| `observability.metrics.port` | `9090` | Port scraped by Prometheus |

---

## Endpoints

| Path | Port | Description |
|------|------|-------------|
| `POST /agent/run` | 8083 | Main agentic request endpoint |
| `GET /health` | 8083 | Liveness/readiness check |
| `GET /metrics` | 9090 | Prometheus text exposition |

---

## Network Policy

The chart deploys a `NetworkPolicy` that restricts traffic to:

- **Ingress**: Only pods with label `app: router` may send traffic to port 8083.
  The `monitoring` namespace may scrape port 9090.
- **Egress**: Only outbound connections to pods labelled `app: router` on TCP 8082,
  and DNS (port 53 UDP/TCP) to `kube-system`.

---

## Tool Catalog

The tool catalog (`catalog.yaml`) is bundled in a `ConfigMap` and mounted at
`/config/tools/catalog.yaml` inside the container. To modify the tool set, update
`templates/configmap.yaml` and redeploy.

---

## POC Constraints

The following production features are disabled in this chart and are deferred to Phase 2:

- `autoscaling.enabled: false` — single replica only
- `vault.enabled: false` — secrets are plain env vars
- Plain HTTP between services (no Istio mTLS)
- In-memory session store (no Redis)
- Mocked `web_search` tool (no real search API)

---

## Example Install

```bash
# Local dev
helm upgrade --install agent-framework ./llm-platform/charts/agent-framework \
  --namespace llm-platform \
  --create-namespace \
  --set image.tag=latest \
  --set env.ROUTER_URL=http://router:8082

# Any non-local environment — always override key and tag
helm upgrade --install agent-framework ./llm-platform/charts/agent-framework \
  --namespace llm-platform \
  --set image.tag=v1.0.0 \
  --set env.GATEWAY_API_KEY="<your-real-key>" \
  --set env.ROUTER_URL=http://router.llm-platform.svc.cluster.local:8082
```
