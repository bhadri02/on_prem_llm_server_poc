# Design Document: API Gateway (Layer 1 — POC)

## Overview

The API Gateway is the single ingress point for all LLM traffic from enterprise consumer applications. It presents an OpenAI-compatible REST API surface, authenticates callers via a static API key, enforces an in-memory rate limit, normalizes every inbound payload into the Internal Message Format (IMF), and forwards the IMF downstream to the Security & Governance Layer via plain HTTP POST. Responses from downstream are deserialized from IMF back into OpenAI-compatible JSON before being returned to the caller. For streaming requests (`stream: true`) the gateway proxies the SSE byte stream without buffering the full body.

This is a POC implementation. The design deliberately chooses the simplest correct approach for each concern. All production-hardening items (TLS, OIDC, Redis rate limiting, mTLS, gRPC, HPA) are explicitly deferred to Phase 2.

### Technology Stack

| Concern | Choice | Rationale |
|---|---|---|
| Web framework | FastAPI (Python 3.11+) | Matches all other services in the platform; async-native; built-in Pydantic validation |
| HTTP client | `httpx.AsyncClient` | Async, timeout support, used in inference_adapter pattern |
| Pydantic models | Pydantic v2 | Matches rest of platform; strict schema validation |
| Config | `pydantic-settings BaseSettings` | Matches `cache_service` and `inference_adapter` conventions |
| Metrics | `prometheus_client` | Matches platform observability contract |
| UUID generation | `uuid.uuid4()` stdlib | No extra dependency |
| Logging | stdlib `logging` + `JSONFormatter` | Matches `audit_store` logging_config pattern |
| Container runtime | Uvicorn | Matches all other services |
| Deployment | Helm chart at `llm-platform/charts/api-gateway/` | Platform convention |

---

## Architecture

```
Consumer (HTTP)
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  Kubernetes NGINX Ingress (HTTP, port 80)                │
│  Host: llm-poc.local  Paths: /v1, /health               │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP :8080
                           ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI Application (api_gateway)                       │
│                                                          │
│  Middleware stack (executed in order):                   │
│  1. PrometheusMiddleware  — latency + counter tracking   │
│  2. LoggingMiddleware     — structured JSON to stdout    │
│  3. AuthMiddleware        — X-Api-Key validation         │
│  4. RateLimitMiddleware   — sliding-window 60 req/min    │
│                                                          │
│  Routers:                                                │
│  ┌─────────────────────────────────────────────────┐    │
│  │ POST /v1/chat/completions                        │    │
│  │   → normalize_request() → forward_to_security() │    │
│  │   → serialize_response() / proxy_sse_stream()   │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ GET  /v1/models  → static model list            │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ GET  /health     → {"status": "ok"}             │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ GET  /metrics    → prometheus_client text fmt   │    │
│  └─────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP POST :8081/process
                           ▼
              Security & Governance Layer
```

### Middleware Execution Order

FastAPI / Starlette applies middleware in **reverse registration order** (last-added wraps outermost). The registration order below produces the correct execution order:

```
app.add_middleware(RateLimitMiddleware)   # innermost — registered first
app.add_middleware(AuthMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(PrometheusMiddleware)  # outermost — registered last
```

Execution order on inbound request:
```
PrometheusMiddleware → LoggingMiddleware → AuthMiddleware → RateLimitMiddleware → Router
```

This ensures:
- Prometheus captures latency for all requests including auth failures
- Logging captures all requests including rejected ones
- Auth runs before rate limiting (no counter increment for unauthenticated requests)
- Rate limiting runs before the route handler (no IMF construction for rate-limited requests)

---

## Components and Interfaces

### Module / File Structure

