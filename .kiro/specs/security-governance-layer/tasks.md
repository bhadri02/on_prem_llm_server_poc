# Implementation Plan: Security & Governance Layer

## Overview

Implementation tasks for the Security & Governance Layer (Layer 2): a FastAPI microservice that enforces all platform governance controls on every request. The service runs on port 8081, exposes Prometheus metrics on port 9090, and every platform request passes through it twice — once before inference (pre-generation pipeline: injection scan → content safety → PII masking → policy check → pre-audit → forward to Router) and once after inference (post-generation pipeline: PII masking on response → post-audit). It integrates with the Audit Store (port 9200) and Intelligent Router (port 8082) using plain HTTP JSON with static API key auth. Structured JSON logs are emitted to stdout. All production-deferred features (OPA, LlamaGuard, ML classifiers, mTLS, Vault, human approval workflow, autoscaling) are explicitly out of scope for the POC.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2", "3"] },
    { "wave": 3, "tasks": ["4", "5"] },
    { "wave": 4, "tasks": ["6", "7", "8", "9", "10"] },
    { "wave": 5, "tasks": ["11", "12", "13"] },
    { "wave": 6, "tasks": ["14", "15", "16", "17"] },
    { "wave": 7, "tasks": ["18", "19", "20"] },
    { "wave": 8, "tasks": ["21", "22", "23"] },
    { "wave": 9, "tasks": ["24"] },
    { "wave": 10, "tasks": ["25"] },
    { "wave": 11, "tasks": ["26"] }
  ]
}
```

---

## Tasks

- [x] 1. Project scaffolding and package structure
  - [x] 1.1 Create the `security_layer/` Python package directory with `__init__.py`
  - [x] 1.2 Create the `security_layer/routers/` sub-package directory with `__init__.py`
  - [x] 1.3 Create the `tests/` directory tree: `tests/conftest.py`, `tests/unit/`, `tests/property/`, `tests/integration/`, `tests/smoke/` (all with `__init__.py`)
  - [x] 1.4 Create `requirements.txt` with pinned versions for: `fastapi==0.115.5`, `uvicorn[standard]==0.32.1`, `pydantic==2.10.3`, `pydantic-settings==2.6.1`, `prometheus-client==0.21.1`, `httpx==0.27.2`, `presidio-analyzer==2.2.355`, `presidio-anonymizer==2.2.355`, `pyyaml==6.0.2`, `pytest==8.3.5`, `pytest-asyncio==0.24.0`, `hypothesis==6.131.18`, `pytest-httpx==0.30.0`

- [ ] 2. `config.py` — environment-driven settings
  - [x] 2.1 Implement `Settings` class using `pydantic_settings.BaseSettings` with required fields: `downstream_router_url: str`, `audit_store_url: str`, `audit_api_key: str`, `injection_patterns_path: str`; and optional fields: `log_level: str = "INFO"`, `pii_enabled: bool = True`
  - [x] 2.2 Instantiate a module-level `settings = Settings()` singleton so other modules can import it directly
  - [x] 2.3 Add validation that `pii_enabled` rejects values other than `"true"` or `"false"` (case-insensitive) at `Settings` construction time, raising `ValidationError` to be caught by the lifespan handler

- [x] 3. `logging_config.py` — structured JSON logger
  - [x] 3.1 Implement `JSONFormatter` class (subclass of `logging.Formatter`) whose `format()` method returns a single-line JSON string containing `timestamp` (ISO-8601 UTC ending in `Z`), `level`, and `message` fields, plus any extra fields passed via `extra={"extra_fields": {...}}`
  - [x] 3.2 Implement `get_logger(name: str) -> logging.Logger` factory that attaches a `StreamHandler(sys.stdout)` with `JSONFormatter` and sets the level from `settings.log_level`, defaulting to `INFO` for unrecognised values
  - [x] 3.3 Write unit tests in `tests/unit/test_logging.py` verifying: output is valid single-line JSON, mandatory fields (`timestamp`, `level`, `message`) are present, extra fields are merged at top level, unrecognised `LOG_LEVEL` falls back to `INFO`

- [x] 4. `models.py` — Pydantic IMF models and audit event payloads
  - [x] 4.1 Define `UUID4_RE` compiled regex pattern: `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (case-insensitive)
  - [x] 4.2 Implement `Message(BaseModel)` with `role: str` and `content: str`
  - [x] 4.3 Implement `UserBlock(BaseModel)` with optional fields: `user_id: str | None = None`, `department: str | None = None`, `roles: list[str] | None = None`, `auth_method: str | None = None`
  - [x] 4.4 Implement `RequestBlock(BaseModel)` with `messages: list[Message] = Field(min_length=1)` and optional fields: `model`, `task_type`, `stream`, `max_tokens`, `temperature`
  - [x] 4.5 Implement `GovernanceBlock(BaseModel)` with all seven governance fields and correct defaults: `pii_masked: bool = False`, `pii_fields_detected: list[str] = []`, `injection_score: float = 0.0`, `jailbreak_score: float = 0.0`, `content_safety_passed: bool = True`, `human_approval_required: bool = False`, `human_approval_status: str = "not_required"`, `policy_decisions: list[str] = []`
  - [x] 4.6 Implement `ResponseBlock(BaseModel)` with `content: str | None = None` and `finish_reason: str | None = None`
  - [x] 4.7 Implement `IMFRequest(BaseModel)` with `request_id: str` (validated UUID-v4 via `@field_validator`), `user: UserBlock | None`, `request: RequestBlock`, `governance: GovernanceBlock`, `response: ResponseBlock | None`, `metadata: dict`, `extensions: dict`; the `@field_validator("request_id")` SHALL reject any value not matching `UUID4_RE` with `ValueError("request_id must be a valid UUID-v4")`
  - [x] 4.8 Implement `PreAuditEventPayload` and `PostAuditEventPayload` models for constructing audit event dicts with fields: `request_id`, `user_id`, `layer: str = "security"`, `event_type: str`, `outcome: str`, `timestamp_utc: str`, `latency_ms: int`, `pii_actions: list[str]`, `policy_decisions: list[str]`
  - [x] 4.9 Write unit tests in `tests/unit/test_models.py` verifying: valid UUID-v4 strings pass, non-UUID strings fail with `ValueError`, `messages` absent or empty list raises `ValidationError`, optional fields accept `None`, `GovernanceBlock` defaults are all correct, `ResponseBlock.content = None` is valid

