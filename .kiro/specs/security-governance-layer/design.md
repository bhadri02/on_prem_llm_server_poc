# Design Document — Security & Governance Layer

## Overview

The Security & Governance Layer (Layer 2) is a standalone FastAPI microservice that enforces all platform governance controls on every request. It sits between the API Gateway (Layer 1) and the Intelligent Router (Layer 3), and every platform request passes through it twice: once before inference (pre-generation) and once after inference (post-generation).

In the pre-generation pass the service runs a four-stage pipeline — prompt injection scan, content safety filter, PII detection and masking, and role-based policy check — then fires a pre-audit event to the Audit Store and forwards the enriched IMF to the Router. In the post-generation pass it masks any PII that leaked into the model output, fires a post-audit event, and returns the enriched IMF to the caller.

This is a **POC implementation**. Production-deferred features (OPA/Rego, LlamaGuard, ML classifiers, mTLS, HashiCorp Vault, human approval workflow, autoscaling) are explicitly out of scope. The POC demonstrates that governance controls are applied to every request using lightweight rule-based alternatives, using the same IMF schema as the production target so upgrades will be additive.

**Ports:** API on 8081, Prometheus metrics on 9090.

**POC constraints in effect:** Plain HTTP JSON between services, static API key auth, rule-based security checks, SQLite-backed Audit Store (already running on port 9200), JSON-to-stdout logging, `autoscaling.enabled: false`, `vault.enabled: false`.

---

## Architecture

The service is a single-process FastAPI application following the same structural pattern as the Audit Store: a lifespan handler performs all startup validation, two ASGI apps share the same process (main app on 8081, metrics app on 9090), and all significant state is loaded once at startup and stored on `app.state`.

```mermaid
graph TD
    subgraph Callers
        GW[API Gateway\nport 8080]
    end

    subgraph Security Layer — port 8081
        PRE[POST /security/check\nPre-generation Pipeline]
        POST_EP[POST /security/post-check\nPost-generation Pipeline]
        HLT[GET /health]

        subgraph Pre-generation Pipeline
            INJ[1. Injection Detector\ninjection_patterns.yaml regex]
            CSF[2. Content Safety Filter\nblocklist keyword match]
            PII[3. PII Detector + Masker\nPresidio AnalyzerEngine\nAnonymizerEngine]
            POL[4. Policy Checker\nrole allow-list lookup]
            AUDIT_PRE[5. Pre-Audit Event\nfire-and-forget]
            FWD[6. Forward enriched IMF\nto Router]
        end

        subgraph Post-generation Pipeline
            PII2[1. PII Masker\non response.content]
            AUDIT_POST[2. Post-Audit Event\nfire-and-forget]
        end

        CFG[config.py\npydantic-settings]
        LOG[logging_config.py\nJSON stdout]
    end

    subgraph Downstream
        RTR[Intelligent Router\nport 8082]
        AUD[Audit Store\nport 9200]
    end

    subgraph Observability
        PROM[Prometheus Scraper]
        MTR[metrics_app.py\nport 9090]
    end

    GW -->|POST /security/check IMF| PRE
    GW -->|POST /security/post-check IMF| POST_EP

    PRE --> INJ
    INJ -->|score=0.0| CSF
    INJ -->|score=1.0 BLOCK 400| GW
    CSF -->|passed| PII
    CSF -->|blocked 400| GW
    PII --> POL
    POL -->|pass| AUDIT_PRE
    POL -->|deny 403| GW
    AUDIT_PRE -->|background task| AUD
    AUDIT_PRE --> FWD
    FWD -->|POST /router/route| RTR
    RTR -->|200 enriched IMF| GW

    POST_EP --> PII2
    PII2 --> AUDIT_POST
    AUDIT_POST -->|background task| AUD
    AUDIT_POST --> GW

    PROM -->|GET /metrics| MTR
```

### Key Design Decisions

**Strict pipeline ordering with short-circuit semantics.** Each pipeline stage returns a decision object. If a stage returns `block`, execution stops immediately and the error response is returned — downstream stages never execute. This is enforced structurally in `pipeline.py`, not via conditional chains scattered across handlers, making the ordering invariant auditable in one place.

**Separate ASGI app for metrics.** The Prometheus `/metrics` endpoint runs on a dedicated port (9090) as a lightweight Starlette app with no auth middleware. This is identical to the Audit Store pattern: `metrics_app.py` imports `security_layer.metrics` to ensure counters are registered in the default Prometheus registry, then mounts `make_asgi_app()` at `/metrics`. Uvicorn starts both processes separately.

**Fire-and-forget audit writes via BackgroundTask.** Both pre- and post-audit events are dispatched using FastAPI's `BackgroundTask` mechanism. The response to the caller is returned before the audit POST completes. The `audit_client.py` wraps each POST in a `try/except` with a 2-second `httpx` timeout; failures are logged as WARNING and never re-raised.

**Presidio loaded once at startup.** `AnalyzerEngine` and `AnonymizerEngine` are instantiated once during the lifespan handler and stored on `app.state.analyzer` / `app.state.anonymizer`. Per-request instantiation would add ~200 ms of model loading overhead.

**Injection patterns compiled at startup.** Patterns from `injection_patterns.yaml` are loaded, compiled as `re.compile(..., re.IGNORECASE)` objects, and stored as `app.state.patterns`. Pattern compilation is done once, not per request.

**`PII_ENABLED` flag short-circuits at the `pii.py` function boundary.** Rather than branching in every caller, the `mask_text()` and `mask_messages()` functions check the flag at entry and return immediately when disabled, so callers require no change if the flag is toggled.

---

## Components and Interfaces

### Module Layout

