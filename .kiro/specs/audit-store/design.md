# Design Document — Audit Store

## Overview

The Audit Store is a centralized, append-only event persistence and query service for the Enterprise On-Premises LLM Platform. Its POC role is to prove that every request generates a complete, queryable audit trail covering the full lifecycle: received → security check → routed → inferred → responded.

Every other platform layer writes audit events to this service as a fire-and-forget HTTP POST, meaning the Audit Store never sits in the hot path of a user-facing request. The service is built on **FastAPI** and **SQLite**, exposes a write API on port 9200, and serves Prometheus metrics on port 9090. It emits structured JSON logs to stdout.

**POC constraints in effect:** No hash chaining, no Elasticsearch/ClickHouse, no S3/MinIO archival, no GDPR erasure, no ILM, no Vault secret management, no mTLS, no HPA — all deferred to Phase 2. Static API key authentication is used for the write endpoints.

---

## Architecture

The service is a single-process FastAPI application. It has no internal worker queues or async background workers for the POC — writes are handled synchronously within the request handler, which keeps the design simple and the failure modes obvious.

```mermaid
graph TD
    subgraph Platform Layers
        GW[API Gateway]
        SEC[Security]
        RTR[Router]
        CACHE[Cache]
        INF[Inference]
        AGT[Agent]
    end

    subgraph Audit Store — port 9200
        AM[Auth Middleware\nX-API-Key]
        WA[Write API\nPOST /audit/events\nPOST /audit/events/batch]
        QA[Query API\nGET /audit/requests/:id\nGET /audit/events\nGET /audit/summary\nGET /health]
        VL[Validation Layer\nPydantic models]
        DB[(SQLite\n/data/audit.db\nWAL mode)]
        LOG[JSON Logger\nstdout]
        MTR[Prometheus Metrics\nport 9090]
    end

    subgraph Consumers
        OPS[Operator / Dashboard]
        PROM[Prometheus Scraper]
    end

    GW -->|fire-and-forget HTTP POST| AM
    SEC -->|fire-and-forget HTTP POST| AM
    RTR -->|fire-and-forget HTTP POST| AM
    CACHE -->|fire-and-forget HTTP POST| AM
    INF -->|fire-and-forget HTTP POST| AM
    AGT -->|fire-and-forget HTTP POST| AM

    AM --> WA
    WA --> VL
    VL --> DB
    VL --> LOG
    VL --> MTR

    OPS -->|HTTP GET| QA
    QA --> DB

    PROM -->|GET /metrics| MTR
```

### Key Design Decisions

**Synchronous writes over async queuing.** For the POC, each POST handler writes directly to SQLite and returns. This eliminates operational complexity (no Kafka, no Redis queue) while still satisfying the 200 ms write budget for single records. Fire-and-forget is enforced at the *caller* side — callers do not await acknowledgment before continuing.

**Separate ASGI app for metrics.** The Prometheus `/metrics` endpoint runs on a second port (9090) using a lightweight ASGI app. This keeps the metrics endpoint unauthenticated without creating an exception in the main app's authentication middleware.

**WAL mode for SQLite.** `PRAGMA journal_mode=WAL` is applied immediately after connection so concurrent reads (query API) are never blocked by an in-progress write transaction.

**Append-only by API surface, not DB constraint.** There are no `PUT`, `PATCH`, or `DELETE` endpoints. SQLite itself does not enforce immutability, but the service API surface guarantees it for the POC.

---

## Components and Interfaces

### Module Layout

```
audit_store/
├── main.py               # FastAPI app factory, lifespan handler, router wiring
├── metrics_app.py        # Separate ASGI app serving /metrics on port 9090
├── config.py             # Settings loaded from environment variables
├── database.py           # SQLite connection, WAL setup, schema init
├── models.py             # Pydantic request/response models, enum definitions
├── auth.py               # X-API-Key middleware
├── logging_config.py     # JSON structured logger factory
├── routers/
│   ├── write.py          # POST /audit/events, POST /audit/events/batch
│   └── query.py          # GET endpoints
└── metrics.py            # Prometheus Counter and Histogram definitions
```