- [x] 5. `injection_patterns.yaml` — seed patterns file
  - [x] 5.1 Create `injection_patterns.yaml` at the repo root (or `security_layer/`) with a `patterns` list containing at minimum: `"ignore previous instructions"`, `"ignore all instructions"`, `"you are now"`, `"disregard your"`, `"forget your training"`, `"act as if"`, `"pretend you are"`, `"\\{\\{.*\\}\\}"`, `"<\\?.*\\?>"` — covering both plain keyword and regex metacharacter entries per Requirement 3.8

- [x] 6. `injection.py` — prompt injection detector
  - [x] 6.1 Implement `load_injection_patterns(path: str) -> Optional[list[re.Pattern]]` that reads `INJECTION_PATTERNS_PATH`, calls `yaml.safe_load`, compiles each entry via `re.compile(p, re.IGNORECASE)`, and returns the list; returns `None` on `FileNotFoundError`, `yaml.YAMLError`, or `re.error`, logging an ERROR identifying the specific failure in each case
  - [x] 6.2 Implement `scan_for_injection(messages: list[dict], patterns: list[re.Pattern]) -> float` that concatenates all message `content` fields with a single space separator, applies each compiled pattern via `re.search`, and returns `1.0` on first match or `0.0` if no pattern matches
  - [x] 6.3 Write unit tests in `tests/unit/test_injection.py` verifying: YAML not found returns `None` and logs ERROR, malformed YAML returns `None` and logs ERROR, invalid regex returns `None` and logs ERROR, empty patterns list returns `[]` (not `None`), scan with no patterns always returns `0.0`, scan with matching pattern returns `1.0`, scan is case-insensitive, plain string entry matches as substring

- [x] 7. `content_safety.py` — content safety filter
  - [x] 7.1 Define a module-level `BLOCKLIST: list[str]` constant with a hardcoded POC blocklist of clearly unsafe keywords
  - [x] 7.2 Implement `check_content_safety(messages: list[dict], blocklist: list[str]) -> bool` that concatenates all message `content` fields (lowercased), returns `True` (safe) if no blocklisted word is found as a case-insensitive substring, returns `False` (unsafe) on first match; logs a WARNING if `blocklist` is empty and returns `True`
  - [x] 7.3 Write unit tests in `tests/unit/test_content_safety.py` verifying: prompt containing blocklisted word returns `False`, prompt with no blocklisted words returns `True`, check is case-insensitive, empty blocklist returns `True` and logs WARNING, match across concatenated messages is detected

- [x] 8. `pii.py` — Presidio wrapper
  - [x] 8.1 Define `POC_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON"]` and `MIN_CONFIDENCE = 0.7` module-level constants
  - [x] 8.2 Implement `mask_text(text: str, analyzer: AnalyzerEngine, anonymizer: AnonymizerEngine, pii_enabled: bool) -> tuple[str, list[str]]` that: returns `(text, [])` immediately if `pii_enabled=False` or `text` is empty; calls `analyzer.analyze()` with `entities=POC_ENTITIES`, `language="en"`, `score_threshold=MIN_CONFIDENCE`; returns `(text, [])` if no results; calls `anonymizer.anonymize()` with `OperatorConfig("replace", {"new_value": f"[REDACTED_{entity}]"})` per entity; returns `(anonymized.text, deduplicated_entity_types)`
  - [x] 8.3 Implement `mask_messages(messages: list[dict], analyzer, anonymizer, pii_enabled: bool) -> tuple[list[dict], list[str]]` that applies `mask_text()` to each message's `content` field and returns the updated messages list with the deduplicated union of all detected entity types
  - [x] 8.4 Write unit tests in `tests/unit/test_pii.py` verifying: `pii_enabled=False` returns original text unchanged with empty entity list, empty text returns unchanged with empty entity list, text with email is masked to `[REDACTED_EMAIL_ADDRESS]`, entity types are deduplicated, `mask_messages` processes all messages and aggregates entity types

- [x] 9. `policy.py` — role-based policy check
  - [x] 9.1 Define `ALLOWED_ROLES = frozenset({"developer", "analyst", "admin"})` as the module-level constant
  - [x] 9.2 Implement `check_policy(roles: list[str] | None) -> tuple[bool, str]` that: returns `(False, "role_check_deny")` if `roles` is `None` or empty; returns `(True, "role_check_pass")` if any role in `roles` is in `ALLOWED_ROLES`; returns `(False, "role_check_deny")` otherwise
  - [x] 9.3 Write unit tests in `tests/unit/test_policy.py` verifying: `None` roles returns deny, empty list returns deny, `["developer"]` returns pass, `["unknown"]` returns deny, list with one valid and multiple invalid roles returns pass, `["admin"]` and `["analyst"]` each return pass, roles comparison is case-sensitive