```
security_layer/
├── main.py               # FastAPI app factory, lifespan handler, router wiring
├── metrics_app.py        # Separate ASGI app serving /metrics on port 9090
├── config.py             # Settings loaded from environment variables (pydantic-settings)
├── models.py             # Pydantic request/response models, IMF schema, audit payloads
├── pipeline.py           # Orchestrates pre-generation and post-generation pipelines
├── injection.py          # Prompt injection detector (regex/keyword against YAML patterns)
├── content_safety.py     # Content safety filter (keyword blocklist)
├── pii.py                # Presidio AnalyzerEngine + AnonymizerEngine wrapper
├── policy.py             # Role-based policy check (static allow-list)
├── audit_client.py       # Fire-and-forget HTTP audit writer to Audit Store
├── router_client.py      # httpx async client for forwarding IMF to downstream Router
├── metrics.py            # Prometheus Counter and Histogram definitions
├── logging_config.py     # JSON structured logger factory (mirrors audit_store pattern)
└── routers/
    ├── __init__.py
    ├── pre_check.py      # POST /security/check
    ├── post_check.py     # POST /security/post-check
    └── health.py         # GET /health
```

### `config.py` — Environment-Driven Settings

```python
from pydantic_settings import BaseSettings
from typing import Literal

class Settings(BaseSettings):
    # Required — startup fails if absent or empty
    downstream_router_url: str      # DOWNSTREAM_ROUTER_URL
    audit_store_url: str            # AUDIT_STORE_URL
    audit_api_key: str              # AUDIT_API_KEY
    injection_patterns_path: str    # INJECTION_PATTERNS_PATH

    # Optional with defaults
    log_level: str = "INFO"         # LOG_LEVEL
    pii_enabled: bool = True        # PII_ENABLED (default true)

settings = Settings()
```

Startup validation (non-empty checks, file existence, pattern loading) is enforced in the lifespan handler in `main.py` before the app begins accepting requests, matching the Audit Store pattern exactly.

### `main.py` — App Factory and Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Validate required env vars are non-empty
    for field in ("downstream_router_url", "audit_store_url",
                  "audit_api_key", "injection_patterns_path"):
        if not getattr(settings, field):
            logger.error(f"{field.upper()} is not set or empty; refusing to start")
            sys.exit(1)

    # 2. Load and compile injection patterns
    patterns = load_injection_patterns(settings.injection_patterns_path)
    if patterns is None:
        sys.exit(1)  # load_injection_patterns logs the specific failure
    if len(patterns) == 0:
        logger.warning("Injection patterns list is empty; all requests pass injection check")

    # 3. Initialise Presidio engines (if PII_ENABLED)
    analyzer, anonymizer = None, None
    if settings.pii_enabled:
        try:
            analyzer = AnalyzerEngine()
            anonymizer = AnonymizerEngine()
        except Exception as exc:
            logger.error(f"Failed to initialise Presidio: {exc}; refusing to start")
            sys.exit(1)

    # 4. Store on app.state
    app.state.settings = settings
    app.state.patterns = patterns
    app.state.analyzer = analyzer
    app.state.anonymizer = anonymizer
    logger.info("Security Layer started",
                extra={"extra_fields": {"pii_enabled": settings.pii_enabled,
                                        "patterns_loaded": len(patterns)}})
    yield
    logger.info("Security Layer stopped")
```

### `injection.py` — Injection Detector

Loads patterns from YAML at startup (called by lifespan). Per-request scanning concatenates all message `content` fields with a single space and applies each compiled pattern via `re.search`.

```python
import re, yaml, pathlib
from typing import Optional

def load_injection_patterns(path: str) -> Optional[list[re.Pattern]]:
    """Load and compile patterns from YAML. Returns None on any failure."""
    try:
        data = yaml.safe_load(pathlib.Path(path).read_text())
        raw_patterns: list[str] = data.get("patterns", [])
        return [re.compile(p, re.IGNORECASE) for p in raw_patterns]
    except FileNotFoundError:
        logger.error(f"Injection patterns file not found: {path}")
    except yaml.YAMLError as e:
        logger.error(f"Malformed injection patterns YAML: {e}")
    except re.error as e:
        logger.error(f"Invalid regex in injection patterns: {e}")
    return None

def scan_for_injection(messages: list[dict], patterns: list[re.Pattern]) -> float:
    """Returns 1.0 if any pattern matches, 0.0 otherwise."""
    text = " ".join(m.get("content", "") for m in messages)
    for pattern in patterns:
        if pattern.search(text):
            return 1.0
    return 0.0
```

**Plain string entries** in the YAML are treated as literal substring patterns (i.e., compiled as-is by `re.compile`). **Entries containing regex metacharacters** are compiled as full regular expressions. The distinction is handled transparently by `re.compile` — plain strings are valid regex literals.

### `content_safety.py` — Content Safety Filter

```python
BLOCKLIST: list[str] = []  # loaded from config or hardcoded list for POC

def check_content_safety(messages: list[dict], blocklist: list[str]) -> bool:
    """Returns True (safe) if no blocklisted word found, False (unsafe) if found."""
    if not blocklist:
        logger.warning("Content safety blocklist is empty; all content passes")
        return True
    text = " ".join(m.get("content", "") for m in messages).lower()
    return not any(word.lower() in text for word in blocklist)
```

The blocklist is a simple in-memory list populated at startup. For the POC it is defined as a module-level constant or loaded from an optional env var pointing to a YAML file.

### `pii.py` — Presidio Wrapper

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

POC_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON"]
MIN_CONFIDENCE = 0.7

def mask_text(
    text: str,
    analyzer: AnalyzerEngine,
    anonymizer: AnonymizerEngine,
    pii_enabled: bool,
) -> tuple[str, list[str]]:
    """
    Returns (masked_text, detected_entity_types).
    If pii_enabled=False, returns (text, []) immediately.
    """
    if not pii_enabled or not text:
        return text, []

    results = analyzer.analyze(text=text, entities=POC_ENTITIES,
                               language="en", score_threshold=MIN_CONFIDENCE)
    if not results:
        return text, []

    operators = {
        entity: OperatorConfig("replace",
                               {"new_value": f"[REDACTED_{entity}]"})
        for entity in POC_ENTITIES
    }
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results,
                                      operators=operators)
    entity_types = list({r.entity_type for r in results})
    return anonymized.text, entity_types
```

