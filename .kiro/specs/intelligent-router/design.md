# Design Document — Intelligent Router

## Overview

The Intelligent Router (Layer 3) is a standalone FastAPI microservice that sits between the Security & Governance Layer (Layer 2) and the downstream inference backends. It runs on port 8082, with Prometheus metrics on port 9090. Every platform request passes through the Router exactly once, after all pre-generation security checks have passed.

The Router is responsible for six sequential pipeline stages: classifying the incoming request by task type, selecting the correct inference model, health-checking the selected model, consulting the Cache Layer, dispatching to the Inference Adapter if no cached response exists, then asynchronously writing the result to the cache and Audit Store before returning the completed IMF to its caller.

This is a **POC implementation**. Production-deferred features — ML-based classification, OPA policy queries, circuit breakers, A/B routing, GPU probing, MLflow integration, gRPC, and mTLS — are explicitly out of scope. The POC demonstrates correct routing decisions, cache integration, fallback behaviour, and a complete audit trail using lightweight alternatives.

**Ports:** API on 8082, Prometheus metrics on 9090.

**POC constraints in effect:** Plain HTTP JSON between services, static API key auth, rule-based keyword classification, static YAML model matrix, simple HTTP health checks, SQLite-backed Audit Store (already running on port 9200), JSON-to-stdout logging, `autoscaling.enabled: false`, `vault.enabled: false`.

---

## Architecture

The service is a single-process FastAPI application following the same structural pattern as the Security & Governance Layer and the Audit Store: a lifespan handler performs all startup validation, two ASGI apps share the same process (main app on 8082, metrics app on 9090), all significant state is loaded once at startup and stored on `app.state`, and all downstream HTTP calls use a shared `httpx.AsyncClient`.

```mermaid
graph TD
    subgraph Callers
        SL[Security Layer\nport 8081]
        LC[LangChain\nChatOpenAI client]
    end

    subgraph Router — port 8082
        RT[POST /route\nIMF endpoint]
        OAI[POST /v1/chat/completions\nOpenAI-compat endpoint]
        HLT[GET /health]

        subgraph Routing Pipeline 6 stages
            TC[1. Task Classifier\nkeyword scan YAML]
            MS[2. Model Selector\nmodel_matrix.yaml lookup]
            HC[3. Health Checker\nHTTP GET health_url 5s]
            CL[4. Cache Lookup\nPOST /cache/lookup]
            ID[5. Inference Dispatch\nPOST /infer IMF]
            BG[6. Cache Write + Audit\nBackgroundTask fire-and-forget]
        end

        FM[Fallback Manager\niterate fallback chain]
        CFG[config.py\npydantic-settings]
        LOG[logging_config.py\nJSON stdout]
    end

    subgraph Downstream
        CACHE[Cache Layer\nport 8086]
        INF[Inference Adapter\nport 8087]
        AUD[Audit Store\nport 9200]
    end

    subgraph Observability
        PROM[Prometheus Scraper]
        MTR[metrics_app.py\nport 9090]
    end

    SL -->|POST /route IMF| RT
    LC -->|POST /v1/chat/completions| OAI
    OAI -->|construct IMF| RT

    RT --> TC
    TC --> MS
    MS --> HC
    HC -->|healthy| CL
    HC -->|unhealthy| FM
    FM -->|next model| HC
    FM -->|exhausted| RT
    CL -->|HIT| BG
    CL -->|MISS| ID
    ID -->|200 OK| BG
    ID -->|error| FM
    BG -->|async| CACHE
    BG -->|async| AUD

    PROM -->|GET /metrics| MTR
```

### Key Design Decisions

**Six-stage pipeline with short-circuit semantics and explicit fallback.** The pipeline is implemented as a sequential async function in `pipeline.py`. Each stage either advances to the next or triggers the Fallback Manager. The Fallback Manager is a separate component that iterates the fallback chain and re-enters the pipeline at the health check stage. This makes the fallback path explicit and auditable, rather than scattered across try/except blocks in multiple modules.

**Separate ASGI app for metrics.** The Prometheus `/metrics` endpoint runs on a dedicated port (9090) as a lightweight Starlette app with no auth middleware. This is identical to the Security Layer pattern: `metrics_app.py` imports `intelligent_router.metrics` to ensure counters are registered in the default Prometheus registry, then mounts `make_asgi_app()` at `/metrics`. Uvicorn starts both apps separately.

**Shared `httpx.AsyncClient` created once in lifespan.** Rather than creating a new client per request, a single `httpx.AsyncClient` is created during the lifespan startup handler and stored on `app.state.http_client`. This is closed during shutdown. Per-call timeouts are set at the call site (`HEALTH_CHECK_TIMEOUT_SECONDS`, `INFERENCE_TIMEOUT_SECONDS`, 3 s for cache and audit).

**Fire-and-forget cache write and audit via BackgroundTask.** Both the cache write (after successful inference) and all audit events are dispatched using FastAPI's `BackgroundTask` mechanism. The response to the caller is returned before either completes. Both `cache_client.py` and `audit_client.py` wrap their POSTs in `try/except` with configured timeouts; failures are logged as WARNING and never re-raised.

**Config files loaded once at startup and stored on `app.state`.** Both `task_classifier_rules.yaml` and `model_matrix.yaml` are loaded and validated during the lifespan handler before the HTTP listener begins accepting connections. Loading failures call `sys.exit(1)`. This means every request handler has zero-latency access to classification rules and model topology.

**`governance.content_safety_passed` gate is the first pipeline check.** Before entering any pipeline stage, the route handler validates that `governance.content_safety_passed` is `true`. A false or missing value results in an immediate HTTP 400 with no downstream calls. This preserves the Security Layer's guarantee that no request reaches inference without passing governance checks.

**OpenAI-compatible endpoint as a thin translation layer.** The `/v1/chat/completions` handler constructs a valid IMF from the OpenAI request body, injects POC user defaults and a generated `request_id`, then passes the constructed IMF through the identical routing pipeline. The response is translated back to OpenAI format before returning. The pipeline itself has no awareness of which endpoint invoked it.

---

## Components and Interfaces

### Module Layout

```
intelligent_router/
├── main.py               # FastAPI app factory, lifespan handler, router wiring
├── metrics_app.py        # Separate ASGI app serving /metrics on port 9090
├── config.py             # Settings loaded from environment variables (pydantic-settings)
├── models.py             # Pydantic IMF models, request/response schemas, audit payloads
├── pipeline.py           # Orchestrates the 6-stage routing pipeline
├── task_classifier.py    # Keyword-based task type classification from YAML rules
├── model_selector.py     # Model matrix loading and model selection logic
├── health_checker.py     # HTTP health check per model (5-second timeout)
├── fallback_manager.py   # Fallback chain traversal and fallback_level tracking
├── cache_client.py       # Cache lookup (sync) and async cache write (fire-and-forget)
├── inference_client.py   # HTTP client for Inference Adapter (/infer endpoint)
├── audit_client.py       # Fire-and-forget audit event writer to Audit Store
├── metrics.py            # Prometheus Counter and Histogram definitions
├── logging_config.py     # JSON structured logger factory (mirrors Security Layer pattern)
└── routers/
    ├── __init__.py
    ├── route.py          # POST /route (primary IMF endpoint from Security Layer)
    ├── openai_compat.py  # POST /v1/chat/completions (OpenAI-compatible endpoint)
    └── health.py         # GET /health
```

### `config.py` — Environment-Driven Settings

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Required — startup fails if absent or empty
    model_matrix_path: str      # MODEL_MATRIX_PATH
    task_rules_path: str        # TASK_RULES_PATH
    audit_store_url: str        # AUDIT_STORE_URL

    # Optional with defaults
    cache_url: str = "http://cache:8086"              # CACHE_URL
    inference_adapter_url: str = "http://inference-adapter:8087"  # INFERENCE_ADAPTER_URL
    log_level: str = "INFO"                           # LOG_LEVEL
    inference_timeout_seconds: int = 120              # INFERENCE_TIMEOUT_SECONDS [1,600]
    health_check_timeout_seconds: int = 5             # HEALTH_CHECK_TIMEOUT_SECONDS [1,30]
    port: int = 8082                                  # PORT [1,65535]