```
api_gateway/
├── main.py                    # FastAPI app factory + lifespan
├── config.py                  # pydantic-settings Settings + get_settings()
├── metrics.py                 # prometheus_client Counter / Histogram definitions
├── middleware/
│   ├── __init__.py
│   ├── auth.py                # AuthMiddleware (X-Api-Key check)
│   ├── rate_limit.py          # RateLimitMiddleware (sliding-window)
│   ├── logging.py             # LoggingMiddleware (JSON to stdout)
│   └── prometheus.py          # PrometheusMiddleware (metrics instrumentation)
├── routers/
│   ├── __init__.py
│   ├── chat.py                # POST /v1/chat/completions
│   ├── models.py              # GET /v1/models
│   └── health.py              # GET /health
├── schemas/
│   ├── __init__.py
│   ├── imf.py                 # Full IMF Pydantic model (IMFDocument + sub-models)
│   ├── openai.py              # OpenAI request + response Pydantic models
│   └── audit.py               # AuditEvent Pydantic model
├── services/
│   ├── __init__.py
│   ├── normalizer.py          # OpenAI payload → IMF (build_imf())
│   ├── serializer.py          # IMF response → OpenAI JSON (serialize_response())
│   ├── downstream.py          # HTTP client wrapper (forward_to_security())
│   └── audit.py               # Audit event emission (emit_audit_event())
├── Dockerfile
└── requirements.txt
```

### Key Component Interfaces

#### `config.py` — Settings

```python
class Settings(BaseSettings):
    gateway_api_key: str          # env: GATEWAY_API_KEY — must be non-empty
    downstream_security_url: str  # env: DOWNSTREAM_SECURITY_URL
    log_level: str                # env: LOG_LEVEL — default "INFO"
    port: int                     # env: PORT — default 8080
    metrics_port: int             # env: METRICS_PORT — default 9090
    rate_limit_requests: int      # env: RATE_LIMIT_REQUESTS — default 60
    rate_limit_window_seconds: int # env: RATE_LIMIT_WINDOW_SECONDS — default 60
    downstream_timeout_seconds: float  # env: DOWNSTREAM_TIMEOUT — default 10.0
```

Startup validation: if `gateway_api_key` is empty string or not set, the Settings validator raises `ValueError` and the process fails to start.

#### `middleware/auth.py` — AuthMiddleware

```python
class AuthMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {"/health", "/metrics"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        key = request.headers.get("X-Api-Key", "")
        if not key or key != settings.gateway_api_key:
            reason = "missing_header" if not key else "key_mismatch"
            emit_audit_event(AuditEventType.AUTH_FAIL, reason=reason, ...)
            return JSONResponse(status_code=401, content=ERROR_401)
        emit_audit_event(AuditEventType.AUTH_PASS, ...)
        return await call_next(request)
```

#### `middleware/rate_limit.py` — RateLimitMiddleware

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    _store: dict[str, list[float]] = {}   # key → [timestamp, ...]
    EXEMPT_PATHS = {"/health", "/metrics"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        key = request.headers.get("X-Api-Key", "unknown")
        now = time.time()
        window_start = now - settings.rate_limit_window_seconds
        # Evict expired timestamps
        timestamps = [t for t in self._store.get(key, []) if t > window_start]
        if len(timestamps) >= settings.rate_limit_requests:
            emit_audit_event(AuditEventType.RATE_LIMITED, ...)
            return JSONResponse(
                status_code=429,
                content=ERROR_429,
                headers={"Retry-After": "60"},
            )
        timestamps.append(now)
        self._store[key] = timestamps
        return await call_next(request)
```

**Note on concurrency:** For the POC single-instance deployment, `asyncio` event loop is single-threaded, so the in-memory dict is safe without a lock. Phase 2 upgrade path: replace dict with Redis ZSET + ZADD/ZREMRANGEBYSCORE.

#### `services/normalizer.py` — build_imf()

```python
def build_imf(payload: OpenAIChatRequest) -> IMFDocument:
    request_id = str(uuid.uuid4())
    return IMFDocument(
        request_id=request_id,
        trace_id=request_id,
        span_id="",
        timestamp_utc=datetime.utcnow().isoformat() + "Z",
        user=IMFUser(
            user_id="poc-user",
            department="poc",
            roles=["developer"],
            auth_method="api_key",
        ),
        request=IMFRequest(
            model=payload.model,
            task_type=None,
            messages=[IMFMessage(role=m.role, content=m.content) for m in payload.messages],
            stream=payload.stream or False,
            max_tokens=payload.max_tokens or 2048,
            temperature=payload.temperature if payload.temperature is not None else 0.7,
        ),
        governance=IMFGovernance(),   # all defaults
        routing=IMFRouting(),         # all defaults
        cache=IMFCache(),             # all defaults
        response=IMFResponse(),       # all defaults
        metadata={},
        extensions={},
    )
```

#### `services/serializer.py` — serialize_response()

```python
def serialize_response(imf: IMFDocument) -> dict:
    return {
        "id": f"chatcmpl-{imf.request_id}",
        "object": "chat.completion",
        "created": int(datetime.utcnow().timestamp()),
        "model": imf.request.model or "",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": imf.response.content,
            },
            "finish_reason": imf.response.finish_reason,
        }],
        "usage": {
            "prompt_tokens": imf.response.usage.prompt_tokens,
            "completion_tokens": imf.response.usage.completion_tokens,
            "total_tokens": imf.response.usage.total_tokens,
        },
    }