### `config.py` — Environment-Driven Settings

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    audit_api_key: str          # AUDIT_API_KEY — required; empty = startup failure
    db_path: str = "/data/audit.db"  # DB_PATH
    log_level: str = "INFO"     # LOG_LEVEL — defaults to INFO if missing/invalid

settings = Settings()
```

Startup failure conditions are enforced in the lifespan handler in `main.py` before the app begins accepting requests.

### `database.py` — Connection and Schema Init

```python
import sqlite3, pathlib

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_events (
            audit_id          TEXT PRIMARY KEY,
            request_id        TEXT NOT NULL,
            timestamp_utc     TEXT NOT NULL,
            user_id           TEXT,
            department        TEXT,
            layer             TEXT,
            event_type        TEXT,
            model_used        TEXT,
            prompt_tokens     INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            latency_ms        INTEGER DEFAULT 0,
            outcome           TEXT,
            error_code        TEXT,
            pii_actions       TEXT,
            policy_decisions  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_request_id ON audit_events(request_id);
        CREATE INDEX IF NOT EXISTS idx_user_id    ON audit_events(user_id);
        CREATE INDEX IF NOT EXISTS idx_timestamp  ON audit_events(timestamp_utc);
    """)
    conn.commit()
```

A single `sqlite3.Connection` is created at startup and stored as an application-level state object on the FastAPI `app.state`. This avoids connection-per-request overhead while remaining safe for the single-instance POC.

### `auth.py` — API Key Middleware

```python
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

class APIKeyMiddleware(BaseHTTPMiddleware):
    WRITE_PATHS = {"/audit/events", "/audit/events/batch"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.WRITE_PATHS and request.method == "POST":
            key = request.headers.get("X-API-Key")
            if not key:
                raise HTTPException(status_code=401,
                    detail={"error": "missing_api_key"})
            if key != request.app.state.settings.audit_api_key:
                raise HTTPException(status_code=403,
                    detail={"error": "invalid_api_key"})
        return await call_next(request)
```

GET endpoints and `/health` bypass the middleware check by path matching. The middleware is mounted **only** on the main app (port 9200), not the metrics app.

### `models.py` — Pydantic Schemas

```python
from enum import Enum
from pydantic import BaseModel, Field, field_validator
import uuid, re

class LayerEnum(str, Enum):
    api_gateway = "api_gateway"
    security    = "security"
    router      = "router"
    cache       = "cache"
    inference   = "inference"
    agent       = "agent"

class EventTypeEnum(str, Enum):
    request_received   = "request_received"
    auth_pass          = "auth_pass"
    auth_fail          = "auth_fail"
    security_block     = "security_block"
    cache_hit          = "cache_hit"
    inference_start    = "inference_start"
    inference_complete = "inference_complete"
    response_sent      = "response_sent"

class OutcomeEnum(str, Enum):
    pass_  = "pass"
    block  = "block"
    flag   = "flag"

UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE
)

class AuditEventCreate(BaseModel):
    audit_id:         str | None = None
    request_id:       str        # validated as UUID-v4
    timestamp_utc:    str | None = None
    user_id:          str | None = None
    department:       str | None = None
    layer:            LayerEnum
    event_type:       EventTypeEnum
    model_used:       str | None = None
    prompt_tokens:    int = 0
    completion_tokens:int = 0
    latency_ms:       int = 0
    outcome:          OutcomeEnum
    error_code:       str | None = None
    pii_actions:      list       = Field(default_factory=list)
    policy_decisions: list       = Field(default_factory=list)

    @field_validator("request_id")
    @classmethod
    def validate_uuid(cls, v):
        if not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v

class AuditEventResponse(AuditEventCreate):
    audit_id:      str
    timestamp_utc: str

class BatchWriteRequest(BaseModel):
    events: list[AuditEventCreate] = Field(min_length=1, max_length=500)

class BatchWriteResponse(BaseModel):
    inserted: int
    audit_ids: list[str]

class SummaryResponse(BaseModel):
    total_events: int
    by_outcome:   dict[str, int]
    by_layer:     dict[str, int]
```

### `routers/write.py` — Write Endpoints

```python
@router.post("/audit/events", status_code=201)
async def write_single(event: AuditEventCreate, request: Request) -> AuditEventResponse:
    t_start = time.monotonic()
    audit_id = event.audit_id or str(uuid.uuid4())
    timestamp_utc = event.timestamp_utc or datetime.utcnow().isoformat() + "Z"
    # serialize JSON fields, insert into DB, record metrics, emit log
    ...

@router.post("/audit/events/batch", status_code=201)
async def write_batch(body: BatchWriteRequest, request: Request) -> BatchWriteResponse:
    # assign audit_id/timestamp for records missing them
    # INSERT all inside a single transaction
    # roll back on any DB error
    # record metrics and emit logs
    ...
```

The batch endpoint wraps all inserts in a single SQLite transaction using `BEGIN IMMEDIATE` to prevent race conditions on concurrent batch writes.

### `routers/query.py` — Query Endpoints

```python
@router.get("/audit/requests/{request_id}")
async def get_by_request_id(request_id: str, app_state) -> list[AuditEventResponse]:
    # validate UUID-v4, query with ORDER BY timestamp_utc ASC, audit_id ASC
    ...

@router.get("/audit/events")
async def get_by_filter(
    user_id: str | None = None,
    event_type: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    app_state = ...
) -> list[AuditEventResponse]:
    # build WHERE clause dynamically
    # validate ISO-8601 UTC for from/to, validate from < to
    # ORDER BY timestamp_utc DESC LIMIT 1000
    ...

@router.get("/audit/summary")
async def get_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    app_state = ...
) -> SummaryResponse:
    # aggregate SELECT with GROUP BY outcome and GROUP BY layer
    # validate from/to
    ...

@router.get("/health")
async def health(app_state) -> dict:
    # execute SELECT 1 with 200ms timeout
    # return {"status":"ok","db":"connected"} or {"status":"degraded","db":"unreachable"}
    ...
```

### `metrics.py` — Prometheus Definitions

```python
from prometheus_client import Counter, Histogram

writes_total = Counter(
    "llm_audit_writes_total",
    "Total audit events successfully written",
    labelnames=["event_type", "layer"]
)

write_latency = Histogram(
    "llm_audit_write_latency_seconds",
    "Write handler latency from entry to DB confirmation or error",
    labelnames=["event_type", "layer"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)
```

Label values use the enum string values. If an event carries an out-of-enumeration value (possible only if validation is bypassed, which should not happen), the label is recorded as `"unknown"`.

### `logging_config.py` — Structured JSON Logger

Uses Python's standard `logging` module with a custom `JSONFormatter`:

```python
import logging, json, datetime

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
        }
        payload.update(record.__dict__.get("extra_fields", {}))
        return json.dumps(payload)  # guaranteed single line
```

All write operations pass structured fields via the `extra` keyword argument:
```python
logger.info("audit_event_written", extra={"extra_fields": {
    "audit_id": audit_id, "request_id": request_id,
    "layer": layer, "event_type": event_type, "latency_ms": latency_ms
}})
```

---

## Data Models

### SQLite Schema (Full DDL)

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id          TEXT    PRIMARY KEY,
    request_id        TEXT    NOT NULL,
    timestamp_utc     TEXT    NOT NULL,
    user_id           TEXT,
    department        TEXT,
    layer             TEXT,
    event_type        TEXT,
    model_used        TEXT,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms        INTEGER DEFAULT 0,
    outcome           TEXT,
    error_code        TEXT,
    pii_actions       TEXT,    -- serialized JSON array string
    policy_decisions  TEXT     -- serialized JSON array string
);

CREATE INDEX IF NOT EXISTS idx_request_id ON audit_events(request_id);
CREATE INDEX IF NOT EXISTS idx_user_id    ON audit_events(user_id);
CREATE INDEX IF NOT EXISTS idx_timestamp  ON audit_events(timestamp_utc);
```

### Index Rationale

| Index | Query Pattern Served |
|---|---|
| `idx_request_id` | `GET /audit/requests/{request_id}` — primary trace lookup |
| `idx_user_id` | `GET /audit/events?user_id=` — incident investigation |
| `idx_timestamp` | All range queries (`from`/`to`), summary aggregation |

The `audit_id` PRIMARY KEY index is created implicitly by SQLite.

### JSON Column Serialization

`pii_actions` and `policy_decisions` are stored as JSON-serialized strings. On write: `json.dumps(value)`. On read: `json.loads(value)`. If `json.loads` raises an exception for a stored value (indicating corrupt data from a direct DB write), the raw string is returned and a `WARNING` log is emitted with the offending `audit_id`.

### Audit Record Lifecycle

```
Caller Layer
    │
    │  HTTP POST (fire-and-forget)
    ▼
Auth Middleware ──► 401/403 (reject)
    │
    ▼
Pydantic Validation ──► 422 (reject with field errors)
    │
    ▼
UUID-v4 + Timestamp auto-assignment (if absent)
    │
    ▼
JSON serialization of pii_actions / policy_decisions
    │
    ▼
SQLite INSERT (single or wrapped in BEGIN IMMEDIATE for batch)
    │
    ├──► HTTP 201 + audit_id(s) returned to caller
    ├──► Prometheus counter incremented
    └──► INFO log emitted
```

### Cross-Layer Correlation

Every `AuditEvent` contains `request_id` (UUID-v4) sourced from the IMF. To reconstruct the full lifecycle of a single user request, the query `GET /audit/requests/{request_id}` returns all events ordered by `timestamp_utc` ascending, revealing the sequence of layer activations. This cross-layer trace is the primary POC deliverable.

### How Layers Write to the Audit Store

Each platform layer uses this fire-and-forget async client pattern:

```python
import httpx
import logging

logger = logging.getLogger(__name__)

async def write_audit_event(event: dict, audit_store_url: str, api_key: str) -> None:
    """Non-blocking audit write. Errors are logged but never raised to the caller."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{audit_store_url}/audit/events",
                json=event,
                headers={"X-API-Key": api_key}
            )
    except Exception as exc:
        logger.warning("audit_write_failed", extra={"extra_fields": {
            "error": str(exc), "request_id": event.get("request_id")
        }})