settings = Settings()
```

Startup validation (non-empty checks, file existence, YAML parsing, range checks) is enforced in the lifespan handler in `main.py` before the app begins accepting requests.

### `main.py` — App Factory and Lifespan

```python
from contextlib import asynccontextmanager
import sys, httpx
from fastapi import FastAPI
from intelligent_router.task_classifier import load_classifier_rules
from intelligent_router.model_selector import load_model_matrix

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Validate required env vars are non-empty
    for field in ("model_matrix_path", "task_rules_path", "audit_store_url"):
        if not getattr(settings, field):
            logger.error(f"{field.upper()} is not set or empty; refusing to start")
            sys.exit(1)

    # 2. Validate numeric ranges
    if not (1 <= settings.inference_timeout_seconds <= 600):
        logger.error("INFERENCE_TIMEOUT_SECONDS out of range [1,600]; refusing to start")
        sys.exit(1)
    if not (1 <= settings.health_check_timeout_seconds <= 30):
        logger.error("HEALTH_CHECK_TIMEOUT_SECONDS out of range [1,30]; refusing to start")
        sys.exit(1)

    # 3. Load task classifier rules
    classifier_rules = load_classifier_rules(settings.task_rules_path)
    if classifier_rules is None:
        sys.exit(1)  # load_classifier_rules logs the specific failure
    if not classifier_rules.rules:
        logger.warning("Task classifier rules map is empty; all requests classified as 'chat'")

    # 4. Load model matrix
    model_matrix = load_model_matrix(settings.model_matrix_path)
    if model_matrix is None:
        sys.exit(1)  # load_model_matrix logs the specific failure

    # 5. Create shared httpx client
    http_client = httpx.AsyncClient()

    # 6. Store on app.state
    app.state.settings = settings
    app.state.classifier_rules = classifier_rules
    app.state.model_matrix = model_matrix
    app.state.http_client = http_client
    logger.info("Intelligent Router started", extra={"extra_fields": {
        "rules_loaded": classifier_rules.total_keyword_count,
        "models_loaded": len(model_matrix.models),
    }})
    yield

    # Shutdown
    await http_client.aclose()
    logger.info("Intelligent Router stopped")
```


### `task_classifier.py` — Keyword-Based Task Classifier

Loads rules from YAML at startup (called by lifespan). Per-request classification concatenates all message `content` fields with a single space, converts to lowercase, and applies each keyword rule as a case-insensitive substring search. Priority order is fixed: `code` → `reasoning` → `summarization` → `translation` → `chat`.

```python
import yaml, pathlib
from dataclasses import dataclass, field
from typing import Optional

PRIORITY_ORDER = ["code", "reasoning", "summarization", "translation", "chat"]

@dataclass
class ClassifierRules:
    rules: dict[str, list[str]]   # task_type -> list of keywords
    default: str = "chat"

    @property
    def total_keyword_count(self) -> int:
        return sum(len(kws) for kws in self.rules.values())

def load_classifier_rules(path: str) -> Optional[ClassifierRules]:
    """Load rules from YAML. Returns None on any failure."""
    try:
        data = yaml.safe_load(pathlib.Path(path).read_text())
        rules = data.get("rules", {})
        default = data.get("default", "chat")
        return ClassifierRules(rules=rules, default=default)
    except FileNotFoundError:
        logger.error(f"Task rules file not found: {path}")
    except yaml.YAMLError as e:
        logger.error(f"Malformed task rules YAML: {e}")
    return None

def classify_task(messages: list[dict], rules: ClassifierRules) -> str:
    """
    Concatenate message content fields, apply keyword rules in priority order.
    Returns the first matching task_type, or rules.default if no match.
    """
    text = " ".join(m.get("content") or "" for m in messages).lower()
    for task_type in PRIORITY_ORDER:
        keywords = rules.rules.get(task_type, [])
        if any(kw.lower() in text for kw in keywords):
            return task_type
    return rules.default
```

### `model_selector.py` — Model Matrix and Model Selection

```python
import yaml, pathlib
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ModelEntry:
    name: str
    backend: str
    endpoint: str
    tasks: list[str]
    health_url: str
    fallback: Optional[str]  # name of fallback model, or None

@dataclass
class ModelMatrix:
    models: dict[str, ModelEntry]   # model_name -> ModelEntry
    task_defaults: dict[str, str]   # task_type -> primary model_name

def load_model_matrix(path: str) -> Optional[ModelMatrix]:
    """Load model matrix from YAML. Returns None on any failure."""
    try:
        data = yaml.safe_load(pathlib.Path(path).read_text())
        raw_models = data.get("models", {})
        if not raw_models:
            logger.error(f"Model matrix 'models' map is empty: {path}; refusing to start")
            return None
        task_defaults = data.get("task_defaults", {})
        if not task_defaults:
            logger.error(f"Model matrix 'task_defaults' map is empty: {path}; refusing to start")
            return None
        models = {
            name: ModelEntry(name=name, **entry)
            for name, entry in raw_models.items()
        }
        return ModelMatrix(models=models, task_defaults=task_defaults)
    except FileNotFoundError:
        logger.error(f"Model matrix file not found: {path}")
    except yaml.YAMLError as e:
        logger.error(f"Malformed model matrix YAML: {e}")
    return None

def select_model(task_type: str, routing_mode: str, pinned_model: Optional[str],
                 matrix: ModelMatrix) -> tuple[str, str]:
    """
    Returns (selected_model_name, effective_routing_mode).
    Raises InvalidPinnedModelError for invalid pinned models.
    Raises NoModelForTaskError if task_type has no mapping and chat also missing.
    """
    if routing_mode == "pinned":
        if not pinned_model or pinned_model not in matrix.models:
            raise InvalidPinnedModelError(pinned_model)
        return pinned_model, "pinned"

    # auto mode
    primary = matrix.task_defaults.get(task_type) or matrix.task_defaults.get("chat")
    if not primary:
        raise NoModelForTaskError(task_type)
    return primary, "auto"

def get_fallback_chain(model_name: str, matrix: ModelMatrix) -> list[str]:
    """Returns ordered list of model names starting from model_name, following fallback links."""
    chain = []
    current = model_name
    visited = set()
    while current and current not in visited:
        chain.append(current)
        visited.add(current)
        entry = matrix.models.get(current)
        current = entry.fallback if entry else None
    return chain
```


### `health_checker.py` — Model Health Check

```python
import httpx