```

#### `services/downstream.py` — forward_to_security()

```python
async def forward_to_security(imf: IMFDocument, client: httpx.AsyncClient) -> IMFDocument:
    url = f"{settings.downstream_security_url}/process"
    try:
        resp = await client.post(
            url,
            json=imf.model_dump(),
            headers={"Content-Type": "application/json"},
            timeout=settings.downstream_timeout_seconds,
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError):
        raise DownstreamError(502)
    if resp.status_code != 200:
        raise DownstreamError(502)
    try:
        return IMFDocument.model_validate(resp.json())
    except Exception:
        raise DownstreamError(502)
```

---

## Data Models

### IMF Pydantic Models (`schemas/imf.py`)

The API Gateway defines the **canonical** IMF Pydantic models for the platform at this layer (it is the producer). Downstream services use a compatible subset.

```python
class IMFMessage(BaseModel):
    role: str       # "system" | "user" | "assistant"
    content: str

class IMFUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class IMFResponse(BaseModel):
    content: str | None = None
    finish_reason: str | None = None   # "stop" | "length" | "tool_call" | None
    usage: IMFUsage = Field(default_factory=IMFUsage)

class IMFGovernance(BaseModel):
    pii_masked: bool = False
    pii_fields_detected: list[str] = []
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list = []

class IMFRouting(BaseModel):
    selected_model: str | None = None
    routing_mode: str = "auto"
    fallback_level: int = 0

class IMFCache(BaseModel):
    lookup_hit: bool = False
    cache_key: str | None = None

class IMFUser(BaseModel):
    user_id: str = "poc-user"
    department: str = "poc"
    roles: list[str] = ["developer"]
    auth_method: str = "api_key"

class IMFRequest(BaseModel):
    model: str | None = None
    task_type: str | None = None
    messages: list[IMFMessage] = []
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7

class IMFDocument(BaseModel):
    request_id: str
    trace_id: str
    span_id: str = ""
    timestamp_utc: str
    user: IMFUser = Field(default_factory=IMFUser)
    request: IMFRequest = Field(default_factory=IMFRequest)
    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    routing: IMFRouting = Field(default_factory=IMFRouting)
    cache: IMFCache = Field(default_factory=IMFCache)
    response: IMFResponse = Field(default_factory=IMFResponse)
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)
```

### OpenAI Request/Response Models (`schemas/openai.py`)

```python
class OpenAIMessage(BaseModel):
    role: str
    content: str

class OpenAIChatRequest(BaseModel):
    model: str | None = None
    messages: list[OpenAIMessage]   # required; Pydantic raises 422 if absent
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None

    @field_validator("messages")
    @classmethod
    def messages_must_be_non_empty(cls, v):
        if not v:
            raise ValueError("messages must be a non-empty array")
        return v

class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[dict]   # [{"id": "...", "object": "model"}]

class OpenAIChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict]
    usage: dict
```

### Audit Event Model (`schemas/audit.py`)

```python
class AuditEvent(BaseModel):
    audit_id: str           # UUID v4, unique per event
    request_id: str         # UUID v4, matches IMF request_id
    timestamp_utc: str      # ISO-8601 UTC
    user_id: str | None = None
    department: str | None = None
    layer: str = "api_gateway"
    event_type: str         # request_received | auth_pass | auth_fail | rate_limited | response_sent
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    outcome: str            # "pass" | "block" | "error"
    reason: str | None = None   # for auth_fail: "missing_header" | "key_mismatch"
    error_code: str | None = None
