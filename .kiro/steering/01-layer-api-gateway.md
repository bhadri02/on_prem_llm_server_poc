---
inclusion: manual
---

# Layer 1 — API Gateway (POC)

> Load this file when working on the API Gateway layer: `#01-layer-api-gateway`
> **Scope:** Proof-of-Concept — functional correctness over production hardening.

---

## POC Goal

Demonstrate a working single ingress point that accepts OpenAI-compatible requests, validates a simple API key, applies basic rate limiting, normalizes the payload into the IMF, and forwards it downstream.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Ingress | Kubernetes Ingress (NGINX) | HTTP only (no TLS required for POC) |
| API Router | FastAPI (Python) | Single versioned handler for `/v1/chat/completions` |
| Auth Middleware | Static API Key check | Single hardcoded or env-var key; no OIDC/LDAP |
| Rate Limiter | In-memory counter (FastAPI middleware) | Per-key, fixed window; no Redis needed |
| Request Normalizer | FastAPI middleware | Parse OpenAI payload → IMF |
| Response Serializer | FastAPI middleware | IMF response → OpenAI-compatible JSON |

---

## API Endpoints (POC — Minimum)

```
POST  /v1/chat/completions    # primary endpoint
GET   /v1/models              # return static list of available models
GET   /health                 # liveness probe
```

### Auth (POC)
```
Header: X-Api-Key: <key>
```
Accept a single key from environment variable `GATEWAY_API_KEY`. Return `401` on mismatch.

### Error Responses
```json
{ "error": { "code": "401", "message": "Unauthorized" } }
{ "error": { "code": "429", "message": "Rate limit exceeded" } }
{ "error": { "code": "400", "message": "Bad request" } }
```

---

## IMF Fields This Layer Populates

```json
{
  "request_id": "uuid-v4 (generated)",
  "trace_id": "same as request_id for POC",
  "timestamp_utc": "ISO-8601",
  "user": {
    "user_id": "extracted from api key or static 'poc-user'",
    "department": "poc",
    "roles": ["developer"],
    "auth_method": "api_key"
  },
  "request": {
    "model": "from client body or null",
    "messages": [...],
    "stream": false,
    "max_tokens": 2048,
    "temperature": 0.7
  }
}
```

All other IMF blocks (`governance`, `routing`, `cache`, `response`) initialized to defaults.

---

## Rate Limiting (POC)

- In-memory sliding window using a simple dict + timestamp list.
- Limit: 60 requests/minute per API key.
- On breach: return `429`.
- No Redis dependency for POC.

---

## Streaming Support (POC)

- Support `stream: true` via FastAPI `StreamingResponse`.
- Proxy SSE chunks from the inference layer back to the client.
- Acceptable to buffer small chunks; no strict zero-latency requirement in POC.

---

## Helm Chart: `llm-platform/charts/api-gateway/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/api-gateway
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8080

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: llm-poc.local
      paths: [/v1, /health]

env:
  GATEWAY_API_KEY: "poc-secret-key"   # override via ConfigMap/Secret in cluster
  DOWNSTREAM_SECURITY_URL: "http://security-layer:8081"
  LOG_LEVEL: "INFO"

resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"

autoscaling:
  enabled: false   # no HPA for POC

vault:
  enabled: false   # secrets via env vars for POC
```

---

## Observability (POC — Minimal)

- Structured JSON logs to stdout (use Python `structlog` or basic `logging` with JSON formatter).
- Log fields: `request_id`, `timestamp`, `method`, `path`, `status_code`, `latency_ms`.
- No OTel tracing required for POC.
- Expose `/metrics` with basic Prometheus counters (optional for POC; include if easy).

---

## Audit Events (POC)

Write a simple Python dict to stdout as JSON for these events:
- `request_received`
- `auth_pass` / `auth_fail`
- `rate_limited`
- `response_sent`

No write to Audit Store required for POC — stdout log is sufficient.

---

## Integration Handoff

- **Downstream:** HTTP POST to Security Layer at `http://security-layer:8081/process`
- **Protocol:** Plain HTTP JSON (no gRPC, no mTLS for POC)
- **Body:** Serialized IMF JSON
- **On error from downstream:** Return `502 Bad Gateway`

---

## POC Non-Goals (Explicitly Out of Scope)

- TLS/HTTPS termination
- OIDC/OAuth2/LDAP authentication
- Redis-backed rate limiting
- WAF rules
- gRPC transport
- mTLS between services
- HPA / multiple replicas