async def check_model_health(health_url: str, http_client: httpx.AsyncClient,
                              timeout_seconds: float) -> bool:
    """
    Issues GET to health_url with specified timeout.
    Returns True only for HTTP 200. All other outcomes (non-200, timeout,
    connection error, 3xx redirect) return False.
    """
    try:
        resp = await http_client.get(
            health_url,
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        return resp.status_code == 200
    except (httpx.TimeoutException, httpx.ConnectError):
        return False
```

3xx redirects are treated as failures because `follow_redirects=False` means httpx returns the 3xx response rather than following it, and a status check of `== 200` rejects it.

### `fallback_manager.py` — Fallback Chain Traversal

The Fallback Manager is a pure-logic component that operates on the fallback chain list. It is not stateful between requests; the caller passes the current fallback level and receives the next model to try.

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class FallbackState:
    chain: list[str]      # ordered list of model names to try
    current_index: int    # index of currently selected model
    fallback_level: int   # == current_index

    @property
    def selected_model(self) -> str:
        return self.chain[self.current_index]

    @property
    def has_next(self) -> bool:
        return self.current_index + 1 < len(self.chain)

    def advance(self) -> Optional[str]:
        """
        Advances to next model in chain. Returns new model name, or None if exhausted.
        Increments fallback_level by exactly 1 when advancing.
        """
        if not self.has_next:
            return None
        self.current_index += 1
        self.fallback_level += 1
        return self.chain[self.current_index]

def create_fallback_state(primary_model: str, matrix: ModelMatrix) -> FallbackState:
    """Build the fallback chain starting from the primary model."""
    chain = get_fallback_chain(primary_model, matrix)
    return FallbackState(chain=chain, current_index=0, fallback_level=0)
```

### `cache_client.py` — Cache Lookup and Async Write

```python
import httpx, logging

logger = logging.getLogger(__name__)
CACHE_TIMEOUT = 3.0

async def cache_lookup(messages: list[dict], model: str, task_type: str,
                       request_id: str, cache_url: str,
                       http_client: httpx.AsyncClient) -> dict:
    """
    POST /cache/lookup. Returns the raw response dict on success.
    On any failure (timeout, non-200, parse error), returns {"hit": False}.
    Never raises.
    """
    try:
        resp = await http_client.post(
            f"{cache_url}/cache/lookup",
            json={"messages": messages, "model": model,
                  "task_type": task_type, "request_id": request_id},
            timeout=CACHE_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning("cache_lookup_non_200", extra={"extra_fields": {
            "request_id": request_id, "status_code": resp.status_code,
        }})
    except httpx.TimeoutException:
        logger.warning("cache_lookup_timeout", extra={"extra_fields": {"request_id": request_id}})
    except Exception as exc:
        logger.warning("cache_lookup_failed", extra={"extra_fields": {
            "request_id": request_id, "error": str(exc),
        }})
    return {"hit": False}

async def cache_write(messages: list[dict], model: str, task_type: str,
                      response_imf: dict, cache_url: str,
                      http_client: httpx.AsyncClient) -> None:
    """
    Fire-and-forget POST /cache/write. Failures are logged as WARNING, never raised.
    Called via BackgroundTask — caller response has already been returned.
    """
    request_id = response_imf.get("request_id")
    try:
        resp = await http_client.post(
            f"{cache_url}/cache/write",
            json={"messages": messages, "model": model,
                  "task_type": task_type, "response_imf": response_imf},
            timeout=CACHE_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.warning("cache_write_non_200", extra={"extra_fields": {
                "request_id": request_id, "status_code": resp.status_code,
            }})
    except httpx.TimeoutException:
        logger.warning("cache_write_timeout", extra={"extra_fields": {"request_id": request_id}})
    except Exception as exc:
        logger.warning("cache_write_failed", extra={"extra_fields": {
            "request_id": request_id, "error": str(exc),
        }})
```


### `inference_client.py` — Inference Adapter Client

```python
import httpx, logging

logger = logging.getLogger(__name__)

async def call_inference(imf: dict, inference_url: str, request_id: str,
                         timeout_seconds: float,
                         http_client: httpx.AsyncClient) -> dict:
    """
    POST /infer with full IMF body. Returns the parsed response IMF on success.
    Raises InferenceError (with reason) on any failure so the pipeline can
    trigger the Fallback Manager.
    """
    try:
        resp = await http_client.post(
            f"{inference_url}/infer",
            json=imf,
            headers={"Content-Type": "application/json", "X-Request-Id": request_id},
            timeout=timeout_seconds,
        )
        if resp.status_code != 200:
            raise InferenceError(
                f"Inference returned HTTP {resp.status_code}",
                reason="non_200",
                status_code=resp.status_code,
            )
        try:
            body = resp.json()
        except Exception:
            raise InferenceError("Inference response is not valid JSON", reason="parse_error")

        # Validate response block
        response_block = body.get("response") or {}
        if not response_block.get("content"):
            raise InferenceError(
                "Inference response missing response.content",
                reason="missing_content",
            )
        return body

    except httpx.TimeoutException:
        raise InferenceError(
            f"Inference timeout after {timeout_seconds}s",
            reason="timeout",
        )
    except httpx.ConnectError:
        raise InferenceError("Inference adapter unreachable", reason="connect_error")

class InferenceError(Exception):
    def __init__(self, message: str, reason: str, status_code: int = None):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
```

### `audit_client.py` — Fire-and-Forget Audit Writer

```python
import httpx, logging

logger = logging.getLogger(__name__)
AUDIT_TIMEOUT = 2.0

async def post_audit_event(event: dict, audit_store_url: str,
                            http_client: httpx.AsyncClient) -> None:
    """Non-blocking audit write. Failures are logged as WARNING, never raised."""
    request_id = event.get("request_id")
    try:
        resp = await http_client.post(
            f"{audit_store_url}/audit/events",
            json=event,
            timeout=AUDIT_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.warning("audit_write_non_2xx", extra={"extra_fields": {
                "request_id": request_id, "status_code": resp.status_code,
            }})
    except httpx.TimeoutException:
        logger.warning("audit_write_timeout", extra={"extra_fields": {"request_id": request_id}})
    except Exception as exc:
        logger.warning("audit_write_failed", extra={"extra_fields": {
            "request_id": request_id, "error": str(exc),
        }})
```

Dispatched via FastAPI `BackgroundTask` so the response to the caller is sent before the audit POST completes.

### `pipeline.py` — Routing Pipeline Orchestrator

The orchestrator is the single point of truth for stage ordering. It processes the IMF through all six stages and returns a `PipelineResult`.

```python
import time
from dataclasses import dataclass
from fastapi import BackgroundTasks

@dataclass
class PipelineResult:
    success: bool
    status_code: int
    imf: dict
    error_code: str | None
    latency_ms: int

async def run_routing_pipeline(
    imf: dict,
    state: AppState,
    background_tasks: BackgroundTasks,
) -> PipelineResult:
    t0 = time.monotonic()
    request_id = imf["request_id"]

    # Gate: governance check before any pipeline stage
    if not imf.get("governance", {}).get("content_safety_passed"):
        return PipelineResult(success=False, status_code=400,
                              imf=imf, error_code="governance_check_failed",
                              latency_ms=_ms(t0))

    # Stage 1: Task Classification (always overwrites inbound task_type)
    imf["request"]["task_type"] = classify_task(
        imf["request"]["messages"], state.classifier_rules)

    # Stage 2: Model Selection
    routing_mode = imf.get("routing", {}).get("routing_mode") or "auto"
    pinned_model = imf.get("request", {}).get("model")
    try:
        selected_model, effective_mode = select_model(
            imf["request"]["task_type"], routing_mode, pinned_model, state.model_matrix)
    except InvalidPinnedModelError as e:
        return PipelineResult(success=False, status_code=422,
                              imf=imf, error_code="invalid_pinned_model",
                              latency_ms=_ms(t0))
    except NoModelForTaskError:
        return PipelineResult(success=False, status_code=503,
                              imf=imf, error_code="no_model_for_task",
                              latency_ms=_ms(t0))

    # Stage 3–5: Health check → Cache → Inference (with Fallback Manager)
    imf["routing"]["routing_mode"] = effective_mode
    imf["routing"]["fallback_level"] = 0
    fallback = create_fallback_state(selected_model, state.model_matrix)

    while True:
        imf["routing"]["selected_model"] = fallback.selected_model

        # Stage 3: Health Check
        healthy = await check_model_health(
            state.model_matrix.models[fallback.selected_model].health_url,
            state.http_client, state.settings.health_check_timeout_seconds)

        if not healthy:
            metrics.fallbacks_total.labels(
                task_type=imf["request"]["task_type"],
                reason="health_check_failed").inc()
            next_model = fallback.advance()
            imf["routing"]["fallback_level"] = fallback.fallback_level
            if next_model:
                logger.info("routing_fallback", extra={"extra_fields": {
                    "request_id": request_id,
                    "failed_model": fallback.chain[fallback.current_index - 1],
                    "fallback_level": fallback.fallback_level,
                    "reason": "health_check_failed",
                }})
                background_tasks.add_task(
                    post_audit_event,
                    _build_fallback_audit(imf, fallback, "fallback", _ms(t0)),
                    state.settings.audit_store_url, state.http_client)
                continue
            else:
                background_tasks.add_task(
                    post_audit_event,
                    _build_routing_audit(imf, "error", _ms(t0)),
                    state.settings.audit_store_url, state.http_client)
                return PipelineResult(success=False, status_code=503,
                                      imf=imf, error_code="all_backends_exhausted",
                                      latency_ms=_ms(t0))

        # Stage 4: Cache Lookup
        cache_response = await cache_lookup(
            imf["request"]["messages"], fallback.selected_model,
            imf["request"]["task_type"], request_id,
            state.settings.cache_url, state.http_client)

        imf["cache"]["lookup_hit"] = bool(cache_response.get("hit"))
        imf["cache"]["cache_key"] = cache_response.get("cache_key")

        if cache_response.get("hit"):
            resp = cache_response.get("response") or {}
            if not resp.get("content"):
                # Invalid cache entry — treat as MISS
                imf["cache"]["lookup_hit"] = False
            else:
                imf["response"] = {
                    "content": resp.get("content"),
                    "finish_reason": resp.get("finish_reason"),
                    "usage": resp.get("usage", {"prompt_tokens": 0,
                                               "completion_tokens": 0, "total_tokens": 0}),
                }
                metrics.cache_hits_total.labels(
                    task_type=imf["request"]["task_type"],
                    model=fallback.selected_model).inc()
                background_tasks.add_task(
                    post_audit_event,
                    _build_cache_hit_audit(imf, _ms(t0)),
                    state.settings.audit_store_url, state.http_client)
                return PipelineResult(success=True, status_code=200,
                                      imf=imf, error_code=None, latency_ms=_ms(t0))

        # Stage 5: Inference Dispatch
        try:
            result_imf = await call_inference(
                imf, state.settings.inference_adapter_url,
                request_id, state.settings.inference_timeout_seconds,
                state.http_client)

            # Stage 6a: Async cache write
            background_tasks.add_task(
                cache_write,
                imf["request"]["messages"], fallback.selected_model,
                imf["request"]["task_type"], result_imf,
                state.settings.cache_url, state.http_client)

            # Stage 6b: Async audit log
            background_tasks.add_task(
                post_audit_event,
                _build_routing_audit(result_imf, "pass", _ms(t0)),
                state.settings.audit_store_url, state.http_client)

            return PipelineResult(success=True, status_code=200,
                                  imf=result_imf, error_code=None, latency_ms=_ms(t0))

        except InferenceError as e:
            metrics.fallbacks_total.labels(
                task_type=imf["request"]["task_type"],
                reason="inference_error").inc()
            next_model = fallback.advance()
            imf["routing"]["fallback_level"] = fallback.fallback_level
            if next_model:
                logger.warning("inference_error_fallback", extra={"extra_fields": {
                    "request_id": request_id,
                    "selected_model": fallback.chain[fallback.current_index - 1],
                    "reason": e.reason, "status_code": e.status_code,
                }})
                background_tasks.add_task(
                    post_audit_event,
                    _build_fallback_audit(imf, fallback, "fallback", _ms(t0)),
                    state.settings.audit_store_url, state.http_client)
                continue
            else:
                background_tasks.add_task(
                    post_audit_event,
                    _build_routing_audit(imf, "error", _ms(t0)),
                    state.settings.audit_store_url, state.http_client)
                return PipelineResult(success=False, status_code=503,
                                      imf=imf, error_code="all_backends_exhausted",
                                      latency_ms=_ms(t0))

def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
```


### `routers/route.py` — POST /route

```python
@router.post("/route")
async def route_imf(body: IMFRequest, request: Request,
                    background_tasks: BackgroundTasks):
    imf = body.model_dump()
    request_id = imf["request_id"]

    result = await run_routing_pipeline(imf, request.app.state, background_tasks)

    if result.success:
        metrics.requests_total.labels(
            outcome=_outcome_label(imf),
            task_type=imf["request"]["task_type"],
            routing_mode=imf["routing"]["routing_mode"]).inc()
        metrics.latency.labels(
            task_type=imf["request"]["task_type"],
            routing_mode=imf["routing"]["routing_mode"]).observe(result.latency_ms / 1000)
        logger.info("routing_decision", extra={"extra_fields": {
            "request_id": request_id,
            "task_type": imf["request"]["task_type"],
            "selected_model": imf["routing"]["selected_model"],
            "routing_mode": imf["routing"]["routing_mode"],
            "cache_hit": imf["cache"]["lookup_hit"],
            "fallback_level": imf["routing"]["fallback_level"],
            "outcome": "success",
            "latency_ms": result.latency_ms,
        }})
        return JSONResponse(status_code=200, content=result.imf)

    metrics.errors_total.labels(error_code=result.error_code).inc()
    error_body = {"error": result.error_code, "request_id": request_id}
    if result.error_code == "all_backends_exhausted":
        error_body["fallback_level"] = imf["routing"]["fallback_level"]
    if result.error_code == "invalid_pinned_model":
        error_body["model"] = imf.get("request", {}).get("model")
    return JSONResponse(status_code=result.status_code, content=error_body)
```

### `routers/openai_compat.py` — POST /v1/chat/completions

```python
import uuid, time as time_mod

@router.post("/v1/chat/completions")
async def openai_chat_completions(body: OpenAIChatRequest, request: Request,
                                   background_tasks: BackgroundTasks):
    # Validate messages
    if not body.messages:
        return JSONResponse(status_code=422, content={
            "error": {"code": 422, "message": "messages array is required and must be non-empty"}
        })

    # Construct IMF from OpenAI request
    routing_mode = "pinned" if body.model else "auto"
    imf = {
        "request_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "user": {
            "user_id": "poc-user",
            "department": "poc",
            "roles": ["developer"],
            "auth_method": "api_key",
        },
        "request": {
            "model": body.model,
            "task_type": None,
            "messages": [m.model_dump() for m in body.messages],
            "max_tokens": body.max_tokens or 2048,
            "temperature": body.temperature or 0.7,
        },
        "governance": {
            "content_safety_passed": True,
            "pii_masked": False, "pii_fields_detected": [],
            "injection_score": 0.0, "jailbreak_score": 0.0,
            "human_approval_required": False,
            "human_approval_status": "not_required",
            "policy_decisions": [],
        },
        "routing": {"selected_model": None, "routing_mode": routing_mode, "fallback_level": 0},
        "cache": {"lookup_hit": False, "cache_key": None},
        "response": {"content": None, "finish_reason": None,
                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
        "metadata": {}, "extensions": {},
    }

    result = await run_routing_pipeline(imf, request.app.state, background_tasks)

    if result.success:
        resp_imf = result.imf
        usage = resp_imf["response"].get("usage") or {}
        return JSONResponse(status_code=200, content={
            "id": resp_imf["request_id"],
            "object": "chat.completion",
            "created": int(time_mod.time()),
            "model": resp_imf["routing"]["selected_model"],
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": resp_imf["response"]["content"],
                },
                "finish_reason": resp_imf["response"].get("finish_reason") or "stop",
                "index": 0,
            }],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        })

    return JSONResponse(status_code=result.status_code, content={
        "error": {
            "code": result.status_code,
            "message": result.error_code,
            "type": "service_unavailable",
        }
    })
```

### `routers/health.py` — GET /health

```python
@router.get("/health")
async def health(request: Request):
    state = request.app.state
    rules = getattr(state, "classifier_rules", None)
    matrix = getattr(state, "model_matrix", None)

    if rules is not None and matrix is not None:
        return JSONResponse(status_code=200, content={
            "status": "ok",
            "rules_loaded": rules.total_keyword_count,
            "models_loaded": len(matrix.models),
        })

    reason = "rules_load_failed" if rules is None else "matrix_load_failed"
    return JSONResponse(status_code=503, content={
        "status": "degraded",
        "reason": reason,
    })
```

No authentication is required on this endpoint. No downstream calls are made.

### `metrics.py` — Prometheus Definitions

```python
from prometheus_client import Counter, Histogram

requests_total = Counter(
    "llm_router_requests_total",
    "Total routing pipeline invocations by outcome, task_type, and routing_mode",
    labelnames=["outcome", "task_type", "routing_mode"],
)

latency = Histogram(
    "llm_router_latency_seconds",
    "End-to-end wall-clock latency of the routing pipeline",
    labelnames=["task_type", "routing_mode"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 120.0],
)

cache_hits_total = Counter(
    "llm_router_cache_hits_total",
    "Cache HITs returned (inference skipped), by task_type and model",
    labelnames=["task_type", "model"],
)

fallbacks_total = Counter(
    "llm_router_fallbacks_total",
    "Fallback Manager advances, by task_type and reason",
    labelnames=["task_type", "reason"],
)

errors_total = Counter(
    "llm_router_errors_total",
    "Non-200 routing pipeline outcomes, by error_code",
    labelnames=["error_code"],
)
```

### `logging_config.py` — Structured JSON Logger

Mirrors the Security Layer and Audit Store pattern exactly:

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

The routing-decision log entry (emitted after every pipeline completion) is always emitted at INFO level regardless of `LOG_LEVEL` configuration — it is never suppressed.

---

## Data Models

### IMF Handling

The Router reads and writes the platform IMF. Pydantic models are used for validation at API boundaries.

**Fields read from inbound IMF:**
- `request_id` — UUID-v4, validated; HTTP 422 if absent or invalid
- `request.messages` — for task classification and cache lookup/write
- `request.model` — for pinned mode validation
- `user.department` — included in audit events
- `governance.content_safety_passed` — must be `true` to proceed; HTTP 400 if `false` or absent
- `routing.routing_mode` — determines `auto` vs `pinned` selection

**Fields written by the Router (WRITE_SET):**
```json
{
  "request": { "task_type": "code" },
  "routing": {
    "selected_model": "llama3.2-3b",
    "routing_mode": "auto",
    "fallback_level": 0
  },
  "cache": {
    "lookup_hit": false,
    "cache_key": "sha256-abc123"
  }
}
```

**Additional fields written on cache HIT:**
```json
{
  "response": {
    "content": "cached response text",
    "finish_reason": "stop",
    "usage": { "prompt_tokens": 10, "completion_tokens": 25, "total_tokens": 35 }
  }
}
```

The Router does **not** set any fields in `governance` or `user`. On cache MISS + inference success, the Inference_Adapter's returned IMF (with its populated `response` block) is used as-is.

### Pydantic Models (`models.py`)

```python
import re, uuid
from pydantic import BaseModel, Field, field_validator
from typing import Optional

UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

class Message(BaseModel):
    role: str
    content: str

class UserBlock(BaseModel):
    user_id: Optional[str] = None
    department: Optional[str] = None
    roles: Optional[list[str]] = None
    auth_method: Optional[str] = None

class RequestBlock(BaseModel):
    model: Optional[str] = None
    task_type: Optional[str] = None
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

class RoutingBlock(BaseModel):
    selected_model: Optional[str] = None
    routing_mode: str = "auto"
    fallback_level: int = 0

class CacheBlock(BaseModel):
    lookup_hit: bool = False
    cache_key: Optional[str] = None

class UsageBlock(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ResponseBlock(BaseModel):
    content: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: UsageBlock = Field(default_factory=UsageBlock)

class IMFRequest(BaseModel):
    request_id: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    timestamp_utc: Optional[str] = None
    user: Optional[UserBlock] = None
    request: RequestBlock
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)
    routing: RoutingBlock = Field(default_factory=RoutingBlock)
    cache: CacheBlock = Field(default_factory=CacheBlock)
    response: Optional[ResponseBlock] = None
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        if not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v

# OpenAI-compatible request body for /v1/chat/completions
class OpenAIChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: bool = False
```

### Config YAML Examples

**`task_classifier_rules.yaml`** (mounted at `/config/task_classifier_rules.yaml`):

```yaml
rules:
  code:
    keywords:
      - "code"
      - "function"
      - "python"
      - "javascript"
      - "debug"
      - "write a script"
      - "implement"
  reasoning:
    keywords:
      - "reason"
      - "think step by step"
      - "math"
      - "calculate"
      - "prove"
      - "analyze"
  summarization:
    keywords:
      - "summarize"
      - "summary"
      - "tldr"
      - "shorten"
      - "condense"
  translation:
    keywords:
      - "translate"
      - "in french"
      - "in spanish"
      - "in german"
      - "en español"
default: chat
```

**`model_matrix.yaml`** (mounted at `/config/model_matrix.yaml`):

```yaml
models:
  llama3.2-3b:
    backend: ollama
    endpoint: "http://inference-ollama:11434"
    tasks:
      - chat
      - summarization
      - reasoning
      - code
    health_url: "http://inference-ollama:11434/api/tags"
    fallback: null

task_defaults:
  chat: llama3.2-3b
  code: llama3.2-3b
  reasoning: llama3.2-3b
  summarization: llama3.2-3b
  translation: llama3.2-3b
  embeddings: llama3.2-3b
```

### Audit Event Payloads

**Routing decision — success:**
```json
{
  "request_id": "...",
  "user_id": "...",
  "department": "...",
  "model_used": "llama3.2-3b",
  "layer": "router",
  "event_type": "inference_complete",
  "outcome": "pass",
  "latency_ms": 340,
  "timestamp_utc": "2026-06-01T12:00:01.340Z"
}
```

**Routing decision — error (503/500):**
```json
{
  "request_id": "...",
  "model_used": "llama3.2-3b",
  "layer": "router",
  "event_type": "inference_start",
  "outcome": "error",
  "latency_ms": 5120,
  "timestamp_utc": "2026-06-01T12:00:06.120Z"
}
```

**Cache hit:**
```json
{
  "request_id": "...",
  "model_used": "llama3.2-3b",
  "layer": "router",
  "event_type": "cache_hit",
  "outcome": "pass",
  "latency_ms": 18,
  "timestamp_utc": "2026-06-01T12:00:00.018Z"
}
```

**Fallback advance:**
```json
{
  "request_id": "...",
  "model_used": "llama3.2-3b",
  "layer": "router",
  "event_type": "inference_start",
  "outcome": "fallback",
  "fallback_level": 1,
  "latency_ms": 5100,
  "timestamp_utc": "2026-06-01T12:00:05.100Z"
}
```

---

## Helm Chart Structure

The chart lives at `llm-platform/charts/router/` and follows the platform Helm conventions with POC-appropriate overrides (`autoscaling.enabled: false`, `vault.enabled: false`, `networkPolicy.enabled: false`, single replica).

```
llm-platform/charts/router/
├── Chart.yaml
├── values.yaml
├── README.md
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml        # model_matrix.yaml + task_classifier_rules.yaml
    ├── networkpolicy.yaml    # disabled by default for POC
    ├── servicemonitor.yaml   # Prometheus ServiceMonitor on port 9090
    └── hpa.yaml              # autoscaling.enabled: false for POC
```

### `Chart.yaml`

```yaml
apiVersion: v2
name: router
description: Intelligent Router Layer 3 for the Enterprise LLM Platform (POC)
type: application
version: 0.1.0
appVersion: "0.1.0"
```

### `values.yaml`

```yaml
replicaCount: 1

image:
  repository: registry.local/router
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8082

env:
  LOG_LEVEL: "INFO"
  MODEL_MATRIX_PATH: "/config/model_matrix.yaml"
  TASK_RULES_PATH: "/config/task_classifier_rules.yaml"
  CACHE_URL: "http://cache:8086"
  INFERENCE_ADAPTER_URL: "http://inference-adapter:8087"
  AUDIT_STORE_URL: "http://audit-store:9200"
  INFERENCE_TIMEOUT_SECONDS: "120"
  HEALTH_CHECK_TIMEOUT_SECONDS: "5"
  # PORT defaults to 8082 in the application

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
  role: "router-role"
  secretPath: "secret/llm-platform/router"

networkPolicy:
  enabled: false   # Set to true in production to restrict ingress to security-layer only
```

### `templates/configmap.yaml`

Mounts both config files into `/config/` in the container:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "router.fullname" . }}-config
data:
  model_matrix.yaml: |
    models:
      llama3.2-3b:
        backend: ollama
        endpoint: "http://inference-ollama:11434"
        tasks: [chat, summarization, reasoning, code]
        health_url: "http://inference-ollama:11434/api/tags"
        fallback: null
    task_defaults:
      chat: llama3.2-3b
      code: llama3.2-3b
      reasoning: llama3.2-3b
      summarization: llama3.2-3b
      translation: llama3.2-3b
      embeddings: llama3.2-3b
  task_classifier_rules.yaml: |
    rules:
      code:
        keywords: ["code", "function", "python", "javascript", "debug", "implement"]
      reasoning:
        keywords: ["reason", "think step by step", "math", "calculate", "prove", "analyze"]
      summarization:
        keywords: ["summarize", "summary", "tldr", "shorten", "condense"]
      translation:
        keywords: ["translate", "in french", "in spanish", "in german"]
    default: chat