- [x] 10. `metrics.py` — Prometheus metric definitions
  - [x] 10.1 Define `requests_total = Counter("llm_security_requests_total", "Total pre-generation pipeline requests by outcome and terminating check", labelnames=["outcome", "check"])` where `outcome` ∈ `{pass, block, error}` and `check` ∈ `{injection, content_safety, policy, full_pipeline}`
  - [x] 10.2 Define `latency = Histogram("llm_security_latency_seconds", "Handler latency from entry to response return", labelnames=["endpoint"], buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0])` where `endpoint` ∈ `{pre_check, post_check}`
  - [x] 10.3 Define `pii_entities_total = Counter("llm_security_pii_entities_total", "Count of PII entities detected by entity type", labelnames=["entity_type"])` where `entity_type` ∈ `{EMAIL_ADDRESS, PHONE_NUMBER, PERSON, OTHER}`
  - [x] 10.4 Define `blocks_total = Counter("llm_security_blocks_total", "Requests blocked at any pipeline stage by reason", labelnames=["reason"])` where `reason` ∈ `{injection_detected, content_safety_violation, policy_denied}`

- [x] 11. `pipeline.py` — pre- and post-generation pipeline orchestrator
  - [x] 11.1 Define `PipelineResult` dataclass with fields: `blocked: bool`, `block_reason: str | None` (one of `injection_detected`, `content_safety_violation`, `policy_denied`), `block_status: int | None` (400 or 403), `imf: dict` (enriched IMF mutated in-place), `latency_ms: int`
  - [x] 11.2 Implement `run_pre_pipeline(imf: dict, state) -> PipelineResult` enforcing strict ordering: (1) injection scan → sets `governance.injection_score`, returns block if score=1.0; (2) content safety → sets `governance.content_safety_passed`, returns block if False; (3) PII masking on `request.messages` → sets `governance.pii_masked` and `governance.pii_fields_detected`; (4) policy check → appends to `governance.policy_decisions`, returns block if denied; (5) sets `governance.human_approval_required=False` and `governance.human_approval_status="not_required"`; returns non-blocked result
  - [x] 11.3 Implement `run_post_pipeline(imf: dict, state) -> tuple[dict, list[str]]` that applies `mask_text()` to `imf["response"]["content"]` if non-null, updates `governance.pii_masked` and `governance.pii_fields_detected`, and returns the enriched IMF and detected entity type list
  - [x] 11.4 Write unit tests in `tests/unit/test_pipeline.py` verifying: injection block short-circuits before content safety, content safety block short-circuits before PII and policy, PII always runs before policy on passing requests, policy deny returns correct `block_status=403`, all governance fields present and correctly typed on a passing pipeline, `run_post_pipeline` with null `response.content` returns IMF unchanged with empty entity list

- [x] 12. `audit_client.py` — fire-and-forget audit writer
  - [x] 12.1 Implement `async def post_audit_event(event: dict, url: str, api_key: str) -> None` using `httpx.AsyncClient(timeout=2.0)` to POST to `{url}/audit/events` with `{"X-API-Key": api_key}` header and `json=event` body
  - [x] 12.2 Catch `httpx.TimeoutException` → log WARNING with `request_id` and the word `"timeout"`; catch non-2xx response → log WARNING with `request_id` and `status_code`; catch all other exceptions → log WARNING with `request_id` and `error`; never re-raise any exception
  - [x] 12.3 Write unit tests in `tests/unit/test_audit_client.py` verifying: HTTP 500 from Audit Store logs WARNING and does not raise, timeout logs WARNING with `"timeout"` keyword and does not raise, connection refused logs WARNING and does not raise, `X-API-Key` header is included in every POST (inspected via `pytest-httpx` mock)

- [x] 13. `router_client.py` — downstream Router client
  - [x] 13.1 Define typed exception classes: `RouterTimeoutError(Exception)`, `RouterUnavailableError(Exception)`, `RouterInvalidResponseError(Exception)`
  - [x] 13.2 Define `ROUTER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)`
  - [x] 13.3 Implement `async def forward_to_router(imf: dict, router_url: str, request_id: str) -> tuple[int, dict]` that POSTs to `{router_url}/router/route` with `{"X-Request-Id": request_id}` header; on 2xx success parses JSON body and returns `(status_code, body_dict)`; raises `RouterInvalidResponseError` if 2xx but body is empty or not valid JSON; raises `RouterTimeoutError` on `httpx.TimeoutException`; raises `RouterUnavailableError` on `httpx.ConnectError`; for non-2xx responses, returns `(status_code, body_dict)` unchanged

- [x] 14. `metrics_app.py` — separate ASGI metrics application
  - [x] 14.1 Create a lightweight ASGI application (Starlette `Router` or bare Starlette app) that serves `GET /metrics` using `prometheus_client.make_asgi_app()` on port 9090
  - [x] 14.2 Import `security_layer.metrics` at the top of `metrics_app.py` to ensure all four counters/histograms are registered in the default Prometheus registry before `make_asgi_app()` is called
  - [x] 14.3 Ensure this app is completely independent of the main FastAPI app — no auth middleware, no shared lifespan, no `app.state` dependency