```

---

## Streaming Architecture

When `request.stream = true` in the IMF, the chat router uses `httpx.AsyncClient` with `stream=True` to open a streaming connection to the Security Layer. The response is proxied to the client via FastAPI `StreamingResponse`.

```
Client                 API Gateway              Security Layer
  │                        │                          │
  │ POST /v1/chat (stream) │                          │
  │───────────────────────►│                          │
  │                        │ POST /process (IMF+stream)│
  │                        │─────────────────────────►│
  │                        │                          │
  │                        │◄── SSE chunk: data: ... ─│
  │◄── data: ... ──────────│                          │
  │                        │◄── SSE chunk: data: ... ─│
  │◄── data: ... ──────────│                          │
  │                        │◄── data: [DONE]\n\n ─────│
  │◄── data: [DONE]\n\n ───│                          │
  │                        │ emit response_sent audit  │
```

Key design decisions:
- The `httpx.AsyncClient` is created in the lifespan and stored on `app.state` for reuse across requests (connection pooling).
- The streaming generator yields bytes directly without JSON parsing — the gateway acts as a transparent SSE proxy.
- On downstream error mid-stream, the generator closes immediately and emits `response_sent` with `outcome: "error"`.
- `StreamingResponse(content=stream_generator(), media_type="text/event-stream")` is returned from the route handler.

```python
async def stream_generator(resp: httpx.Response):
    async for chunk in resp.aiter_bytes():
        yield chunk
    # ensure [DONE] terminator reaches the client even if downstream omits it
```

---

## Helm Chart Structure

```
llm-platform/charts/api-gateway/
├── Chart.yaml
├── README.md
├── values.yaml
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── ingress.yaml
    ├── networkpolicy.yaml
    ├── servicemonitor.yaml
    └── hpa.yaml             # present but disabled (autoscaling.enabled: false for POC)
```

### Chart.yaml (key fields)

```yaml
apiVersion: v2
name: api-gateway
description: "Layer 1 — API Gateway for the Enterprise On-Prem LLM Platform (POC)"
type: application
version: 0.1.0
appVersion: "0.1.0"
```

### values.yaml (POC defaults)

```yaml
replicaCount: 1

image:
  repository: registry.local/api-gateway
  tag: ""          # MUST be overridden at deploy time
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8080

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: llm-poc.local
      paths:
        - path: /v1
          pathType: Prefix
        - path: /health
          pathType: Exact

env:
  GATEWAY_API_KEY: "poc-secret-key"          # MUST be replaced before non-dev deployment
  DOWNSTREAM_SECURITY_URL: "http://security-layer:8081"
  LOG_LEVEL: "INFO"
  PORT: "8080"
  METRICS_PORT: "9090"
  DOWNSTREAM_TIMEOUT: "10.0"
  RATE_LIMIT_REQUESTS: "60"
  RATE_LIMIT_WINDOW_SECONDS: "60"

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
  enabled: false   # env var secrets for POC

observability:
  metrics:
    enabled: true
    port: 9090