```

### `templates/deployment.yaml` (key sections)

```yaml
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      containers:
        - name: router
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: 8082
              name: http
            - containerPort: 9090
              name: metrics
          env:
            - name: LOG_LEVEL
              value: {{ .Values.env.LOG_LEVEL }}
            - name: MODEL_MATRIX_PATH
              value: {{ .Values.env.MODEL_MATRIX_PATH }}
            - name: TASK_RULES_PATH
              value: {{ .Values.env.TASK_RULES_PATH }}
            - name: CACHE_URL
              value: {{ .Values.env.CACHE_URL }}
            - name: INFERENCE_ADAPTER_URL
              value: {{ .Values.env.INFERENCE_ADAPTER_URL }}
            - name: AUDIT_STORE_URL
              value: {{ .Values.env.AUDIT_STORE_URL }}
            - name: INFERENCE_TIMEOUT_SECONDS
              value: {{ .Values.env.INFERENCE_TIMEOUT_SECONDS | quote }}
            - name: HEALTH_CHECK_TIMEOUT_SECONDS
              value: {{ .Values.env.HEALTH_CHECK_TIMEOUT_SECONDS | quote }}
          volumeMounts:
            - name: router-config
              mountPath: /config
              readOnly: true
          livenessProbe:
            httpGet:
              path: /health
              port: 8082
            initialDelaySeconds: 15
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8082
            initialDelaySeconds: 15
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 3
      volumes:
        - name: router-config
          configMap:
            name: {{ include "router.fullname" . }}-config
