---
inclusion: manual
---

# Layer 9 — Audit Store (POC)

> Load this file when working on the Audit Store: `#09-layer-audit-store`
> **Scope:** Proof-of-Concept — demonstrate the audit trail exists and is queryable.

---

## POC Goal

Show that every request generates a tamper-evident audit trail that covers the full lifecycle (received → security check → routed → inferred → responded). For POC, store audit records in SQLite and expose a simple REST query API. No Elasticsearch, no hash chaining, no compliance reporting.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Audit Write API | FastAPI + SQLite | No Elasticsearch/ClickHouse |
| Audit Records | SQLite table | Append-only by convention (no ILM/Object Lock) |
| Hash Chaining | **Skip for POC** | Records stored as-is; no cryptographic chain |
| Audit Query API | FastAPI | Simple filter by request_id, user_id, time range |
| S3 / MinIO Archive | **Skip for POC** | No archival |
| GDPR Erasure | **Skip for POC** | Not implemented |
| Compliance Reports | **Skip for POC** | Not implemented |

---

## Audit Record Schema (POC)

A simplified subset of the master contract schema:

```json
{
  "audit_id": "uuid-v4",
  "request_id": "uuid-v4",
  "timestamp_utc": "ISO-8601",
  "user_id": "string",
  "department": "string",
  "layer": "api_gateway | security | router | cache | inference | agent",
  "event_type": "request_received | auth_pass | auth_fail | security_block | cache_hit | inference_start | inference_complete | response_sent",
  "model_used": "string | null",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "latency_ms": 0,
  "outcome": "pass | block | flag",
  "error_code": "string | null",
  "pii_actions": [],
  "policy_decisions": []
}
```

---

## SQLite Schema (POC)

```sql
CREATE TABLE audit_events (
    audit_id        TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL,
    timestamp_utc   TEXT NOT NULL,
    user_id         TEXT,
    department      TEXT,
    layer           TEXT,
    event_type      TEXT,
    model_used      TEXT,
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    outcome         TEXT,
    error_code      TEXT,
    pii_actions     TEXT,   -- JSON string
    policy_decisions TEXT   -- JSON string
);

-- Index for common queries
CREATE INDEX idx_request_id ON audit_events(request_id);
CREATE INDEX idx_user_id ON audit_events(user_id);
CREATE INDEX idx_timestamp ON audit_events(timestamp_utc);
```

> **Append-only by convention:** The Audit Store service only exposes a write (`POST`) and read (`GET`) API. No `PUT`, `PATCH`, or `DELETE` endpoints exist. This simulates immutability for POC.

---

## Write API (POC)

All layers call this endpoint asynchronously (fire-and-forget):

```
POST /audit/events
Content-Type: application/json

Body: AuditRecord JSON
```

**Batch write:**
```
POST /audit/events/batch
Body: [ AuditRecord, ... ]
```

The Audit Store service inserts directly into SQLite. No queue, no async worker for POC.

---

## Query API (POC)

```
GET  /audit/requests/{request_id}         # all events for a specific request
GET  /audit/events?user_id=&from=&to=     # events by user and time range
GET  /audit/events?event_type=&from=&to=  # events by type
GET  /audit/summary?from=&to=             # count by outcome and layer
GET  /health
```

**Example response for `/audit/requests/{request_id}`:**
```json
[
  { "layer": "api_gateway",  "event_type": "request_received", "outcome": "pass", ... },
  { "layer": "security",     "event_type": "auth_pass",        "outcome": "pass", ... },
  { "layer": "router",       "event_type": "routing_decision",  "outcome": "pass", ... },
  { "layer": "cache",        "event_type": "cache_miss",        "outcome": "miss", ... },
  { "layer": "inference",    "event_type": "inference_complete","outcome": "pass", ... },
  { "layer": "api_gateway",  "event_type": "response_sent",     "outcome": "pass", ... }
]
```

This trace across layers is the key POC deliverable — it proves the full audit trail works.

---

## How Layers Write to the Audit Store (POC)

Each layer makes a fire-and-forget HTTP POST to the Audit Store. Errors are logged but not retried.

```python
import httpx, asyncio

async def write_audit(event: dict):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post("http://audit-store:9200/audit/events", json=event)
    except Exception as e:
        logger.warning(f"Audit write failed: {e}")  # non-blocking
```

---

## Helm Chart: `llm-platform/charts/audit-store/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/audit-store
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 9200

env:
  LOG_LEVEL: "INFO"
  DB_PATH: "/data/audit.db"

persistence:
  enabled: true
  size: 5Gi

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

---

## Observability (POC)

- Structured JSON logs to stdout.
- Log each write: `audit_id`, `request_id`, `layer`, `event_type`, `latency_ms`.

Basic Prometheus metrics:
```
llm_audit_writes_total{event_type, layer}
llm_audit_write_latency_seconds{quantile}
```

---

## POC Non-Goals (Explicitly Out of Scope)

- Elasticsearch or ClickHouse backend
- Cryptographic hash chain
- S3 / MinIO Object Lock archival
- GDPR right-to-erasure
- Index Lifecycle Management (ILM)
- Compliance report generation (SOC 2, HIPAA, GDPR)
- Tamper-detection verification API
- Access control by role (auditor/compliance roles)
- Retention policy enforcement