- [x] 15. `routers/pre_check.py` — POST /security/check
  - [x] 15.1 Implement `POST /security/check` handler accepting `body: IMFRequest`, `request: Request`, `background_tasks: BackgroundTasks`; capture `t0 = time.monotonic()` at handler entry
  - [x] 15.2 Call `run_pre_pipeline(imf, request.app.state)` to get `PipelineResult`; construct `pre_audit_event` dict unconditionally (pass or block); dispatch `post_audit_event` via `background_tasks.add_task(...)` before returning any response
  - [x] 15.3 If `result.blocked`: increment `metrics.blocks_total.labels(reason=result.block_reason).inc()`, increment `metrics.requests_total.labels(outcome="block", check=result.block_reason).inc()`, observe `metrics.latency.labels(endpoint="pre_check").observe(...)`, raise `HTTPException(status_code=result.block_status, detail={"error": result.block_reason, "request_id": request_id})`
  - [x] 15.4 If not blocked: call `forward_to_router(...)`; on success increment `metrics.requests_total.labels(outcome="pass", check="full_pipeline").inc()`, observe latency, return `JSONResponse(status_code=status, content=router_body)`; catch `RouterTimeoutError` → return HTTP 504 `{"error": "router_timeout", "request_id": request_id}`; catch `RouterUnavailableError` → return HTTP 502 `{"error": "router_unavailable", "request_id": request_id}`; catch `RouterInvalidResponseError` → return HTTP 502 `{"error": "router_invalid_response", "request_id": request_id}`
  - [x] 15.5 Emit INFO-level security-decision log entry after pipeline completes containing: `request_id`, `injection_detected` (bool), `pii_entities_found` (list), `outcome` (`pass` or `block`), `latency_ms`

- [x] 16. `routers/post_check.py` — POST /security/post-check
  - [x] 16.1 Implement `POST /security/post-check` handler accepting `body: IMFRequest`, `request: Request`, `background_tasks: BackgroundTasks`; capture `t0 = time.monotonic()` at handler entry
  - [x] 16.2 Call `run_post_pipeline(imf, request.app.state)` to get enriched IMF and entity types; on unhandled Presidio exception: set `pii_masked=False`, log ERROR with `request_id` and exception, return HTTP 200 with unmasked IMF (graceful degradation per Requirement 2.5)
  - [x] 16.3 Construct `post_audit_event` dict with `event_type: "response_sent"`, `outcome: "pass"`, `pii_actions` (entity types or `[]`); dispatch via `background_tasks.add_task(...)` before returning response
  - [x] 16.4 Observe `metrics.latency.labels(endpoint="post_check")`; increment `metrics.pii_entities_total` per detected entity type; emit INFO-level log entry with `request_id`, `pii_entities_found`, `latency_ms`; return `JSONResponse(status_code=200, content=enriched_imf)`

- [x] 17. `routers/health.py` — GET /health
  - [x] 17.1 Implement `GET /health` handler with NO authentication requirement; read `state.settings.pii_enabled`, `state.patterns` (count), `state.analyzer` (None check) from `request.app.state`
  - [x] 17.2 Return HTTP 200 `{"status": "ok", "pii_enabled": <bool>, "patterns_loaded": <int>}` when Presidio is initialized (or `pii_enabled=False`) AND `len(state.patterns) > 0`
  - [x] 17.3 Return HTTP 503 `{"status": "degraded", "reason": "presidio_unavailable"}` if `pii_enabled=True` and `state.analyzer is None`; return HTTP 503 `{"status": "degraded", "reason": "no_patterns_loaded"}` if `len(state.patterns) == 0`

- [x] 18. `main.py` — FastAPI app factory and lifespan handler
  - [x] 18.1 Define `lifespan` async context manager that before `yield` performs in order: (1) validate each of `downstream_router_url`, `audit_store_url`, `audit_api_key`, `injection_patterns_path` is non-empty — log ERROR and `sys.exit(1)` if any is absent or empty; (2) call `load_injection_patterns(settings.injection_patterns_path)` — if returns `None` call `sys.exit(1)`; if returns empty list log WARNING and continue; (3) if `settings.pii_enabled`, instantiate `AnalyzerEngine()` and `AnonymizerEngine()` — on exception log ERROR and `sys.exit(1)`; (4) store `settings`, `patterns`, `analyzer`, `anonymizer`, `blocklist` on `app.state`; after `yield`: log INFO "Security Layer stopped"
  - [x] 18.2 Create the FastAPI `app` with `lifespan=lifespan`, `title="Security & Governance Layer"`, `version="0.1.0"`
  - [x] 18.3 Add a custom exception handler for `RequestValidationError` that returns HTTP 400 when the body is not parseable as JSON (JSON decode error) and HTTP 422 for all other validation errors, preserving the validation detail
  - [x] 18.4 Include `pre_check_router`, `post_check_router`, and `health_router` on `app` — no prefix needed as routes already carry their full paths
  - [x] 18.5 Define a `create_app() -> FastAPI` factory function that returns the configured `app` — used by tests and the entrypoint

- [x] 19. `Dockerfile` and entrypoint
  - [x] 19.1 Write a multi-stage `Dockerfile`: base stage `python:3.12-slim`; install `requirements.txt`; copy `security_layer/` package and `injection_patterns.yaml`; set `CMD ["sh", "-c", "uvicorn security_layer.main:app --host 0.0.0.0 --port 8081 & uvicorn security_layer.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & wait"]`
  - [x] 19.2 Add a `.dockerignore` (or extend existing) excluding `.git`, `__pycache__`, `tests/`, `*.pyc`, `.kiro/`