```

### `templates/service.yaml`

Exposes two named ports on a single ClusterIP Service:
- Port 8082 named `http` — application API
- Port 9090 named `metrics` — Prometheus scraping

### `templates/networkpolicy.yaml`

Disabled by default for POC (`networkPolicy.enabled: false`). When enabled in production:

```yaml
# Ingress to port 8082: pods with label app.kubernetes.io/name: security-layer only
# Ingress to port 9090: observability namespace only
# All other ingress: denied
# Egress: allowed to Cache (8086), Inference Adapter (8087), Audit Store (9200), DNS (53)
spec:
  podSelector:
    matchLabels: {{ include "router.selectorLabels" . | nindent 6 }}
  policyTypes: [Ingress, Egress]
  ingress:
    - ports: [{port: 8082}]
      from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: security-layer
    - ports: [{port: 9090}]
      from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
  egress:
    - ports: [{port: 8086}]    # Cache Layer
    - ports: [{port: 8087}]    # Inference Adapter
    - ports: [{port: 9200}]    # Audit Store
    - ports: [{port: 53}, {port: 53, protocol: UDP}]  # DNS
```

### `templates/servicemonitor.yaml`

```yaml
spec:
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
  selector:
    matchLabels: {{ include "router.selectorLabels" . | nindent 6 }}
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature uses **Hypothesis** (Python property-based testing library) to validate these properties with a minimum of 100 generated inputs each.