```

The 2-second timeout ensures that even in degraded conditions, the calling layer's request processing is not blocked for more than 2 seconds. Failed writes are logged but not retried — audit completeness for the POC is best-effort for fire-and-forget senders.

---

## Helm Chart Structure

The chart lives at `llm-platform/charts/audit-store/` and follows the platform Helm conventions, with POC-appropriate overrides (no HPA, no Vault, single replica).

```
llm-platform/charts/audit-store/
├── Chart.yaml
├── values.yaml
├── README.md
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── networkpolicy.yaml
    └── servicemonitor.yaml
```

Note: `hpa.yaml` is omitted for the POC because `autoscaling.enabled: false` and the Audit Store is a stateful single-instance service. A StatefulSet (instead of Deployment) would be appropriate in production; for POC, a Deployment with a PVC is sufficient.

### `Chart.yaml`

```yaml
apiVersion: v2
name: audit-store
description: Append-only audit trail service for the Enterprise LLM Platform (POC)
type: application
version: 0.1.0
appVersion: "0.1.0"
```

### `values.yaml`

```yaml
replicaCount: 1

image:
  repository: registry.local/audit-store
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 9200

env:
  DB_PATH: "/data/audit.db"
  LOG_LEVEL: "INFO"
  # AUDIT_API_KEY must be provided at deploy time via --set env.AUDIT_API_KEY=<value>
  # or via a Kubernetes Secret reference; never committed to values.yaml