- [ ] 20. Property-based tests — injection, content safety, policy, determinism (Properties 1, 6, 7, 8)
  - [x] 20.1 Create `tests/conftest.py` with: a `test_app` fixture using `create_app()` with mocked `app.state` (compiled patterns, mocked Presidio engines, BLOCKLIST, settings), an `httpx.AsyncClient` fixture using `ASGITransport`, a Prometheus registry reset fixture to prevent counter bleed between tests, and a `mock_router_transport` fixture returning a configurable 200/502/504 response
  - [ ] 20.2 Create `tests/property/test_injection_properties.py` with Hypothesis `settings` profile `ci` (`max_examples=100`)
  - [ ] 20.3 **[PBT]** Property 1a — `test_injection_no_match_gives_zero_score`: `@given(messages=st.lists(st.fixed_dictionaries({"role": st.sampled_from(["user","assistant","system"]), "content": st.text(min_size=1)}), min_size=1, max_size=5))`; with empty patterns list; assert `scan_for_injection(messages, []) == 0.0`
    - **Validates: Requirements 3.3, 3.4**
  - [ ] 20.4 **[PBT]** Property 1b — `test_injection_match_gives_one_score`: `@given(prefix=st.text(), suffix=st.text(), pattern=st.sampled_from(["ignore previous instructions", "you are now", "pretend you are"]))`; build messages with pattern embedded; compile pattern; assert `scan_for_injection(messages, compiled) == 1.0`
    - **Validates: Requirements 3.1, 3.2, 3.4**
  - [ ] 20.5 **[PBT]** Property 6 — `test_injection_determinism`: `@given(messages=st.lists(...), patterns=st.lists(st.sampled_from([...])))`; call `scan_for_injection` twice with same inputs; assert both calls return identical result
    - **Validates: Requirement 3.8**
  - [ ] 20.6 **[PBT]** Property 7 — `test_policy_deny_for_unauthorized_roles`: `@given(roles=st.one_of(st.none(), st.just([]), st.lists(st.text().filter(lambda r: r not in {"developer","analyst","admin"}), min_size=1, max_size=5)))`; assert `check_policy(roles) == (False, "role_check_deny")`
    - **Validates: Requirements 6.2, 6.3**
  - [ ] 20.7 **[PBT]** Property 7b — `test_policy_pass_for_any_authorized_role`: `@given(valid_role=st.sampled_from(["developer","analyst","admin"]), extra=st.lists(st.text(), max_size=3))`; assert `check_policy([valid_role] + extra)[0] == True`
    - **Validates: Requirement 6.1**
  - [ ] 20.8 **[PBT]** Property 8 — `test_content_safety_blocks_blocklisted_word`: `@given(prefix=st.text(), suffix=st.text(), word=st.sampled_from(BLOCKLIST))`; construct messages with word embedded; assert `check_content_safety(messages, BLOCKLIST) == False`
    - **Validates: Requirements 4.1, 4.3**
  - [ ] 20.9 **[PBT]** Property 8b — `test_content_safety_passes_clean_prompt`: `@given(content=st.text().filter(lambda t: not any(w.lower() in t.lower() for w in BLOCKLIST)))`; assert `check_content_safety([{"role":"user","content":content}], BLOCKLIST) == True`
    - **Validates: Requirements 4.2, 4.3**

- [ ] 21. Property-based tests — PII masking, pipeline ordering, governance fields, audit isolation (Properties 2, 3, 4, 5, 14)
  - [ ] 21.1 Create `tests/property/test_pii_properties.py` with Hypothesis `ci` profile (`max_examples=100`)
  - [ ] 21.2 **[PBT]** Property 3 — `test_pii_masking_round_trip`: `@given(text=st.text(min_size=1).filter(lambda t: "@" in t or re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', t) is not None))`; call `mask_text(text, analyzer, anonymizer, pii_enabled=True)`; if entity types returned, assert none of the original PII tokens appear in the masked text and entity types are a non-empty deduplicated list
    - **Validates: Requirements 5.1, 5.2, 5.4**
  - [ ] 21.3 **[PBT]** Property 14 — `test_pii_disabled_skips_masking_for_any_input`: `@given(text=st.text(min_size=0, max_size=500))`; call `mask_text(text, analyzer, anonymizer, pii_enabled=False)`; assert returned text equals original text and entity list is `[]`
    - **Validates: Requirement 5.5**
  - [ ] 21.4 Create `tests/property/test_pipeline_properties.py`
  - [ ] 21.5 **[PBT]** Property 2 — `test_pipeline_short_circuit_after_injection_block`: `@given(imf=valid_imf_strategy_with_injection_pattern())`; run `run_pre_pipeline`; assert `result.blocked==True`, `result.block_reason=="injection_detected"`, `result.imf["governance"]["content_safety_passed"]` is still the default (content safety never executed), policy decisions list is empty
    - **Validates: Requirements 1.6, 4.4, 6.5**
  - [ ] 21.6 **[PBT]** Property 2b — `test_pipeline_short_circuit_after_content_safety_block`: `@given(imf=valid_imf_strategy_with_blocklisted_content())`; assert content safety blocks, `result.block_reason=="content_safety_violation"`, policy decisions list is empty
    - **Validates: Requirements 1.6, 4.4, 6.5**
  - [ ] 21.7 **[PBT]** Property 4 — `test_governance_field_completeness_on_pass`: `@given(imf=valid_passing_imf_strategy())`; run `run_pre_pipeline` with mocked downstream; assert all seven governance fields present with correct types: `injection_score` is float `0.0`, `content_safety_passed` is `True`, `pii_masked` is bool, `pii_fields_detected` is list, `policy_decisions` is non-empty list, `human_approval_required` is `False`, `human_approval_status == "not_required"`
    - **Validates: Requirements 1.2, 6.6**
  - [ ] 21.8 **[PBT]** Property 5 — `test_audit_failure_isolation`: `@given(imf=valid_passing_imf_strategy(), audit_status=st.sampled_from([500, 503, 0]))`; configure mock Audit Store to fail (non-2xx or timeout); call `POST /security/check`; assert endpoint still returns the correct status code (200, 400, or 403 per pipeline outcome) and caller response is not affected
    - **Validates: Requirements 7.3, 7.4, 7.5, 8.2, 8.3**