`mask_messages()` calls `mask_text()` for each message's `content` field and aggregates the detected entity types with deduplication.

### `policy.py` — Role-Based Policy Check

```python
ALLOWED_ROLES = frozenset({"developer", "analyst", "admin"})

def check_policy(roles: list[str] | None) -> tuple[bool, str]:
    """
    Returns (permitted, decision_string).
    permitted=True if any role in roles matches ALLOWED_ROLES.
    """
    if not roles:
        return False, "role_check_deny"
    if any(r in ALLOWED_ROLES for r in roles):
        return True, "role_check_pass"
    return False, "role_check_deny"
```

### `pipeline.py` — Pipeline Orchestrator

The orchestrator is the single point of truth for stage ordering. It returns a `PipelineResult` dataclass to the route handler.

```python
@dataclass
class PipelineResult:
    blocked: bool
    block_reason: str | None      # injection_detected | content_safety_violation | policy_denied
    block_status: int | None      # 400 or 403
    imf: dict                     # enriched IMF (mutated in-place)
    latency_ms: int

async def run_pre_pipeline(imf: dict, state: AppState) -> PipelineResult:
    t0 = time.monotonic()

    # Stage 1: Injection
    score = scan_for_injection(imf["request"]["messages"], state.patterns)
    imf["governance"]["injection_score"] = score
    if score == 1.0:
        return PipelineResult(blocked=True, block_reason="injection_detected",
                              block_status=400, imf=imf,
                              latency_ms=_ms(t0))

    # Stage 2: Content Safety
    safe = check_content_safety(imf["request"]["messages"], state.blocklist)
    imf["governance"]["content_safety_passed"] = safe
    if not safe:
        return PipelineResult(blocked=True, block_reason="content_safety_violation",
                              block_status=400, imf=imf,
                              latency_ms=_ms(t0))

    # Stage 3: PII masking on request.messages
    masked_messages, entities = mask_messages(
        imf["request"]["messages"], state.analyzer, state.anonymizer,
        state.settings.pii_enabled)
    imf["request"]["messages"] = masked_messages
    imf["governance"]["pii_masked"] = len(entities) > 0
    imf["governance"]["pii_fields_detected"] = entities

    # Stage 4: Policy check
    roles = imf.get("user", {}).get("roles")
    permitted, decision = check_policy(roles)
    imf["governance"]["policy_decisions"].append(decision)
    if not permitted:
        return PipelineResult(blocked=True, block_reason="policy_denied",
                              block_status=403, imf=imf,
                              latency_ms=_ms(t0))

    # POC: human approval always not required
    imf["governance"]["human_approval_required"] = False
    imf["governance"]["human_approval_status"] = "not_required"

    return PipelineResult(blocked=False, block_reason=None, block_status=None,
                          imf=imf, latency_ms=_ms(t0))
```

### `audit_client.py` — Fire-and-Forget Audit Writer

```python
import httpx, logging

logger = logging.getLogger(__name__)

async def post_audit_event(event: dict, url: str, api_key: str) -> None:
    """Non-blocking audit write. Failures are logged as WARNING, never raised."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(
                f"{url}/audit/events",
                json=event,
                headers={"X-API-Key": api_key},
            )
        if resp.status_code >= 300:
            logger.warning("audit_write_non_2xx", extra={"extra_fields": {
                "request_id": event.get("request_id"),
                "status_code": resp.status_code,
            }})
    except httpx.TimeoutException:
        logger.warning("audit_write_timeout", extra={"extra_fields": {
            "request_id": event.get("request_id"),
        }})
    except Exception as exc:
        logger.warning("audit_write_failed", extra={"extra_fields": {
            "request_id": event.get("request_id"),
            "error": str(exc),
        }})
```

Dispatched via FastAPI `BackgroundTask` so the response to the caller is sent first.

### `router_client.py` — Downstream Router Client

```python
import httpx

ROUTER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

async def forward_to_router(imf: dict, router_url: str,
                             request_id: str) -> tuple[int, dict | None]:
    """
    Returns (status_code, response_body_dict | None).
    Raises RouterTimeoutError or RouterUnavailableError for specific failure modes.
    """
    try:
        async with httpx.AsyncClient(timeout=ROUTER_TIMEOUT) as client:
            resp = await client.post(
                f"{router_url}/router/route",
                json=imf,
                headers={"X-Request-Id": request_id},
            )
        if resp.status_code < 300:
            try:
                return resp.status_code, resp.json()
            except Exception:
                raise RouterInvalidResponseError(request_id)
        return resp.status_code, resp.json()
    except httpx.TimeoutException:
        raise RouterTimeoutError(request_id)
    except httpx.ConnectError:
        raise RouterUnavailableError(request_id)
```

### `routers/pre_check.py` — POST /security/check

```python
@router.post("/security/check")
async def pre_check(body: IMFRequest, request: Request,
                    background_tasks: BackgroundTasks):
    t0 = time.monotonic()
    imf = body.model_dump()
    request_id = imf["request_id"]

    result = await run_pre_pipeline(imf, request.app.state)

    # Build audit event regardless of outcome
    audit_event = build_pre_audit_event(imf, result, request_id)
    background_tasks.add_task(post_audit_event, audit_event,
                               request.app.state.settings.audit_store_url,
                               request.app.state.settings.audit_api_key)

    if result.blocked:
        metrics.blocks_total.labels(reason=result.block_reason).inc()
        metrics.requests_total.labels(outcome="block",
                                      check=result.block_reason).inc()
        metrics.latency.labels(endpoint="pre_check").observe(
            (time.monotonic() - t0))
        raise HTTPException(status_code=result.block_status,
                            detail={"error": result.block_reason,
                                    "request_id": request_id})

    # Forward to Router
    try:
        status, router_body = await forward_to_router(
            result.imf,
            request.app.state.settings.downstream_router_url,
            request_id,
        )
        metrics.requests_total.labels(outcome="pass",
                                      check="full_pipeline").inc()
        metrics.latency.labels(endpoint="pre_check").observe(
            (time.monotonic() - t0))
        return JSONResponse(status_code=status, content=router_body)
    except RouterTimeoutError:
        ...  # return 504
    except RouterUnavailableError:
        ...  # return 502
    except RouterInvalidResponseError:
        ...  # return 502
```