persistence:
  enabled: true
  size: 5Gi
  storageClass: ""
  accessMode: ReadWriteOnce

resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"

observability:
  metrics:
    enabled: true
    port: 9090

autoscaling:
  enabled: false

vault:
  enabled: false
```

### `templates/deployment.yaml` (key sections)

```yaml
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      containers:
        - name: audit-store
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: 9200   # application API
            - containerPort: 9090   # Prometheus metrics
          env:
            - name: DB_PATH
              value: {{ .Values.env.DB_PATH }}
            - name: LOG_LEVEL
              value: {{ .Values.env.LOG_LEVEL }}
            - name: AUDIT_API_KEY
              valueFrom:
                secretKeyRef:
                  name: audit-store-secrets
                  key: AUDIT_API_KEY
          volumeMounts:
            {{- if .Values.persistence.enabled }}
            - name: data
              mountPath: /data
            {{- end }}
          livenessProbe:
            httpGet:
              path: /health
              port: 9200
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 9200
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        {{- if .Values.persistence.enabled }}
        - name: data
          persistentVolumeClaim:
            claimName: {{ include "audit-store.fullname" . }}-data
        {{- end }}
```

### `templates/service.yaml`

Exposes two ports on a single ClusterIP Service:
- Port 9200 for the application API (write + query + health)
- Port 9090 for Prometheus metrics scraping

### `templates/networkpolicy.yaml`

```yaml
# Ingress to port 9200: platform layer namespaces only
# Ingress to port 9090: llm-observability namespace only
# All other ingress: denied
spec:
  podSelector:
    matchLabels: {{ include "audit-store.selectorLabels" . | nindent 6 }}
  policyTypes: [Ingress]
  ingress:
    - ports: [{port: 9200}]
      from:
        - namespaceSelector:
            matchExpressions:
              - key: kubernetes.io/metadata.name
                operator: In
                values:
                  - llm-api-gateway
                  - llm-security
                  - llm-router
                  - llm-cache
                  - llm-inference
                  - llm-agent-framework
                  - llm-governance
    - ports: [{port: 9090}]
      from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: llm-observability