- [ ] 22. Property-based tests — HTTP API properties, health, metrics, logging (Properties 9, 10, 11, 12, 13)
  - [ ] 22.1 Create `tests/property/test_api_properties.py` with Hypothesis `ci` profile (`max_examples=100`)
  - [ ] 22.2 **[PBT]** Property 12 — `test_invalid_request_id_always_422`: `@given(request_id=st.text().filter(lambda s: not UUID4_RE.match(s)), endpoint=st.sampled_from(["/security/check", "/security/post-check"]))`; submit otherwise-valid IMF with invalid `request_id`; assert HTTP 422 with detail identifying `request_id`, no pipeline stages invoked
    - **Validates: Requirements 1.4, 2.7**
  - [ ] 22.3 **[PBT]** Property 13 — `test_non_json_body_always_400`: `@given(body=st.binary().filter(lambda b: _is_not_valid_json(b)), endpoint=st.sampled_from(["/security/check", "/security/post-check"]))`; send raw bytes with `Content-Type: application/json`; assert HTTP 400
    - **Validates: Requirements 1.3, 2.6**
  - [ ] 22.4 **[PBT]** Property 9 — `test_health_reflects_engine_state`: `@given(presidio_ok=st.booleans(), patterns_count=st.integers(min_value=0, max_value=20))`; set `app.state.analyzer` to mock or `None` and `app.state.patterns` to list of given length; call `GET /health` without auth; assert HTTP 200 with `{"status":"ok"}` when `presidio_ok=True` and `patterns_count > 0`; assert HTTP 503 with `{"status":"degraded"}` otherwise
    - **Validates: Requirements 10.1, 10.2**
  - [ ] 22.5 **[PBT]** Property 10 — `test_metrics_counters_monotonically_nondecreasing`: `@given(n=st.integers(min_value=1, max_value=10), outcome=st.sampled_from(["pass", "injection_block", "content_block", "policy_deny"]))`; record counter values before N requests; process N requests; assert `llm_security_requests_total` increased by exactly N, `llm_security_blocks_total` increased by number of blocked requests, `llm_security_latency_seconds` has N new observations; counters never decreased
    - **Validates: Requirements 11.2, 11.3, 11.4, 11.5**
  - [ ] 22.6 **[PBT]** Property 11 — `test_every_log_entry_is_single_line_json`: `@given(operation=st.sampled_from(["pre_check_pass", "pre_check_injection_block", "post_check", "health"]))`; capture stdout during operation; for each captured line assert: `json.loads(line)` succeeds, `"timestamp"` is present and parses as ISO-8601 ending in `Z`, `"level"` is one of `DEBUG/INFO/WARNING/ERROR`, line contains no embedded newlines
    - **Validates: Requirements 12.1, 12.5**

- [x] 23. Unit tests — models, startup validation, router client
  - [x] 23.1 Extend `tests/unit/test_models.py`: verify `IMFRequest` rejects non-UUID `request_id` with `ValidationError`; verify valid UUID-v4 strings pass; verify absent `request.messages` raises `ValidationError`; verify empty `request.messages` raises `ValidationError`; verify `GovernanceBlock` defaults match spec; verify `ResponseBlock.content=None` is valid
  - [x] 23.2 Create `tests/unit/test_config_startup.py`: verify `Settings` raises when `DOWNSTREAM_ROUTER_URL` is absent or empty; verify raises when `AUDIT_STORE_URL` is absent or empty; verify raises when `AUDIT_API_KEY` is absent or empty; verify raises when `INJECTION_PATTERNS_PATH` is absent or empty; verify invalid `PII_ENABLED` value (not `"true"` or `"false"`) raises; verify `LOG_LEVEL` unset defaults to `"INFO"` without error
  - [x] 23.3 Create `tests/unit/test_router_client.py`: verify `httpx.TimeoutException` raises `RouterTimeoutError`; verify `httpx.ConnectError` raises `RouterUnavailableError`; verify 2xx response with valid JSON returns `(status_code, dict)`; verify 2xx response with empty body raises `RouterInvalidResponseError`; verify 2xx response with non-JSON body raises `RouterInvalidResponseError`; verify non-2xx response returns `(status_code, body)` unchanged; verify `X-Request-Id` header is included in POST (via mock inspection)