### `metrics.py` — Prometheus Definitions

```python
from prometheus_client import Counter, Histogram

requests_total = Counter(
    "llm_security_requests_total",
    "Total pre-generation pipeline requests by outcome and terminating check",
    labelnames=["outcome", "check"],
)

latency = Histogram(
    "llm_security_latency_seconds",
    "Handler latency from entry to response return",
    labelnames=["endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

pii_entities_total = Counter(
    "llm_security_pii_entities_total",
    "Count of PII entities detected, by entity type",
    labelnames=["entity_type"],
)

blocks_total = Counter(
    "llm_security_blocks_total",
    "Requests blocked at any pipeline stage, by reason",
    labelnames=["reason"],
)
```

### `logging_config.py` — Structured JSON Logger

Mirrors the Audit Store pattern exactly:

```python
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

Security-decision log entries (pre-pipeline completion, blocks, post-pipeline completion) are always emitted at INFO level regardless of `LOG_LEVEL` configuration — they are never suppressed.

### `routers/health.py` — GET /health

```python
@router.get("/health")
async def health(request: Request):
    state = request.app.state
    pii_enabled = state.settings.pii_enabled
    patterns_loaded = len(state.patterns)
    presidio_ok = (not pii_enabled) or (state.analyzer is not None)

    if presidio_ok and patterns_loaded > 0:
        return JSONResponse(status_code=200, content={
            "status": "ok",
            "pii_enabled": pii_enabled,
            "patterns_loaded": patterns_loaded,
        })

    reason = "presidio_unavailable" if not presidio_ok else "no_patterns_loaded"
    return JSONResponse(status_code=503, content={
        "status": "degraded",
        "reason": reason,
    })
```

No authentication is required on this endpoint.

---

## Data Models

### IMF Handling

The Security Layer reads and writes the platform IMF. Pydantic models are used for validation at API boundaries. The full IMF schema is defined in the master contract; the Security Layer only validates fields it actively reads.

**Fields read:**
- `request_id` — UUID-v4, validated; HTTP 422 if absent or invalid
- `user.user_id` — for audit event `user_id` field
- `user.roles` — for policy check
- `request.messages` — for injection scan, content safety, and PII masking
- `response.content` — for post-generation PII masking

**Fields written (pre-generation):**
```json
{
  "governance": {
    "injection_score": 0.0,
    "content_safety_passed": true,
    "pii_masked": true,
    "pii_fields_detected": ["EMAIL_ADDRESS"],
    "policy_decisions": ["role_check_pass"],
    "human_approval_required": false,
    "human_approval_status": "not_required"
  },
  "request": {
    "messages": "[ PII-masked version ]"
  }
}
```

**Fields written (post-generation):**
```json
{
  "governance": {
    "pii_masked": true,
    "pii_fields_detected": ["PHONE_NUMBER"]
  },
  "response": {
    "content": "[ PII-masked version of model response ]"
  }
}
```

### Pydantic Models (`models.py`)

```python
import re
from pydantic import BaseModel, Field, field_validator

UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE
)

class Message(BaseModel):
    role: str
    content: str

class UserBlock(BaseModel):
    user_id: str | None = None
    department: str | None = None
    roles: list[str] | None = None
    auth_method: str | None = None

class RequestBlock(BaseModel):
    model: str | None = None
    task_type: str | None = None
    messages: list[Message] = Field(min_length=1)
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7

class GovernanceBlock(BaseModel):
    pii_masked: bool = False
    pii_fields_detected: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list[str] = Field(default_factory=list)

class ResponseBlock(BaseModel):
    content: str | None = None
    finish_reason: str | None = None

class IMFRequest(BaseModel):
    request_id: str
    user: UserBlock | None = None
    request: RequestBlock
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)
    response: ResponseBlock | None = None
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        if not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v
```

### Audit Event Payload

Both pre- and post-audit events follow the platform Audit Record Schema. The Security Layer constructs the payload and passes it to `audit_client.post_audit_event()`.

**Pre-audit event (pass):**
```json
{
  "request_id": "...",
  "user_id": "...",
  "layer": "security",
  "event_type": "request_received",
  "outcome": "pass",
  "timestamp_utc": "2026-06-01T12:00:00.123Z",
  "latency_ms": 45,
  "pii_actions": ["EMAIL_ADDRESS"],
  "policy_decisions": ["role_check_pass"]
}
```

**Pre-audit event (block):**
```json
{
  "request_id": "...",
  "user_id": "...",
  "layer": "security",
  "event_type": "security_block",
  "outcome": "block",
  "timestamp_utc": "2026-06-01T12:00:00.050Z",
  "latency_ms": 8,
  "pii_actions": [],
  "policy_decisions": []
}
```

**Post-audit event:**
```json
{
  "request_id": "...",
  "user_id": "...",
  "layer": "security",
  "event_type": "response_sent",
  "outcome": "pass",
  "timestamp_utc": "2026-06-01T12:00:01.200Z",
  "latency_ms": 30,
  "pii_actions": ["PHONE_NUMBER"]
}
```

### Injection Patterns File (`injection_patterns.yaml`)

```yaml
patterns:
  - "ignore previous instructions"
  - "ignore all instructions"
  - "you are now"
  - "disregard your"
  - "forget your training"
  - "act as if"
  - "pretend you are"
  - "\\{\\{.*\\}\\}"
  - "<\\?.*\\?>"