**Property reflection performed on all acceptance criteria across Requirements 1–16:**

After analyzing every acceptance criterion, several consolidations were applied:

- Requirements 2.1 + 2.2 + 2.3: Both sides of classification (keyword match and default) are kept as two separate properties since they test mutually exclusive input conditions.
- Requirements 3.7 + 3.8 feed into the fallback monotonicity property rather than standing alone.
- Requirements 4.3 + 4.4 + 4.5 + 6.3 + 6.4 (all fallback-triggering conditions): Consolidated into the fallback monotonicity property.
- Requirements 5.2 + 5.3 + 6.1 + 7.1 + 7.4 (all cache/inference routing decisions): Consolidated into the cache lookup result consistency property.
- Requirements 8.1 + 8.6 (audit success and audit failure isolation): Kept as two separate properties — one tests that audit IS fired, the other tests that audit failures never surface to the caller.
- Requirements 11.1 + 11.2 + 11.6 (IMF field preservation from both directions, governance immutability): Unified into one property.
- Requirements 12.2–12.6 (all metrics counters): Consolidated into one monotonicity property.
- Requirements 13.1–13.5 (all log structure requirements): Consolidated into one log structure property.
- Properties 1 and 2 (task classification, both directions): These are distinct because generators must produce different input classes; kept separate.

---

### Property 1: Task Classification — Keyword Match Invariant

*For any* non-empty message list where the concatenated content contains at least one keyword from `task_classifier_rules.yaml`, the Task_Classifier always returns a `task_type` corresponding to the highest-priority rule whose keyword is present, where priority order is `code` → `reasoning` → `summarization` → `translation` → `chat`. Case of the keyword in the message does not affect the result.

**Validates: Requirements 2.1, 2.3, 2.4**

---

### Property 2: Task Classification — Default Invariant

*For any* message list whose concatenated content contains no keyword from any rule in `task_classifier_rules.yaml` (including messages with empty content, null content, and whitespace-only content), the Task_Classifier always returns `"chat"`.

**Validates: Requirements 2.2**

---

### Property 3: Model Selection — Selected Model Always in Matrix

*For any* valid `task_type` value and `routing_mode = "auto"` (including task types with no explicit entry in `task_defaults` that must fall back to the `chat` default), the model selector always returns a `selected_model` name that is a key present in the `models` map of the Model_Matrix, and the returned `routing_mode` is always `"auto"`. For `routing_mode = "pinned"` with a valid model name present in the matrix, the returned `selected_model` always equals the pinned model name exactly.

**Validates: Requirements 3.1, 3.2, 3.6**

---

### Property 4: IMF Field Preservation Invariant

*For any* valid inbound IMF document with arbitrary values in all non-write-set fields, after the routing pipeline executes (with mocked Cache_Layer returning MISS and mocked Inference_Adapter echoing the IMF back with a populated `response` block), every field not in the WRITE_SET is byte-identical to its inbound value. The Router never sets any field in `governance` or `user`.

Where `WRITE_SET = {request.task_type, routing.selected_model, routing.routing_mode, routing.fallback_level, cache.lookup_hit, cache.cache_key}`.

**Validates: Requirements 11.1, 11.2, 11.6**

---

### Property 5: Fallback Level Monotonicity

*For any* routing attempt with a model matrix containing a fallback chain of length N, and with health checks or inference calls mocked to fail for the first K models in the chain (where 0 ≤ K < N), the final `routing.fallback_level` in the returned IMF equals exactly K. The fallback level never decreases during a single request and never exceeds the fallback chain length. When all N models fail (K = N), the Router returns HTTP 503 with `fallback_level = N`.

**Validates: Requirements 3.7, 3.8, 4.3, 4.4, 4.5, 4.7, 6.3, 6.4**

---

### Property 6: OpenAI Compatibility — Response Shape Invariant

*For any* valid OpenAI-format request body with a non-empty `messages` array (varying message counts, content lengths, presence/absence of the `model` field), the `/v1/chat/completions` endpoint always returns a JSON response body that conforms to the OpenAI chat completions schema: containing `id` (non-empty string), `object` (equals `"chat.completion"`), `model` (non-null string), `choices` (a non-empty array where `choices[0].message.role = "assistant"` and `choices[0].message.content` is a non-null non-empty string, and `choices[0].finish_reason` is a non-null string), and `usage` (an object with non-negative integer fields `prompt_tokens`, `completion_tokens`, `total_tokens`). For error responses (non-200), the body always conforms to the OpenAI error schema with `error.code`, `error.message`, and `error.type`.

**Validates: Requirements 9.2, 9.5**

---

### Property 7: Cache Lookup Result Consistency

*For any* valid IMF routed through the pipeline, when the mocked Cache_Layer returns `{"hit": true, "response": R}` with a non-null `response.content`, the pipeline returns an IMF with `cache.lookup_hit = true` and `response.content` equal to `R.content`, and the Inference_Adapter is never called. When the mocked Cache_Layer returns `{"hit": false}`, the pipeline sets `cache.lookup_hit = false` and calls the Inference_Adapter exactly once. When the mocked Cache_Layer fails (timeout, non-200, connection error), the pipeline sets `cache.lookup_hit = false` and calls the Inference_Adapter exactly once. These invariants hold regardless of `routing_mode` (auto or pinned).

**Validates: Requirements 5.2, 5.3, 5.4, 5.6, 6.1, 7.1, 7.4, 11.3, 11.5**