- [x] 24. Integration tests — health, startup validation, pre-check and post-check pipelines
  - [x] 24.1 Create `tests/integration/test_health.py`: test `GET /health` returns 200 + `{"status":"ok","pii_enabled":true,"patterns_loaded":<n>}` when Presidio loaded and patterns present; test returns 503 `{"status":"degraded","reason":"presidio_unavailable"}` when `state.analyzer=None`; test returns 503 `{"status":"degraded","reason":"no_patterns_loaded"}` when `state.patterns=[]`; test requires no `X-API-Key` header
  - [x] 24.2 Create `tests/integration/test_startup.py`: test lifespan with `DOWNSTREAM_ROUTER_URL=""` calls `sys.exit(1)` after logging ERROR; repeat for `AUDIT_STORE_URL=""`, `AUDIT_API_KEY=""`, non-existent `INJECTION_PATTERNS_PATH`, malformed YAML; test empty patterns list logs WARNING but starts successfully; test valid configuration starts successfully with `app.state.patterns` and `app.state.analyzer` set
  - [x] 24.3 Create `tests/integration/test_pre_check.py`: test happy path — valid IMF with no injection/content issues and authorized role returns 200 with Router's enriched IMF; test injection block returns 400 `{"error":"injection_detected"}` with Router NOT called and audit dispatched; test content safety block returns 400 `{"error":"content_safety_violation"}` with Router NOT called; test policy deny returns 403 `{"error":"policy_denied","reason":"insufficient_role"}`; test PII masking — IMF with email in message forwards masked IMF to Router with `pii_masked=true`; test Router timeout → 504 `{"error":"router_timeout"}`; test Router unavailable → 502 `{"error":"router_unavailable"}`; test Router invalid response → 502 `{"error":"router_invalid_response"}`
  - [x] 24.4 Create `tests/integration/test_post_check.py`: test happy path — IMF with PII in `response.content` returns 200 with masked content and `pii_masked=true`; test null `response.content` returns IMF unchanged with `pii_actions:[]` in audit event; test Presidio exception during post-generation returns 200 with unmasked content and `pii_masked=false`; test audit event dispatched as background task and does not block response return; test `event_type:"response_sent"` and `outcome:"pass"` in audit payload
  - [x] 24.5 Verify audit `X-API-Key` header: in both pre-check and post-check integration tests, inspect the mock Audit Store call and assert the `X-API-Key` header equals the configured `AUDIT_API_KEY` value on every POST attempt