```

Plain string entries are treated as literal case-insensitive substring patterns (valid regex literals). Entries with regex metacharacters are compiled as full regular expressions.

---

## Helm Chart Structure

The chart lives at `llm-platform/charts/security-layer/` and follows the platform Helm conventions with POC-appropriate overrides (`autoscaling.enabled: false`, `vault.enabled: false`, single replica).

```
llm-platform/charts/security-layer/
├── Chart.yaml
├── values.yaml
├── README.md
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── networkpolicy.yaml
    ├── servicemonitor.yaml
    ├── configmap.yaml        # injection_patterns.yaml content
    └── hpa.yaml              # autoscaling.enabled: false for POC
```

### `Chart.yaml`

```yaml
apiVersion: v2
name: security-layer
description: Security and Governance Layer for the Enterprise LLM Platform (POC)
type: application
version: 0.1.0
appVersion: "0.1.0"
```

### `values.yaml`

```yaml
replicaCount: 1

image:
  repository: registry.local/security-layer
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8081

env:
  LOG_LEVEL: "INFO"
  DOWNSTREAM_ROUTER_URL: "http://router:8082"
  PII_ENABLED: "true"
  INJECTION_PATTERNS_PATH: "/config/injection_patterns.yaml"
  # AUDIT_STORE_URL, AUDIT_API_KEY must be provided at deploy time via --set or K8s Secret

resources:
  requests:
    cpu: "200m"
    memory: "512Mi"
  limits:
    cpu: "1"
    memory: "1Gi"

observability:
  metrics:
    enabled: true
    port: 9090
  tracing:
    enabled: false
    endpoint: "http://otel-collector:4317"

autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

vault:
  enabled: false
  role: "security-layer-role"
  secretPath: "secret/llm-platform/security-layer"
```

### `templates/deployment.yaml` (key sections)

```yaml
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      containers:
        - name: security-layer
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: 8081   # application API
              name: http
            - containerPort: 9090   # Prometheus metrics
              name: metrics
          env:
            - name: LOG_LEVEL
              value: {{ .Values.env.LOG_LEVEL }}
            - name: DOWNSTREAM_ROUTER_URL
              value: {{ .Values.env.DOWNSTREAM_ROUTER_URL }}
            - name: PII_ENABLED
              value: {{ .Values.env.PII_ENABLED | quote }}
            - name: INJECTION_PATTERNS_PATH
              value: {{ .Values.env.INJECTION_PATTERNS_PATH }}
            - name: AUDIT_STORE_URL
              valueFrom:
                secretKeyRef:
                  name: security-layer-secrets
                  key: AUDIT_STORE_URL
            - name: AUDIT_API_KEY
              valueFrom:
                secretKeyRef:
                  name: security-layer-secrets
                  key: AUDIT_API_KEY
          volumeMounts:
            - name: injection-patterns
              mountPath: /config
              readOnly: true
          livenessProbe:
            httpGet:
              path: /health
              port: 8081
            initialDelaySeconds: 15
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8081
            initialDelaySeconds: 10
            periodSeconds: 10
      volumes:
        - name: injection-patterns
          configMap:
            name: {{ include "security-layer.fullname" . }}-patterns
```

### `templates/service.yaml`

Exposes two named ports on a single ClusterIP Service:
- Port 8081 named `http` — application API
- Port 9090 named `metrics` — Prometheus scraping

### `templates/networkpolicy.yaml`

```yaml
# Ingress to port 8081: llm-api-gateway namespace only
# Ingress to port 9090: llm-observability namespace only
# All other ingress: denied
# Egress: allowed to Router (8082), Audit Store (9200), OTel collector (4317)
spec:
  podSelector:
    matchLabels: {{ include "security-layer.selectorLabels" . | nindent 6 }}
  policyTypes: [Ingress, Egress]
  ingress:
    - ports: [{port: 8081}]
      from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: llm-api-gateway
    - ports: [{port: 9090}]
      from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: llm-observability
  egress:
    - ports: [{port: 8082}]     # Router
    - ports: [{port: 9200}]     # Audit Store
    - ports: [{port: 4317}]     # OTel collector (Phase 2)
    - ports: [{port: 53}, {port: 53, protocol: UDP}]  # DNS
```

### `templates/configmap.yaml`

Mounts `injection_patterns.yaml` into the container at `/config/injection_patterns.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "security-layer.fullname" . }}-patterns
data:
  injection_patterns.yaml: |
    patterns:
      - "ignore previous instructions"
      - "ignore all instructions"
      - "you are now"
      - "disregard your"
      - "forget your training"
      - "act as if"
      - "pretend you are"
      - "\\{\\{.*\\}\\}"
      - "<\\?.*\\?>"
```

### `templates/servicemonitor.yaml`

```yaml
spec:
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
  selector:
    matchLabels: {{ include "security-layer.selectorLabels" . | nindent 6 }}
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature uses **Hypothesis** (Python property-based testing library) to validate these properties with a minimum of 100 generated inputs each.

**Property reflection performed on all 14 requirements:**

After analyzing every acceptance criterion, several redundancies and consolidations were applied before finalizing the property list:

- Requirements 1.3, 2.6: JSON parse error (400) — same behavior on both endpoints. Consolidated into one property covering both.
- Requirements 1.4, 2.7: UUID-v4 validation (422) — same behavior on both endpoints. Consolidated into one property.
- Requirements 3.1 + 3.2: "injection pattern → score=1.0 → 400" — these two criteria are one logical flow. Merged into the injection detection property.
- Requirements 5.1 + 5.2 + 5.4: PII masking behavior for both request and response sides share the same masking function. The round-trip property covers both directions.
- Requirements 7.3 + 7.4 + 7.5 + 8.2 + 8.3: All fire-and-forget semantics (audit failures never propagate). Consolidated into one "audit failure isolation" property.
- Requirements 10.1 + 10.2: Health endpoint reflecting engine state is one property (pass/fail cases both tested by generating different states).
- Requirements 11.1–11.5: Metrics counter increment is one property covering monotonicity across request outcomes.
- Requirements 1.6 + 4.4 + 4.5 + 6.5: Pipeline ordering (short-circuit, stage execution order) is one consolidated pipeline ordering property.
- Requirements 6.1 + 6.2 + 6.3: Policy check covers all three with one property that generates arbitrary roles lists and verifies the pass/deny outcome.
- Requirements 6.6: POC governance fields invariant is its own property.
- Requirements 12.1–12.9: Log structure is one property.