```

### NetworkPolicy

The NetworkPolicy template restricts ingress to only the NGINX ingress controller and restricts egress to only the Security Layer service on port 8081, plus DNS (port 53).

### ServiceMonitor

The ServiceMonitor template configures Prometheus scraping of `/metrics` on port 9090 with a 30-second interval.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The API Gateway contains significant pure-function logic (request validation, IMF normalization, response serialization, rate-limit window eviction, audit event construction) that is well-suited to property-based testing. The property-based testing library used is **Hypothesis** (Python).

### Property 1: Invalid messages field always returns 400

*For any* POST `/v1/chat/completions` request body where the `messages` field is absent, null, an empty array, or any non-array value, the API Gateway SHALL return HTTP 400 with the standard error body.

**Validates: Requirements 1.5, 1.6, 4.7, 11.7**

---

### Property 2: Missing or wrong API key always returns 401

*For any* valid request payload sent to `/v1/chat/completions` or `/v1/models` with an `X-Api-Key` header that is absent, empty, or any string value not equal to `GATEWAY_API_KEY`, the API Gateway SHALL return HTTP 401.

**Validates: Requirements 2.4, 2.5**

---

### Property 3: IMF normalization is a total function with correct field mapping

*For any* valid OpenAI chat request payload (arbitrary `model`, arbitrary non-empty `messages` list of any length, any `stream` boolean, any `max_tokens` integer, any `temperature` float), calling `build_imf(payload)` SHALL produce an IMFDocument where:
- `request_id` matches the UUID v4 format regex
- `trace_id` equals `request_id`
- `span_id` equals `""`
- `timestamp_utc` is parseable as an ISO-8601 UTC datetime
- `request.model` equals `payload.model` (or `None` if absent)
- `request.messages` is the same sequence as `payload.messages` in insertion order with `role` and `content` preserved exactly
- `request.stream` equals `payload.stream` (or `False` if absent)
- `request.max_tokens` equals `payload.max_tokens` (or `2048` if absent)
- `request.temperature` equals `payload.temperature` (or `0.7` if absent)
- `user.user_id` equals `"poc-user"`, `user.department` equals `"poc"`, `user.roles` equals `["developer"]`, `user.auth_method` equals `"api_key"`

**Validates: Requirements 4.1–4.11, 11.1–11.5**

---

### Property 4: IMF serialization round-trip preserves all field values

*For any* valid `IMFDocument` instance (with any combination of field values within their type domains), serializing to JSON via `model.model_dump()` and then deserializing back via `IMFDocument.model_validate()` SHALL produce an instance where every field value equals the corresponding field value of the original instance, with no data loss or type coercion.

**Validates: Requirements 11.6**

---

### Property 5: Sliding-window eviction leaves only in-window timestamps

*For any* list of timestamps spanning an arbitrary time range, after applying the rate-limiter's eviction function with the current time `now` and window of 60 seconds, the resulting list SHALL contain exactly those timestamps `t` where `t > now - 60`. Timestamps at or before the window boundary SHALL be removed.

**Validates: Requirements 3.1, 3.2, 3.5**

---

### Property 6: Response serialization maps all IMF fields to OpenAI schema

*For any* `IMFDocument` with a populated `response` block (arbitrary `content`, `finish_reason`, `usage`), calling `serialize_response(imf)` SHALL produce a dict where:
- `id` equals `f"chatcmpl-{imf.request_id}"`
- `object` equals `"chat.completion"`
- `choices[0].message.role` equals `"assistant"`
- `choices[0].message.content` equals `imf.response.content`
- `choices[0].finish_reason` equals `imf.response.finish_reason`
- `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens` equal the corresponding fields in `imf.response.usage`

**Validates: Requirements 6.1–6.4**

---

### Property 7: Downstream error mapping always produces 502

*For any* mocked downstream response with an HTTP status code other than 200 (i.e., any value in 4xx or 5xx), or for any simulated network error (timeout, connection refused), the `forward_to_security()` function SHALL raise `DownstreamError(502)` and the route handler SHALL return HTTP 502 to the client.

**Validates: Requirements 5.3, 5.4, 5.5**

---

### Property 8: Every audit event contains the mandatory invariant fields

*For any* request processed by the API Gateway that produces an audit event (auth_pass, auth_fail, rate_limited, request_received, or response_sent), the emitted `AuditEvent` object SHALL have:
- `audit_id` matching the UUID v4 format regex
- `layer` equal to `"api_gateway"`
- `outcome` equal to one of `"pass"`, `"block"`, or `"error"`

**Validates: Requirements 9.7**

---

### Property 9: Structured log record contains all mandatory fields for every request

*For any* HTTP request processed by the API Gateway (regardless of outcome — 200, 401, 429, 400, 502), the `LoggingMiddleware` SHALL emit exactly one JSON line to stdout containing the fields: `request_id`, `timestamp`, `method`, `path`, `status_code`, and `latency_ms`.

**Validates: Requirements 8.1, 8.2**

---

## Error Handling

### Error Response Format

All error responses use the canonical format:
```json
{"error": {"code": "<http_status_as_string>", "message": "<human_readable>"}}
```

### Error Taxonomy

| Condition | HTTP Status | Error body code |
|---|---|---|
| Missing or invalid `messages` field | 400 | `"400"` |
| Invalid JSON body | 400 | `"400"` |
| Missing or wrong `X-Api-Key` | 401 | `"401"` |
| Rate limit exceeded | 429 | `"429"` |
| Downstream non-200, timeout, or network error | 502 | `"502"` |
| Downstream 200 but empty/non-JSON body | 502 | `"502"` |
| Unhandled internal exception | 500 | `"500"` |
| Unknown path | 404 | FastAPI default |
| Wrong HTTP method on known path | 405 | FastAPI default |

### Unhandled Exception Handler

The `main.py` registers a global exception handler for unhandled `Exception` to ensure a JSON response is always returned and a structured error log is emitted:

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # emit structured ERROR log with traceback
    # return JSONResponse(status_code=500, ...)
```