- [x] 25. Helm chart — `llm-platform/charts/security-layer/`
  - [x] 25.1 Create `llm-platform/charts/security-layer/Chart.yaml` with `apiVersion: v2`, `name: security-layer`, `description: "Security and Governance Layer for the Enterprise LLM Platform (POC)"`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`
  - [x] 25.2 Create `llm-platform/charts/security-layer/values.yaml` with all required defaults from Requirement 14.3: `replicaCount: 1`, `image.repository: registry.local/security-layer`, `image.tag: ""`, `image.pullPolicy: IfNotPresent`, `service.port: 8081`, `env.LOG_LEVEL: "INFO"`, `env.DOWNSTREAM_ROUTER_URL: "http://router:8082"`, `env.PII_ENABLED: "true"`, `env.INJECTION_PATTERNS_PATH: "/config/injection_patterns.yaml"` (note: `AUDIT_STORE_URL` and `AUDIT_API_KEY` NOT committed here — supplied at deploy time), `resources.requests.cpu: "200m"`, `resources.requests.memory: "512Mi"`, `resources.limits.cpu: "1"`, `resources.limits.memory: "1Gi"`, `observability.metrics.enabled: true`, `observability.metrics.port: 9090`, `observability.tracing.enabled: false`, `observability.tracing.endpoint: "http://otel-collector:4317"`, `autoscaling.enabled: false`, `autoscaling.minReplicas: 2`, `autoscaling.maxReplicas: 10`, `autoscaling.targetCPUUtilizationPercentage: 70`, `vault.enabled: false`, `vault.role: "security-layer-role"`, `vault.secretPath: "secret/llm-platform/security-layer"`
  - [x] 25.3 Create `llm-platform/charts/security-layer/templates/_helpers.tpl` defining `security-layer.fullname`, `security-layer.name`, `security-layer.chart`, `security-layer.selectorLabels`, and `security-layer.labels` template helpers following standard Helm conventions
  - [x] 25.4 Create `llm-platform/charts/security-layer/templates/deployment.yaml`: single container with `containerPort: 8081` (named `http`) and `containerPort: 9090` (named `metrics`); env vars `LOG_LEVEL`, `DOWNSTREAM_ROUTER_URL`, `PII_ENABLED`, `INJECTION_PATTERNS_PATH` from values; `AUDIT_STORE_URL` and `AUDIT_API_KEY` from `secretKeyRef` on `security-layer-secrets`; volume mount of `injection-patterns` ConfigMap at `/config` (read-only); liveness probe `GET /health:8081` (`initialDelaySeconds: 15`, `periodSeconds: 30`); readiness probe `GET /health:8081` (`initialDelaySeconds: 10`, `periodSeconds: 10`); resource requests/limits from values
  - [x] 25.5 Create `llm-platform/charts/security-layer/templates/service.yaml`: `ClusterIP` Service with port 8081 (named `http`) and port 9090 (named `metrics`) using selector from `_helpers.tpl`
  - [x] 25.6 Create `llm-platform/charts/security-layer/templates/networkpolicy.yaml`: allow ingress to port 8081 from `llm-api-gateway` namespace only; allow ingress to port 9090 from `llm-observability` namespace only; deny all other ingress; egress rules allow outbound to Router (8082), Audit Store (9200), OTel collector (4317), and DNS (53/UDP)
  - [x] 25.7 Create `llm-platform/charts/security-layer/templates/servicemonitor.yaml`: `ServiceMonitor` targeting port `metrics`, path `/metrics`, `interval: 30s`, selector using `security-layer.selectorLabels`
  - [x] 25.8 Create `llm-platform/charts/security-layer/templates/configmap.yaml`: ConfigMap named `{{ include "security-layer.fullname" . }}-patterns` with `data.injection_patterns.yaml` containing the seed patterns; mounted into the container at `/config/injection_patterns.yaml`
  - [x] 25.9 Create `llm-platform/charts/security-layer/templates/hpa.yaml`: conditional on `autoscaling.enabled`; defines `HorizontalPodAutoscaler` with `minReplicas`, `maxReplicas`, `targetCPUUtilizationPercentage` from values; disabled (`false`) for POC
  - [x] 25.10 Create `llm-platform/charts/security-layer/README.md` documenting: purpose and pipeline overview, port layout (8081 API / 9090 metrics), required secrets (`AUDIT_STORE_URL` and `AUDIT_API_KEY` via `security-layer-secrets`), all configurable `values.yaml` fields with types and defaults, ConfigMap injection patterns mount, example `helm install` command with `--set` flags for required secrets

- [x] 26. Smoke tests, Helm lint, and end-to-end integration validation
  - [x] 26.1 Create `tests/smoke/test_helm.py`: run `helm lint llm-platform/charts/security-layer/` via `subprocess` and assert exit code 0
  - [x] 26.2 Add `helm template` smoke test asserting the rendered output contains `kind: Deployment`, `kind: Service`, `kind: NetworkPolicy`, `kind: ServiceMonitor`, `kind: ConfigMap`, and `kind: HorizontalPodAutoscaler` resources
  - [x] 26.3 Add startup smoke test: instantiate `create_app()` with valid env vars and in-memory compiled patterns; run through lifespan; assert `app.state.patterns` is a non-empty list of compiled regex objects, `app.state.analyzer` is not `None` (when `PII_ENABLED=true`), `app.state.settings.downstream_router_url` is set
  - [x] 26.4 Add startup-refusal smoke test: with each of the four required env vars unset or empty (`DOWNSTREAM_ROUTER_URL`, `AUDIT_STORE_URL`, `AUDIT_API_KEY`, `INJECTION_PATTERNS_PATH`), assert that the lifespan raises `SystemExit` or calls `sys.exit(1)`
  - [x] 26.5 Create `tests/integration/test_end_to_end.py` for the full platform smoke flow: (a) submit a valid IMF to `POST /security/check` with a mocked Router returning 200 enriched IMF → assert 200 with all seven governance fields set, `X-Request-Id` header sent to Router, audit event dispatched; (b) take the Router IMF response and submit to `POST /security/post-check` → assert 200 with `event_type:"response_sent"`, `pii_actions` populated or `[]`, both audit events share the same `request_id`; (c) call `GET /health` → assert 200 `{"status":"ok"}`; (d) verify the metrics app test client returns `Content-Type: text/plain; version=0.0.4` and body contains `llm_security_requests_total`, `llm_security_latency_seconds`, `llm_security_pii_entities_total`, `llm_security_blocks_total`

---

## Notes

- **POC constraints in effect:** No HPA active (`autoscaling.enabled: false`), no Vault (`vault.enabled: false`), no mTLS, no OPA, no LlamaGuard, no ML classifiers, no human approval workflow — all deferred to Phase 2. Plain HTTP JSON between services. Static API key auth on Audit Store calls only.
- **Testing framework:** `pytest` + `hypothesis` (minimum 100 examples per PBT). HTTP test client is `httpx.AsyncClient` with `ASGITransport` — no real network required. Downstream services (Audit Store, Router) are mocked using `pytest-httpx` `MockTransport`.
- **Presidio in tests:** `AnalyzerEngine` and `AnonymizerEngine` are CPU-only. For unit tests of PII properties, use real Presidio engines with known PII strings (e.g., `"test@example.com"`). For integration and pipeline tests where Presidio side effects are undesirable, use mock objects returning pre-configured `AnalyzerResult` lists.
- **Metrics isolation in tests:** Reset the Prometheus registry between test runs using a session-scoped fixture to prevent counter bleed across tests. Use `CollectorRegistry()` per test app instance when possible.
- **PBT tasks** are marked `[PBT]` in the task list. Each must have its status updated after the test run.
- **Separate ASGI apps:** The metrics app (`metrics_app.py`) runs on port 9090 independently of the main app on port 8081. Both are started in the `Dockerfile` CMD using `&` + `wait`, identical to the Audit Store pattern.
- **Startup validation ordering:** The lifespan handler checks required env vars first (exits on missing), then loads injection patterns (exits on file/parse failure), then initializes Presidio (exits on failure). An empty patterns list is a WARNING, not an error.
- **Fire-and-forget audit dispatching:** All audit POSTs are dispatched via FastAPI `BackgroundTask`. The response to the caller is returned before the audit POST network round-trip completes or times out. Audit failures (timeout, non-2xx, connection refused) are logged as WARNING and never re-raised.
- **Security-decision log entries** (pipeline completion, blocks, post-pipeline completion) are always emitted at INFO level regardless of the configured `LOG_LEVEL` — they must never be suppressed per Requirement 12.8.
- **`AUDIT_STORE_URL` and `AUDIT_API_KEY` are never committed** to `values.yaml`. They must be supplied at deploy time via `--set` flags or via a Kubernetes `Secret` (`security-layer-secrets`).
- **Property numbering** maps directly to the 14 correctness properties defined in `design.md` (Properties 1–14). Properties 1, 6, 7, and 8 target pure functions and require no mocking. Properties 2, 4, 5 target the pipeline orchestrator with mocked downstream. Properties 3 and 14 target `pii.py` with real or mocked Presidio. Properties 9–13 target the FastAPI test client.
- **ConfigMap injection patterns:** The Helm `configmap.yaml` mounts `injection_patterns.yaml` into the container at `/config/injection_patterns.yaml` (the default `INJECTION_PATTERNS_PATH`). Customising patterns without rebuilding the image requires only a ConfigMap update and pod restart.