---

### Property 1: Injection detection determines pipeline outcome

*For any* set of request messages where the concatenated content contains a match for any pattern in the loaded injection patterns list, the pre-check pipeline SHALL set `governance.injection_score` to `1.0` and return HTTP 400 with `error: "injection_detected"`. *For any* set of messages whose content contains no match for any loaded pattern, the pipeline SHALL set `governance.injection_score` to `0.0` and proceed to the content safety stage.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

---

### Property 2: Pipeline ordering invariant — short-circuit semantics

*For any* request that is blocked at the injection detection stage, the content safety filter, PII masking, and policy check stages SHALL NOT execute. *For any* request that is blocked at the content safety stage, the PII masking and policy check stages SHALL NOT execute. *For any* request that passes injection and content safety, the PII masking stage SHALL always execute before the policy check stage.

**Validates: Requirements 1.6, 4.4, 6.5**

---

### Property 3: PII masking round-trip — original PII never present in masked output

*For any* text string that contains at least one entity of type `EMAIL_ADDRESS`, `PHONE_NUMBER`, or `PERSON` with confidence ≥ 0.7, after calling `mask_text()` the returned masked string SHALL NOT contain any of the original PII token values, and the returned entity type list SHALL contain the corresponding entity type(s) as a deduplicated list. The masking function applied to both `request.messages` and `response.content` satisfies this property independently.

**Validates: Requirements 5.1, 5.2, 5.4**

---

### Property 4: IMF governance field completeness after pre-check

*For any* valid IMF that passes all pre-generation pipeline checks, the returned enriched IMF SHALL contain all seven governance fields with correct types: `injection_score` (float, 0.0 for pass), `content_safety_passed` (bool, true for pass), `pii_masked` (bool), `pii_fields_detected` (list of strings), `policy_decisions` (list containing at least one entry), `human_approval_required` (bool, always false in POC), `human_approval_status` (string, always `"not_required"` in POC).

**Validates: Requirements 1.2, 6.6**

---

### Property 5: Audit failure isolation — audit errors never propagate to caller

*For any* Audit Store response (HTTP 500, HTTP 503, connection timeout after 2 seconds, connection refused), the pre-check or post-check endpoint SHALL still return its correct response (200, 400, or 403) to the caller. The caller's response SHALL be returned before the audit POST attempt completes or times out.

**Validates: Requirements 7.3, 7.4, 7.5, 8.2, 8.3**

---

### Property 6: Injection detection is deterministic

*For any* fixed set of request messages and a fixed set of compiled injection patterns, calling `scan_for_injection()` multiple times SHALL always return the same score (0.0 or 1.0). The function has no side effects and no non-deterministic behavior.

**Validates: Requirements 3.8**

---

### Property 7: Policy denial for any roles list with no authorized role

*For any* list of role strings that contains none of `{"developer", "analyst", "admin"}` (including the empty list and None), the policy check SHALL return HTTP 403 with `error: "policy_denied"` and `reason: "insufficient_role"`. *For any* list that contains at least one of the authorized role strings (regardless of other roles present), the policy check SHALL append `"role_check_pass"` to `governance.policy_decisions` and allow the request to proceed.

**Validates: Requirements 6.1, 6.2, 6.3**

---

### Property 8: Content safety blocks any prompt containing a blocklisted word

*For any* set of request messages whose concatenated content contains a case-insensitive substring match for any word in the blocklist, the content safety filter SHALL set `governance.content_safety_passed` to `false` and the pre-check endpoint SHALL return HTTP 400 with `error: "content_safety_violation"`. *For any* prompt containing no blocklisted words, `governance.content_safety_passed` SHALL be `true`.

**Validates: Requirements 4.1, 4.2, 4.3**

---

### Property 9: Health endpoint accurately reflects engine state

*For any* combination of (Presidio initialized: bool, patterns_loaded: int), the `GET /health` endpoint SHALL return HTTP 200 with `{"status": "ok", ...}` when Presidio is initialized (or PII_ENABLED=false) AND patterns_loaded > 0, and SHALL return HTTP 503 with `{"status": "degraded", "reason": ...}` in all other cases. The `patterns_loaded` field in the 200 response SHALL equal the actual count of loaded patterns.

**Validates: Requirements 10.1, 10.2**

---

### Property 10: Metrics counters are monotonically non-decreasing

*For any* N requests processed by the pre-check or post-check endpoint, `llm_security_requests_total` (with the appropriate `outcome` and `check` labels) SHALL increase by exactly N. `llm_security_blocks_total` SHALL increase by exactly the number of blocked requests. `llm_security_latency_seconds` histogram SHALL have N new observations. Counters SHALL never decrease between requests.

**Validates: Requirements 11.2, 11.3, 11.4, 11.5**

---

### Property 11: Every log entry is a single-line JSON object with mandatory fields

*For any* operation performed by the Security Layer (request processing, blocking, startup, error), every log line emitted to stdout SHALL be parseable as a single JSON object and SHALL contain at minimum the fields `timestamp` (ISO-8601 UTC string ending with `Z`) and `level` (one of `DEBUG`, `INFO`, `WARNING`, `ERROR`). No log entry SHALL span more than one line.

**Validates: Requirements 12.1, 12.5**

---

### Property 12: Invalid request_id always rejected with HTTP 422