### Downstream Timeout Handling

`httpx.AsyncClient` is configured with `timeout=10.0` (hard-coded default, configurable via `DOWNSTREAM_TIMEOUT` env var). Both `httpx.TimeoutException` and `httpx.ConnectError` are caught and mapped to 502.

### Body Re-injection Pattern

Like `cache_service` and `inference_adapter`, any middleware that must read the request body MUST re-inject it into `request.scope["_body"]` so downstream handlers can still read it. Only `LoggingMiddleware` reads the body (to extract `request_id`); all other middleware read only headers.

---

## Testing Strategy

### Dual Testing Approach

Both unit/example-based tests and property-based tests are required. Unit tests verify specific concrete behaviors and integration points; property tests verify universal correctness guarantees across a wide input space.

### Property-Based Testing (Hypothesis)

**Library:** `hypothesis` (Python)
**Configuration:** minimum 100 examples per property test (`settings(max_examples=100)`)

Each property test is tagged with a comment referencing the design property:
```python
# Feature: api-gateway, Property 3: IMF normalization is a total function with correct field mapping
@given(st.builds(OpenAIChatRequest, ...))
@settings(max_examples=100)
def test_imf_normalization_property(payload):
    ...
```

Property tests to implement (one test per property):
1. `test_invalid_messages_returns_400` — Hypothesis `st.one_of` for invalid messages shapes
2. `test_missing_or_wrong_api_key_returns_401` — generate arbitrary key strings
3. `test_imf_normalization_total_function` — generate valid `OpenAIChatRequest` instances
4. `test_imf_round_trip` — generate `IMFDocument` instances; serialize/deserialize; assert equality
5. `test_sliding_window_eviction` — generate timestamp lists; assert eviction postcondition
6. `test_response_serialization_field_mapping` — generate `IMFDocument` with varied response blocks
7. `test_downstream_error_maps_to_502` — generate non-200 status codes; mock httpx client
8. `test_audit_event_invariant_fields` — generate request scenarios; capture stdout; parse JSON
9. `test_log_record_mandatory_fields` — generate request scenarios; capture stdout; parse JSON

### Unit / Example-Based Tests

- `GET /health` returns 200 with `{"status": "ok"}` (no auth)
- `GET /v1/models` returns 200 with correct OpenAI list schema
- `GET /health` without API key returns 200 (auth exempt)
- `GET /v1/chat/completions` returns 405 (method not allowed)
- Request to undefined path returns 404
- 61 sequential requests with same API key — 61st returns 429 with `Retry-After: 60`
- Downstream 200 with empty body returns 502
- `LOG_LEVEL=ERROR` suppresses INFO log entries
- Startup with missing `GATEWAY_API_KEY` raises `ValidationError`

### Integration Tests

- Mock Security Layer returns IMF response → verify full OpenAI JSON response
- Mock Security Layer returns streaming SSE → verify `StreamingResponse` proxies chunks
- Mock downstream connection timeout → verify 502 response
- Full middleware pipeline with valid request → verify all 5 audit events in stdout order

### Test File Layout

```
tests/
├── conftest.py                 # TestClient fixtures, settings overrides
├── unit/
│   ├── test_normalizer.py      # build_imf() unit + property tests
│   ├── test_serializer.py      # serialize_response() unit + property tests
│   ├── test_rate_limiter.py    # sliding window eviction property tests
│   ├── test_auth_middleware.py # auth pass/fail property tests
│   └── test_audit.py           # audit event field invariant tests
├── integration/
│   ├── test_chat_endpoint.py   # full pipeline with mocked downstream
│   ├── test_streaming.py       # SSE proxy integration test
│   └── test_metrics.py         # Prometheus counter increment tests
└── smoke/
    ├── test_health.py
    └── test_startup.py
```