---

### Property 8: Audit Failure Isolation

*For any* routing pipeline invocation (success, cache hit, or error), when the Audit_Store returns any failure response (HTTP 500, HTTP 503, connection timeout after 2 seconds, connection refused), the `/route` and `/v1/chat/completions` endpoints still return their correct response (200 with IMF, or the appropriate error status). The caller response is returned before the audit POST attempt completes or times out. Audit failures are never surfaced to the caller.

**Validates: Requirements 8.5, 8.6**

---

### Property 9: Health State Accurately Reflects Loaded Configuration

*For any* combination of (classifier_rules loaded: bool, model_matrix loaded: bool), the `GET /health` endpoint returns HTTP 200 with `{"status": "ok", "rules_loaded": N, "models_loaded": M}` when both are loaded, where `rules_loaded` equals the exact total keyword count across all task types and `models_loaded` equals the exact count of entries in the `models` map. When either fails to load, `GET /health` returns HTTP 503 with `{"status": "degraded", "reason": <specific_reason>}`.

**Validates: Requirements 10.1, 10.2**

---

### Property 10: Metrics Counters Are Monotonically Non-Decreasing

*For any* N requests processed by the routing pipeline (varying outcomes: cache_hit, inference_success, fallback_success, error), `llm_router_requests_total` (with appropriate `outcome`, `task_type`, and `routing_mode` labels) increases by exactly N. `llm_router_cache_hits_total` increases by exactly the number of cache-hit responses. `llm_router_fallbacks_total` increases by exactly the number of fallback advances. `llm_router_errors_total` increases by exactly the number of error responses. `llm_router_latency_seconds` accumulates exactly N new observations. No counter ever decreases between requests.

**Validates: Requirements 12.2, 12.3, 12.4, 12.5, 12.6**

---

### Property 11: Every Log Entry Is a Single-Line JSON Object With Mandatory Fields

*For any* routing pipeline operation (request processing, fallback, cache hit, startup, error), every log line emitted to stdout is parseable as a single JSON object and contains at minimum the fields `timestamp` (ISO-8601 UTC string ending with `Z`) and `level` (one of `DEBUG`, `INFO`, `WARNING`, `ERROR`). No log entry spans more than one line. The routing-decision log entry always contains `request_id`, `task_type`, `selected_model`, `routing_mode`, `cache_hit`, `fallback_level`, `outcome`, and `latency_ms`.

**Validates: Requirements 13.1, 13.2, 13.5**

---

## Error Handling

### Startup Failures (Non-Zero Exit)

All startup validation failures call `sys.exit(1)` after emitting an ERROR log. The lifespan handler checks these in order:

| Condition | Error message logged |
|---|---|
| `MODEL_MATRIX_PATH` absent or empty | `"MODEL_MATRIX_PATH is not set or empty; refusing to start"` |
| `TASK_RULES_PATH` absent or empty | `"TASK_RULES_PATH is not set or empty; refusing to start"` |
| `AUDIT_STORE_URL` absent or empty | `"AUDIT_STORE_URL is not set or empty; refusing to start"` |
| `INFERENCE_TIMEOUT_SECONDS` outside `[1, 600]` | `"INFERENCE_TIMEOUT_SECONDS out of range [1,600]; refusing to start"` |
| `HEALTH_CHECK_TIMEOUT_SECONDS` outside `[1, 30]` | `"HEALTH_CHECK_TIMEOUT_SECONDS out of range [1,30]; refusing to start"` |
| `PORT` not valid integer or outside `[1, 65535]` | `"PORT is not a valid integer or out of range [1,65535]; refusing to start"` |
| Model matrix file not found | `"Model matrix file not found: <path>"` |
| Model matrix YAML malformed | `"Malformed model matrix YAML: <error>"` |
| Model matrix `models` map empty | `"Model matrix 'models' map is empty; refusing to start"` |
| Model matrix `task_defaults` map empty | `"Model matrix 'task_defaults' map is empty; refusing to start"` |
| Task rules file not found | `"Task rules file not found: <path>"` |
| Task rules YAML malformed | `"Malformed task rules YAML: <error>"` |

An empty `rules` map in `task_classifier_rules.yaml` emits a WARNING but does NOT cause startup failure (all requests will classify as `"chat"`).

### Runtime Error Responses

All error responses use a consistent structured body. The `request_id` is included when available (absent for JSON parse errors):

```json
{
  "error": "<error_code>",
  "request_id": "<uuid | null>"
}
```

| Condition | HTTP Status | `error` value |
|---|---|---|
| Invalid JSON body | 400 | `"invalid_json"` |
| Invalid/missing `request_id` | 422 | `"validation_error"` |
| Missing/empty `request.messages` | 422 | `"validation_error"` |
| `governance.content_safety_passed` is false or absent | 400 | `"governance_check_failed"` |
| Pinned model absent/invalid | 422 | `"invalid_pinned_model"` |
| No model entry for task_type (and no `chat` default) | 503 | `"no_model_for_task"` |
| All backends exhausted | 503 | `"all_backends_exhausted"` |
| Unhandled exception during pipeline | 500 | `"internal_error"` |

For the `all_backends_exhausted` error, the response includes `"fallback_level": <n>`.
For the `invalid_pinned_model` error, the response includes `"model": "<value or null>"`.

OpenAI endpoint errors wrap the above in the OpenAI error envelope:
```json
{"error": {"code": <status_code>, "message": "<error_code>", "type": "service_unavailable"}}
```

### Downstream Service Failure Handling

**Cache Layer failures (lookup):** Any failure (non-200, timeout after 3 s, connection error) is treated as a MISS: `cache.lookup_hit = false`, `cache.cache_key = null`, WARNING logged, inference proceeds. The caller never receives an error due to cache unavailability.

**Cache Layer failures (write):** Fire-and-forget background task. Any failure (non-200, timeout after 3 s, connection error) logs a WARNING with `request_id`; the failure is never propagated to the caller.

**Inference Adapter failures:** Non-200, timeout (per `INFERENCE_TIMEOUT_SECONDS`), empty response body, invalid JSON, or missing `response.content` all trigger the Fallback Manager. A WARNING is logged with `request_id`, `selected_model`, and the failure reason before each fallback advance.

**Health check failures:** Non-200, timeout (per `HEALTH_CHECK_TIMEOUT_SECONDS`), connection error, or 3xx redirect all trigger the Fallback Manager. A structured `routing_fallback` log entry is emitted before advancing.

**Audit Store failures:** Any failure (non-200, timeout after 2 s, connection error) logs a WARNING with `request_id`; the failure is never propagated to the caller. No retry is attempted.

---

## Testing Strategy

### Dual Testing Approach

Testing uses both **example-based unit tests** (pytest) and **property-based tests** (Hypothesis), matching the approach used for the Security & Governance Layer. Property tests run a minimum of 100 iterations per property; example tests cover concrete flows, integration scenarios, and startup conditions.

### Property-Based Tests (Hypothesis)

Each property from the Correctness Properties section is implemented as a single Hypothesis `@given` test. Tests target pure function modules (`task_classifier.py`, `model_selector.py`, `fallback_manager.py`) or the FastAPI test client with mocked downstream services (Cache_Layer, Inference_Adapter, Audit_Store, Health_Checker) via `pytest-httpx`.

**Configuration:**
- Minimum 100 examples per test: `@settings(max_examples=100)`
- Tag format in test docstrings: `Feature: intelligent-router, Property N: <property_text>`