*For any* string value submitted as `request_id` that does not match the UUID-v4 format (including empty strings, non-UUID strings, integers cast to string, or null), both the `/security/check` and `/security/post-check` endpoints SHALL return HTTP 422 with a structured error body identifying `request_id` as the failing field. No pipeline stages SHALL execute for such requests.

**Validates: Requirements 1.4, 2.7**

---

### Property 13: Non-JSON body always rejected with HTTP 400

*For any* byte sequence submitted as the request body to `/security/check` or `/security/post-check` that is not parseable as valid JSON, both endpoints SHALL return HTTP 400. No pipeline stages or downstream calls SHALL be attempted.

**Validates: Requirements 1.3, 2.6**

---

### Property 14: PII_ENABLED=false skips all PII steps for any input

*For any* input text (regardless of whether it contains EMAIL_ADDRESS, PHONE_NUMBER, or PERSON entities), when `PII_ENABLED` is set to `false`, calling `mask_text()` or `mask_messages()` SHALL return the original unmodified text and an empty entity list, and the pre-check and post-check pipelines SHALL set `governance.pii_masked` to `false` and `governance.pii_fields_detected` to `[]`.

**Validates: Requirements 5.5**

---

## Error Handling

### Startup Failures (Non-Zero Exit)

All startup validation failures call `sys.exit(1)` after emitting an ERROR log. The lifespan handler checks these in order:

| Condition | Error message logged |
|---|---|
| `DOWNSTREAM_ROUTER_URL` absent or empty | `"DOWNSTREAM_ROUTER_URL is not set or empty; refusing to start"` |
| `AUDIT_STORE_URL` absent or empty | `"AUDIT_STORE_URL is not set or empty; refusing to start"` |
| `AUDIT_API_KEY` absent or empty | `"AUDIT_API_KEY is not set or empty; refusing to start"` |
| `INJECTION_PATTERNS_PATH` absent or empty | `"INJECTION_PATTERNS_PATH is not set or empty; refusing to start"` |
| Patterns file not found | `"Injection patterns file not found: <path>"` |
| Patterns YAML malformed | `"Malformed injection patterns YAML: <error>"` |
| Pattern entry is invalid regex | `"Invalid regex in injection patterns: <error>"` |
| Presidio initialization failure | `"Failed to initialise Presidio: <error>; refusing to start"` |

`PII_ENABLED` set to an invalid value (not `"true"` or `"false"`) also triggers startup failure. An empty patterns list emits a WARNING but does NOT cause startup failure.

### Runtime Error Responses

All error responses use a consistent structured body:

```json
{
  "error": "<error_code>",
  "request_id": "<uuid>",
  "reason": "<human_readable_description>"  // optional, present on 403
}
```

| Condition | HTTP Status | `error` value |
|---|---|---|
| Invalid JSON body | 400 | (FastAPI default `detail`) |
| Invalid/missing `request_id` | 422 | (Pydantic validation error) |
| Missing/empty `request.messages` | 422 | (Pydantic validation error) |
| Injection pattern detected | 400 | `injection_detected` |
| Content safety violation | 400 | `content_safety_violation` |
| Policy denied (insufficient role) | 403 | `policy_denied` |
| Router connection refused | 502 | `router_unavailable` |
| Router timeout | 504 | `router_timeout` |
| Router returned invalid JSON | 502 | `router_invalid_response` |
| Presidio unhandled exception during masking | 500 | `pii_processing_error` |

### Audit Client Failures (Fire-and-Forget)

Audit failures never surface to callers. The `audit_client.post_audit_event()` function catches all exceptions:

- **Timeout (> 2 s):** Log WARNING with `request_id` and `"timeout"` keyword; continue.
- **Non-2xx response:** Log WARNING with `request_id` and received status code; continue.
- **Connection refused / unreachable:** Log WARNING with `request_id` and exception string; continue.

No retry is attempted. Audit completeness for the POC is best-effort at the caller side.

### Presidio Graceful Degradation (Post-Generation Only)

If the `AnonymizerEngine` raises an unhandled exception during post-generation PII masking:
- Return HTTP 200 with the unmasked IMF.
- Set `governance.pii_masked = false`.
- Write a Post_Audit_Event with a flag indicating masking was skipped.
- Log ERROR with `request_id` and exception message.

For pre-generation PII masking, an unhandled Presidio exception returns HTTP 500 (the unmasked prompt must not reach the Router).

### Router Client Error Handling

`router_client.py` raises typed exceptions that `routers/pre_check.py` catches and maps to HTTP responses:

```python
class RouterTimeoutError(Exception): ...      # → HTTP 504
class RouterUnavailableError(Exception): ...  # → HTTP 502
class RouterInvalidResponseError(Exception):  # → HTTP 502
    ...
```

The pre-audit event written before the forwarding attempt is always retained regardless of Router failure.

---

## Testing Strategy

### Dual Testing Approach

Testing uses both **example-based unit tests** (pytest) and **property-based tests** (Hypothesis), matching the approach used for the Audit Store. Property tests run a minimum of 100 iterations per property; example tests cover concrete flows, integration scenarios, and startup conditions.

### Property-Based Tests (Hypothesis)

Each property from the Correctness Properties section is implemented as a single Hypothesis `@given` test. Tests target the pure function layer (`injection.py`, `content_safety.py`, `pii.py`, `policy.py`, `pipeline.py`) or the FastAPI test client with mocked downstream services (Audit Store, Router, Presidio).

**Configuration:**
- Minimum 100 examples per test: `@settings(max_examples=100)`
- Tag format in test docstrings: `Feature: security-governance-layer, Property N: <property_text>`