```

### `templates/servicemonitor.yaml`

```yaml
spec:
  endpoints:
    - port: metrics        # named port 9090 in service.yaml
      path: /metrics
      interval: 30s
  selector:
    matchLabels: {{ include "audit-store.selectorLabels" . | nindent 6 }}
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature uses **hypothesis** (Python property-based testing library) to validate these properties with a minimum of 100 generated inputs each.

**Property reflection:** After analyzing all 11 requirements, several properties are logically related or implied by broader ones. Specifically:
- Properties for 1.5, 1.6, 1.7 (enum validation for `layer`, `event_type`, `outcome`) are consolidated into a single "enum field validation" property, since all three follow the same pattern.
- Properties 4.2 and 4.3 (from/to range filtering) are combined with 4.5 (conjunctive filter application) into one "filter correctness" property.
- Properties 9.2, 9.3, and 9.4 (log field presence) are consolidated into a single "log structure invariant" property.
- Property 8.4 (failed writes don't increment counter but do record latency) is subsumed by Properties 15 and 16 together.
- Summary properties 5.2 and 5.3 are subsumed by Property 17 (sum invariant with time range).

---

### Property 1: Write single valid event succeeds with auto-assigned IDs

*For any* valid `AuditEventCreate` object (with mandatory fields `request_id`, `layer`, `event_type`, `outcome` present and valid), posting it to `POST /audit/events` SHALL return HTTP 201 with a non-null `audit_id` in the response body that is a valid UUID-v4.

**Validates: Requirements 1.1, 1.2, 1.3**

---

### Property 2: audit_id auto-generation is always a valid UUID-v4

*For any* valid audit event submitted without an `audit_id` field, the `audit_id` present in the HTTP 201 response SHALL match the UUID-v4 format pattern `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`.

**Validates: Requirement 1.2**

---

### Property 3: Invalid request_id always rejected with HTTP 422

*For any* string value submitted as `request_id` that does not match the UUID-v4 format (including empty strings, non-UUID strings, integers, or null), the write endpoint SHALL return HTTP 422 with a structured error body identifying `request_id` as the failing field.

**Validates: Requirement 1.4**

---

### Property 4: Invalid enum field values always rejected with HTTP 422

*For any* value submitted for `layer`, `event_type`, or `outcome` that is not in its respective enumeration, the write endpoint SHALL return HTTP 422 with a structured error body that identifies the invalid field name and value. This holds regardless of which enum field contains the invalid value and regardless of the values of other fields.

**Validates: Requirements 1.5, 1.6, 1.7**

---

### Property 5: Non-JSON body always rejected with HTTP 400

*For any* byte sequence submitted as the request body to `POST /audit/events` that is not parseable as valid JSON, the write endpoint SHALL return HTTP 400. No insertion is attempted.

**Validates: Requirement 1.8**

---

### Property 6: Append-only API surface — write-mutating HTTP methods always rejected

*For any* string used as a path identifier and *for any* of the HTTP methods `PUT`, `PATCH`, `DELETE` applied to any `/audit/events/*` or `/audit/requests/*` path, the service SHALL return HTTP 404 or HTTP 405. No modification or deletion of stored records is possible through the API.

**Validates: Requirement 1.10**

---

### Property 7: Batch write is all-or-nothing — atomicity invariant

*For any* batch submitted to `POST /audit/events/batch` that contains at least one record with a validation error (invalid `request_id`, `layer`, `event_type`, or `outcome`), the endpoint SHALL return HTTP 422, zero records SHALL be inserted into the database, and the error body SHALL identify every failing record by its index in the input array.

**Validates: Requirements 2.1, 2.3**

---

### Property 8: Batch size boundary enforcement

*For any* JSON array submitted to `POST /audit/events/batch` with length > 500, the endpoint SHALL return HTTP 422. *For any* array of length 1 through 500 containing only valid records, the endpoint SHALL return HTTP 201 with a list of `audit_ids` whose length equals the length of the input array.

**Validates: Requirements 2.1, 2.5**

---

### Property 9: Request lifecycle trace is ordered correctly

*For any* `request_id` with N ≥ 2 associated audit events stored in the database, `GET /audit/requests/{request_id}` SHALL return a JSON array of exactly N events ordered by `timestamp_utc` ascending; when two events share the same `timestamp_utc`, they SHALL be secondarily ordered by `audit_id` ascending.

**Validates: Requirement 3.1**

---

### Property 10: JSON round-trip for pii_actions and policy_decisions

*For any* audit event written with arbitrary JSON-serializable arrays in `pii_actions` and `policy_decisions`, querying that event back via `GET /audit/requests/{request_id}` SHALL return those fields as native JSON arrays that are equal to the originally submitted values (i.e., `json.loads(json.dumps(value)) == value`).

**Validates: Requirements 3.4, 7.3**

---

### Property 11: Duplicate audit_id submission returns HTTP 409

*For any* `audit_id` value that already exists in the database, a subsequent `POST /audit/events` or batch containing the same `audit_id` SHALL return HTTP 409. The pre-existing record SHALL remain unchanged.

**Validates: Requirement 7.4**

---

### Property 12: Filter query results satisfy all supplied conditions conjunctively

*For any* combination of filter parameters (`user_id`, `event_type`, `from`, `to`) supplied to `GET /audit/events`, every event in the returned JSON array SHALL satisfy ALL supplied filter conditions simultaneously (AND logic). No event violating any single filter condition SHALL appear in the results.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**

---

### Property 13: Filter query results are ordered descending by timestamp, capped at 1000

*For any* query to `GET /audit/events` — with any combination of valid filter parameters — the returned array SHALL have at most 1000 elements, and the elements SHALL be ordered by `timestamp_utc` descending.

**Validates: Requirement 4.6**

---

### Property 14: Summary counts form a consistent totals invariant

*For any* time range (or no range) applied to `GET /audit/summary`, the response SHALL satisfy: `sum(by_outcome.values()) == total_events` AND `sum(by_layer.values()) == total_events`. The `total_events` count SHALL equal the number of records in the database that fall within the specified range.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4**

---

### Property 15: llm_audit_writes_total incremented exactly on successful inserts

*For any* N audit events successfully inserted (individually or in a batch), the `llm_audit_writes_total` counter with the corresponding `{event_type, layer}` labels SHALL increase by exactly N. *For any* write attempt that fails validation or encounters a DB error, the counter SHALL NOT increment.

**Validates: Requirements 8.2, 8.4**

---

### Property 16: llm_audit_write_latency_seconds records every write attempt

*For any* write attempt to the Audit Store (whether it succeeds or fails), the `llm_audit_write_latency_seconds` histogram observation count SHALL increase by at least 1, and the recorded value SHALL be ≥ 0.

**Validates: Requirements 8.3, 8.4**

---

### Property 17: Every log entry is a single-line JSON object with mandatory fields

*For any* operation performed by the Audit Store (write, query, error, startup), every log line emitted to stdout SHALL be parseable as a single JSON object and SHALL contain at minimum the fields `timestamp` (ISO-8601 UTC string) and `level` (one of `DEBUG`, `INFO`, `WARNING`, `ERROR`). No log entry SHALL span more than one line.

**Validates: Requirements 9.1, 9.4**

---

### Property 18: Auth enforcement — missing or invalid API key always rejected on write endpoints

*For any* POST request to `/audit/events` or `/audit/events/batch`:
- Sent without an `X-API-Key` header → always returns HTTP 401 with `{"error": "missing_api_key"}`
- Sent with any `X-API-Key` value that does not match the configured key → always returns HTTP 403 with `{"error": "invalid_api_key"}`

This holds regardless of request body content.

**Validates: Requirements 10.1, 10.2**

---

### Property 19: GET endpoints never require authentication

*For any* GET request to `/audit/requests/{id}`, `/audit/events`, `/audit/summary`, or `/health`, sent without an `X-API-Key` header, the response SHALL NOT be HTTP 401 or HTTP 403.

**Validates: Requirements 10.6, 6.4**

---

## Error Handling

### Validation Errors (HTTP 422)

Pydantic validation is used for all write and query inputs. FastAPI's default 422 response structure is extended to always identify the failing field:

```json
{
  "detail": [
    {
      "loc": ["body", "request_id"],
      "msg": "request_id must be a valid UUID-v4",
      "type": "value_error"
    }
  ]
}
```

Validation errors take precedence over all other error types — if validation fails, no DB operation is attempted.

### Malformed JSON (HTTP 400)

FastAPI raises a `RequestValidationError` when the body cannot be parsed as JSON. A custom exception handler converts this to HTTP 400 with a structured body indicating a malformed request.

### Duplicate Key (HTTP 409)

When SQLite raises an `IntegrityError` with `UNIQUE constraint failed: audit_events.audit_id`, the handler returns HTTP 409 with:

```json
{"error": "duplicate_audit_id", "audit_id": "<conflicting_id>"}
```

### Database Errors (HTTP 500)

Any other `sqlite3.Error` during insert or query is caught, logged at ERROR level with full exception detail, and returned to the caller as HTTP 500:

```json
{"error": "database_error", "detail": "internal storage failure"}
```

The detail field is intentionally generic — internal DB errors are not exposed to callers to avoid information leakage.

### Startup Failures (Non-Zero Exit)

The following conditions cause the service to log an ERROR and exit with code 1 before accepting any requests:

| Condition | Error logged |
|---|---|
| `AUDIT_API_KEY` env var missing or empty | `"AUDIT_API_KEY environment variable is not set"` |
| `DB_PATH` parent directory does not exist | `"DB_PATH parent directory does not exist: /data"` |
| SQLite connection failure (permission, corruption) | `"Failed to connect to SQLite database: {reason}"` |

This is enforced in the FastAPI `lifespan` context manager before the `yield` that starts the server.

### Health Check Degraded State

When the `/health` probe SELECT query fails or exceeds 200 ms, the service returns HTTP 503 rather than a 5xx from the probe itself. This distinction matters for Kubernetes — liveness/readiness probes treat 503 as a failed check, which triggers pod replacement.

### Time Range Validation

A shared utility function validates `from`/`to` parameters across all endpoints that accept them:

1. Parse as ISO-8601 datetime; require `Z` or `+00:00` suffix (reject naive datetimes).
2. If `from` and `to` are both present, assert `from < to`; return HTTP 422 if not.

This logic is centralized and reused by `/audit/events`, `/audit/summary`, and any future endpoints.

---

## Testing Strategy

### Test Stack

- **Framework:** `pytest`
- **Property-based testing:** `hypothesis` (minimum 100 iterations per property test)
- **HTTP test client:** `httpx.AsyncClient` with FastAPI's `ASGITransport` (no real network, no running server required)
- **SQLite:** in-memory SQLite (`:memory:`) for unit and property tests — fast, isolated, no file I/O
- **Metrics isolation:** reset Prometheus registry between tests using `prometheus_client.REGISTRY._names_to_collectors.clear()` or a test-scoped fixture

### Test Organization

```
tests/
├── conftest.py          # app fixture with in-memory SQLite and test API key
├── unit/
│   ├── test_models.py          # Pydantic model validation (examples + edge cases)
│   ├── test_database.py        # schema init, WAL mode, index creation
│   └── test_logging.py         # JSONFormatter output structure
├── property/
│   ├── test_write_properties.py   # Properties 1–8, 11, 15, 16, 18
│   ├── test_query_properties.py   # Properties 9, 10, 12–14, 19
│   └── test_logging_properties.py # Property 17
├── integration/
│   ├── test_health.py          # Health endpoint smoke + DB failure simulation
│   └── test_startup.py         # Startup failure conditions
└── smoke/
    └── test_helm.py            # helm lint + helm template dry-run assertions
```

### Property Test Configuration

Each property test file opens with:

```python
from hypothesis import given, settings, HealthCheck

settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")
```

Property tag comment format (on each test function):
```python
# Feature: audit-store, Property 1: Write single valid event succeeds with auto-assigned IDs
```

### Example Property Tests

**Property 1 — Single write succeeds:**
```python
from hypothesis import given
from hypothesis import strategies as st

@given(
    request_id=st.uuids(version=4).map(str),
    layer=st.sampled_from([e.value for e in LayerEnum]),
    event_type=st.sampled_from([e.value for e in EventTypeEnum]),
    outcome=st.sampled_from([e.value for e in OutcomeEnum]),
)
async def test_valid_single_write_returns_201(client, request_id, layer, event_type, outcome):
    # Feature: audit-store, Property 1
    response = await client.post("/audit/events", json={
        "request_id": request_id, "layer": layer,
        "event_type": event_type, "outcome": outcome
    }, headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 201
    assert UUID4_RE.match(response.json()["audit_id"])
```

**Property 7 — Batch atomicity:**
```python
@given(
    valid_events=st.lists(valid_event_strategy(), min_size=1, max_size=20),
    bad_index=st.integers(min_value=0),
)
async def test_batch_with_invalid_record_inserts_nothing(client, valid_events, bad_index):
    # Feature: audit-store, Property 7
    bad_index = bad_index % len(valid_events)
    events = list(valid_events)
    events[bad_index] = {**events[bad_index], "layer": "not_a_valid_layer"}
    before_count = await get_event_count(client)
    response = await client.post("/audit/events/batch",
        json={"events": events}, headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 422
    assert await get_event_count(client) == before_count  # zero insertion
```

**Property 14 — Summary totals invariant:**
```python
@given(events=st.lists(valid_event_strategy(), min_size=0, max_size=50))
async def test_summary_totals_invariant(client, events):
    # Feature: audit-store, Property 14
    for e in events:
        await client.post("/audit/events", json=e, headers={"X-API-Key": TEST_API_KEY})
    summary = (await client.get("/audit/summary")).json()
    assert sum(summary["by_outcome"].values()) == summary["total_events"]
    assert sum(summary["by_layer"].values()) == summary["total_events"]
```

### Unit Tests (Example-Based)

Unit tests cover:
- Specific HTTP status codes for each error condition (empty batch, > 500 records, missing DB parent dir, missing API key env var)
- `JSONFormatter` produces parseable single-line JSON for INFO, WARNING, ERROR levels
- `init_schema` is idempotent when run twice on the same connection
- `GET /audit/requests/{request_id}` with a non-UUID path param returns 422 with a `detail.request_id` field

### Integration Tests

Integration tests cover:
- `/health` returns 200 when DB is healthy, 503 when DB is replaced with an unconnectable path
- Full lifecycle trace: write 6 events (one per layer) for the same `request_id`, query by `request_id`, verify all 6 returned in timestamp order
- Batch rollback: simulate a SQLite `IntegrityError` mid-batch (by pre-inserting a conflicting `audit_id`), verify zero additional records inserted

### Smoke Tests

Smoke tests cover:
- `helm lint llm-platform/charts/audit-store/`
- `helm template` renders a Deployment, Service, NetworkPolicy, and ServiceMonitor
- Service starts with a fresh in-memory DB, schema exists after startup, WAL mode is active
- Service refuses to start when `AUDIT_API_KEY` is unset

### What Is Not Tested with PBT

The following are excluded from property-based testing due to their nature:

| Item | Reason | Alternative |
|---|---|---|
| Helm chart templates | Declarative config, not application logic | `helm lint` + `helm template` |
| SQLite startup/init | One-time setup, not input-dependent | Smoke tests |
| DB-level failures (Req 1.9, 2.6) | Requires infrastructure fault injection | Integration tests with mocks |
| `/health` latency p95 | Performance property, not correctness | Load test or manual benchmark |
| Log level filtering | Startup config, single example | Unit test with LOG_LEVEL=WARNING |
