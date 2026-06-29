---
inclusion: manual
---

# Layer 10 — Platform Portals (POC)

> Load this file when working on the Admin or Developer Portal: `#10-layer-platform-portals`
> **Scope:** Proof-of-Concept — a minimal UI to demonstrate platform visibility and usability.

---

## POC Goal

Provide a simple web UI that lets a developer or admin:
1. Send a test request (playground) and see the response.
2. View recent audit events.
3. See which models are registered and their status.

For POC, build a single combined portal (admin + developer merged into one lightweight app). No production RBAC, no Keycloak integration.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Portal UI | React (Vite) or simple HTML/JS | Single-page app; no auth/login screen |
| Portal API | FastAPI | Thin API that proxies to other layer APIs |
| Playground | React chat component | POST to API Gateway `/v1/chat/completions` |
| Audit Viewer | React table | GET from Audit Store query API |
| Model Viewer | React table | GET from Model Registry API |
| Grafana Embed | `<iframe>` | Embed existing Grafana POC dashboard |
| Human Review UI | **Skip for POC** | Not implemented |
| Compliance Reports | **Skip for POC** | Not implemented |
| User Management | **Skip for POC** | Not implemented |

---

## Portal API Endpoints (POC)

```
GET   /portal/health

# Playground — proxy to API Gateway
POST  /portal/playground/chat
      → proxies to http://api-gateway:8080/v1/chat/completions

# Audit — proxy to Audit Store
GET   /portal/audit/requests/{request_id}
GET   /portal/audit/events?from=&to=&limit=50

# Models — proxy to Model Registry
GET   /portal/models
PATCH /portal/models/{name}/status   # activate / retire a model

# Observability — direct Prometheus queries
GET   /portal/metrics/summary        # request rate, error rate, cache hit rate
```

---

## Playground UI (POC)

Simple chat interface:
- Model selector dropdown (populated from `/portal/models`)
- Message input field + Send button
- Response display area (streaming optional)
- Request ID display for audit correlation
- "View Audit Trail" button → opens audit viewer for that request_id

```
[ Model: llama3:8b ▼ ]   [ Temperature: 0.7 ]

[ You: Summarize the key benefits of Kubernetes ]

[ Assistant: Kubernetes provides... ]

Request ID: 550e8400-e29b-41d4-a716-446655440000
[ View Audit Trail ]
```

---

## Audit Viewer UI (POC)

Table showing recent audit events with columns:
- `timestamp_utc`
- `request_id` (clickable — shows all events for that request)
- `layer`
- `event_type`
- `user_id`
- `outcome`
- `latency_ms`

Filter controls: time range, layer, outcome (pass/block).

---

## Model Viewer UI (POC)

Table showing registered models:
- `name`
- `version`
- `backend`
- `tasks` (comma-separated)
- `status` (active/retired/staging)
- Action button: [Activate] [Retire]

---

## Grafana Embed (POC)

```html
<!-- Embed POC Overview dashboard -->
<iframe
  src="http://grafana:3000/d/poc-overview/llm-platform-poc?orgId=1&kiosk"
  width="100%"
  height="600px"
  frameborder="0">
</iframe>
```

---

## Helm Chart: `llm-platform/charts/admin-portal/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/admin-portal
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8084

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: llm-portal.local
      paths: [/]

env:
  LOG_LEVEL: "INFO"
  API_GATEWAY_URL: "http://api-gateway:8080"
  AUDIT_STORE_URL: "http://audit-store:9200"
  MODEL_REGISTRY_URL: "http://model-registry:5000"
  GRAFANA_URL: "http://grafana:3000"
  GATEWAY_API_KEY: "poc-secret-key"

resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"

autoscaling:
  enabled: false

vault:
  enabled: false
```

> **Note:** Developer Portal and Admin Portal are merged into this single service for POC. Separate them in production.

---

## Observability (POC)

- JSON logs to stdout.
- Log each API call: `endpoint`, `status_code`, `latency_ms`.

---

## POC Non-Goals (Explicitly Out of Scope)

- OIDC / Keycloak authentication (portal is open for POC)
- Role-based access (admin vs developer vs auditor)
- Human approval review queue UI
- API key management UI
- Compliance report generation
- SDK download page
- Prompt template library
- Evaluation framework UI
- Separate Admin Portal and Developer Portal deployments