```python
# Example: Property 1 — Injection detection
from hypothesis import given, settings, strategies as st
from security_layer.injection import scan_for_injection, load_injection_patterns

@given(
    messages=st.lists(
        st.fixed_dictionaries({
            "role": st.sampled_from(["user", "assistant", "system"]),
            "content": st.text(min_size=1),
        }),
        min_size=1, max_size=5,
    )
)
@settings(max_examples=100)
def test_injection_no_match_gives_zero_score(messages):
    """
    Feature: security-governance-layer, Property 1:
    For any messages with no injection patterns, score is 0.0
    """
    patterns = []  # empty pattern list = no match possible
    score = scan_for_injection(messages, patterns)
    assert score == 0.0


@given(
    prefix=st.text(),
    suffix=st.text(),
    pattern=st.sampled_from(["ignore previous instructions", "you are now",
                              "pretend you are", "forget your training"]),
)
@settings(max_examples=100)
def test_injection_match_gives_one_score(prefix, suffix, pattern):
    """
    Feature: security-governance-layer, Property 1:
    For any messages containing an injection pattern, score is 1.0
    """
    import re
    messages = [{"role": "user", "content": f"{prefix}{pattern}{suffix}"}]
    compiled = [re.compile(pattern, re.IGNORECASE)]
    score = scan_for_injection(messages, compiled)
    assert score == 1.0
```

**Property test targets by module:**

| Property | Module Under Test | Mocking Needed |
|---|---|---|
| 1 — Injection detection | `injection.py` | None (pure function) |
| 2 — Pipeline ordering | `pipeline.py` | Mock Audit Store, Router |
| 3 — PII masking round-trip | `pii.py` | None (Presidio CPU, or mocked) |
| 4 — Governance field completeness | `pipeline.py` | Mock Audit Store, Router |
| 5 — Audit failure isolation | `routers/pre_check.py` | Mock Audit Store to fail |
| 6 — Injection determinism | `injection.py` | None (pure function) |
| 7 — Policy denial for unknown roles | `policy.py` | None (pure function) |
| 8 — Content safety blocking | `content_safety.py` | None (pure function) |
| 9 — Health state reflection | `routers/health.py` | Mock app.state |
| 10 — Metrics monotonicity | `metrics.py` + route handlers | Mock Router, Audit Store |
| 11 — Log structure invariant | `logging_config.py` | Capture stdout |
| 12 — Invalid UUID-v4 rejection | FastAPI test client | None |
| 13 — Non-JSON body rejection | FastAPI test client | None |
| 14 — PII_ENABLED=false skips masking | `pii.py` | None (pure function) |

### Example-Based Unit Tests

Unit tests cover:
- Startup validation: each missing/invalid env var causes `sys.exit(1)` with correct log output
- Injection pattern loading: YAML not found, YAML malformed, invalid regex, empty patterns list
- Presidio error during pre-generation → HTTP 500
- Presidio error during post-generation → HTTP 200 with unmasked content
- Router returns non-2xx → status relayed unchanged to caller
- Router timeout → HTTP 504 with `router_timeout`
- Router connection refused → HTTP 502 with `router_unavailable`
- Router returns empty body → HTTP 502 with `router_invalid_response`
- `user.roles` is not a list → HTTP 400
- `response.content` is null → post-check returns IMF unchanged with `pii_actions: []`
- Health returns 503 when Presidio uninitialized
- Health returns 503 when patterns list is empty
- X-API-Key header present in every audit POST (verified via mock call inspection)
- Audit POST dispatched as background task, not blocking response return

### Integration Tests

Integration tests run against the full FastAPI test client with mocked downstream services using `httpx.MockTransport` or `pytest-httpx`:

1. **Pre-check happy path:** Valid IMF with no injection, no unsafe content, no PII, authorized role → governance fields populated, Router called with enriched IMF, 200 returned.
2. **Pre-check injection block:** IMF containing injection pattern → 400, Router NOT called, audit fired.
3. **Pre-check content safety block:** IMF containing blocklisted word → 400, Router NOT called, audit fired.
4. **Pre-check policy deny:** IMF with unauthorized role → 403, Router NOT called, audit fired.
5. **Pre-check with PII:** IMF with email in message → Router receives masked IMF, `pii_masked=true`, `pii_fields_detected=["EMAIL_ADDRESS"]`.
6. **Post-check with PII in response:** IMF with PII in `response.content` → content masked, `pii_masked=true`, audit fired.
7. **Audit Store unavailable:** All audit POST calls return 503 → caller still gets correct response, WARNING logged.
8. **Router unavailable:** Connection refused → 502 with `router_unavailable`.
9. **Full platform smoke:** Sequential pre-check → get Router response → post-check → both audit events fired with matching `request_id`.

### Smoke Tests

- Service starts successfully with valid configuration
- `GET /health` returns 200 when Presidio and patterns are loaded
- `GET /health` requires no API key
- `/metrics` endpoint on port 9090 returns `Content-Type: text/plain; version=0.0.4`
- `llm_security_requests_total`, `llm_security_latency_seconds`, `llm_security_pii_entities_total`, `llm_security_blocks_total` are all present in the `/metrics` output
- `helm lint llm-platform/charts/security-layer/` passes without errors
- `helm template` renders all required Kubernetes resources

### Test Directory Structure

```
tests/
├── unit/
│   ├── test_injection.py          # Properties 1, 6
│   ├── test_content_safety.py     # Property 8
│   ├── test_pii.py                # Properties 3, 14
│   ├── test_policy.py             # Property 7
│   ├── test_pipeline.py           # Properties 2, 4
│   ├── test_audit_client.py       # Property 5
│   ├── test_logging.py            # Property 11
│   └── test_config_startup.py     # Startup smoke tests
├── integration/
│   ├── test_pre_check.py          # Properties 9, 10, 12, 13 + integration flows
│   ├── test_post_check.py         # Post-check integration flows
│   └── test_health_metrics.py     # Properties 9, 10
└── conftest.py                    # Shared fixtures: test app, mock clients
```

### Dependencies

```
# Test dependencies (requirements-test.txt)
pytest==8.3.5
pytest-asyncio==0.24.0
hypothesis==6.131.18
httpx==0.27.2          # test client transport
pytest-httpx==0.30.0   # httpx mock transport
```

---