```python
# Example: Property 1 — Task Classification Keyword Match
from hypothesis import given, settings, strategies as st
from intelligent_router.task_classifier import classify_task, ClassifierRules

RULES = ClassifierRules(rules={
    "code": ["code", "function", "python"],
    "reasoning": ["reason", "think step by step", "math"],
    "summarization": ["summarize", "summary", "tldr"],
    "translation": ["translate", "in french"],
}, default="chat")

@given(
    prefix=st.text(max_size=50),
    suffix=st.text(max_size=50),
    keyword=st.sampled_from(["code", "function", "python"]),
    case_variant=st.sampled_from(["lower", "upper", "title"]),
)
@settings(max_examples=100)
def test_classification_code_keyword(prefix, suffix, keyword, case_variant):
    """
    Feature: intelligent-router, Property 1:
    For any messages containing a code keyword, task_type = 'code'
    """
    kw = {"lower": keyword.lower(), "upper": keyword.upper(),
          "title": keyword.title()}[case_variant]
    messages = [{"role": "user", "content": f"{prefix}{kw}{suffix}"}]
    assert classify_task(messages, RULES) == "code"


# Example: Property 5 — Fallback Level Monotonicity
@given(
    chain_length=st.integers(min_value=1, max_value=5),
    failures=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=100)
def test_fallback_level_monotonicity(chain_length, failures):
    """
    Feature: intelligent-router, Property 5:
    fallback_level == models_tried - 1
    """
    failures = min(failures, chain_length)
    # Build a chain of chain_length models
    models = {f"model-{i}": ModelEntry(name=f"model-{i}", backend="ollama",
              endpoint="http://x", tasks=["chat"],
              health_url="http://x/health",
              fallback=f"model-{i+1}" if i < chain_length - 1 else None)
              for i in range(chain_length)}
    matrix = ModelMatrix(models=models, task_defaults={"chat": "model-0"})
    state = create_fallback_state("model-0", matrix)

    for i in range(failures):
        next_model = state.advance()
        assert state.fallback_level == i + 1
        if failures < chain_length:
            assert next_model is not None
        else:
            assert state.fallback_level <= chain_length
```

**Property test targets by module:**

| Property | Module Under Test | Mocking Needed |
|---|---|---|
| 1 — Task Classification keyword match | `task_classifier.py` | None (pure function) |
| 2 — Task Classification default | `task_classifier.py` | None (pure function) |
| 3 — Model Selection always in matrix | `model_selector.py` | None (pure function) |
| 4 — IMF Field Preservation | `pipeline.py` | Mock Cache (MISS), mock Inference (echo IMF) |
| 5 — Fallback Level Monotonicity | `fallback_manager.py` + `pipeline.py` | Mock Health Checker, mock Inference |
| 6 — OpenAI Response Shape | `routers/openai_compat.py` | Mock full pipeline via httpx |
| 7 — Cache Lookup Result Consistency | `pipeline.py` | Mock Cache (HIT/MISS/fail), mock Inference |
| 8 — Audit Failure Isolation | `routers/route.py` | Mock Audit Store to fail |
| 9 — Health State Reflection | `routers/health.py` | Mock app.state |
| 10 — Metrics Monotonicity | `metrics.py` + route handlers | Mock all downstream |
| 11 — Log Structure Invariant | `logging_config.py` | Capture stdout |

### Example-Based Unit Tests

Unit tests cover:

- Startup validation: each missing/invalid required env var causes `sys.exit(1)` with correct error log
- Config file loading: YAML not found, YAML malformed, empty `models` map, empty `task_defaults` map, empty `rules` map (WARNING, not failure)
- Inference timeout range validation: values 0 and 601 cause `sys.exit(1)`; values 1 and 600 are accepted
- Health check timeout range validation: values 0 and 31 cause `sys.exit(1)`; values 1 and 30 are accepted
- `governance.content_safety_passed` absent → HTTP 400, no downstream calls
- `governance.content_safety_passed` false → HTTP 400, no downstream calls
- Pinned mode with unknown model → HTTP 422 with `invalid_pinned_model`
- Pinned mode with empty string model → HTTP 422
- Health check returns 3xx → Fallback Manager advances
- Health check returns connection refused → Fallback Manager advances
- `fallback: null` model with health check failure → immediate HTTP 503 (no advance possible)
- Cache hit with missing `response.content` → treated as MISS, inference called
- Inference returns empty body → Fallback Manager advances
- Inference returns unparseable JSON → Fallback Manager advances
- Inference returns JSON with null `response.content` → Fallback Manager advances
- Cache write background task: dispatched after inference success, not dispatched on cache HIT
- OpenAI endpoint: `model` present → `routing_mode = "pinned"`; `model` absent → `routing_mode = "auto"`
- OpenAI endpoint: missing `messages` → HTTP 422 with OpenAI error schema
- `GET /health` returns 200 when both config files loaded
- `GET /health` returns 503 when model matrix failed to load
- `GET /health` requires no auth header
- `GET /health` makes no downstream calls

### Integration Tests

Integration tests run against the full FastAPI test client using `httpx.MockTransport` or `pytest-httpx`:

1. **Route endpoint — happy path:** Valid IMF, content_safety_passed=true, cache MISS → inference succeeds → HTTP 200, all IMF write-set fields populated, cache write dispatched, audit fired.
2. **Route endpoint — cache hit:** Valid IMF, cache returns HIT with response content → HTTP 200, `cache.lookup_hit=true`, Inference_Adapter NOT called, cache_hit audit fired.
3. **Route endpoint — governance gate:** IMF with `content_safety_passed=false` → HTTP 400, no cache/inference/audit calls.
4. **Route endpoint — health check failure then fallback success:** Primary model health check fails → Fallback Manager advances → fallback model healthy → inference succeeds → HTTP 200, `fallback_level=1`.
5. **Route endpoint — all backends exhausted:** All models in chain fail health check → HTTP 503 with `all_backends_exhausted`, `fallback_level = chain_length`.
6. **Route endpoint — inference failure then fallback:** Primary inference returns 500 → Fallback Manager advances → fallback inference succeeds → HTTP 200, `fallback_level=1`.
7. **OpenAI endpoint — happy path:** Valid `messages` array, no `model` field → `routing_mode=auto` → pipeline succeeds → OpenAI-compatible response body returned.
8. **OpenAI endpoint — pinned mode:** `model` field present → `routing_mode=pinned` → correct model selected.
9. **OpenAI endpoint — pipeline error:** All backends exhausted → HTTP 503 with OpenAI error envelope `{"error": {"code": 503, ...}}`.
10. **Audit Store unavailable:** All audit POST calls return 503 → caller still gets correct response, WARNING logged.
11. **Cache Layer unavailable:** Cache lookup times out → `cache.lookup_hit=false`, inference proceeds normally.
12. **IMF field preservation:** Inbound IMF with non-null values in all non-write-set fields → output IMF preserves every non-write-set field unchanged.

### Smoke Tests

- Service starts successfully with valid configuration
- `GET /health` returns 200 with correct `rules_loaded` and `models_loaded` counts
- `GET /health` requires no X-API-Key header
- `/metrics` endpoint on port 9090 returns `Content-Type: text/plain; version=0.0.4`
- `llm_router_requests_total`, `llm_router_latency_seconds`, `llm_router_cache_hits_total`, `llm_router_fallbacks_total`, `llm_router_errors_total` are all present in the `/metrics` output after one request
- `helm lint llm-platform/charts/router/` passes without errors
- `helm template llm-platform/charts/router/` renders Deployment, Service, ConfigMap, ServiceMonitor, HPA, and NetworkPolicy manifests
- Deployment manifest includes liveness and readiness probes pointing to `/health`
- ConfigMap data contains both `model_matrix.yaml` and `task_classifier_rules.yaml` keys
- Service exposes ports 8082 (`http`) and 9090 (`metrics`)

### Test Directory Structure

```
tests/
├── unit/
│   ├── test_task_classifier.py    # Properties 1, 2
│   ├── test_model_selector.py     # Property 3
│   ├── test_fallback_manager.py   # Property 5 (pure logic)
│   ├── test_cache_client.py       # Property 7 (unit side)
│   ├── test_audit_client.py       # Property 8
│   ├── test_logging.py            # Property 11
│   └── test_config_startup.py    # Startup smoke tests
├── integration/
│   ├── test_route.py              # Properties 4, 5, 7, 9, 10 + integration flows
│   ├── test_openai_compat.py      # Property 6 + OpenAI integration flows
│   ├── test_health_metrics.py     # Properties 9, 10
│   └── test_pipeline.py          # Property 4 + full pipeline flows
└── conftest.py                    # Shared fixtures: test app, mock httpx clients
```

### Dependencies

```
# Test dependencies (requirements-test.txt)
pytest==8.3.5
pytest-asyncio==0.24.0
hypothesis==6.131.18
httpx==0.27.2
pytest-httpx==0.30.0
```

---
