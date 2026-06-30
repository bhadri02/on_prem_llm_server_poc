# Implementation Plan: Audit Store

## Overview

Implementation tasks for the Audit Store service: an append-only FastAPI + SQLite audit trail service for the Enterprise On-Premises LLM Platform (POC). The service runs on port 9200, exposes Prometheus metrics on port 9090, emits structured JSON logs to stdout, and uses static API key auth for write endpoints.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2"] },
    { "wave": 3, "tasks": ["3", "4", "5", "7"] },
    { "wave": 4, "tasks": ["6", "8"] },
    { "wave": 5, "tasks": ["9", "10"] },
    { "wave": 6, "tasks": ["11", "12"] },
    { "wave": 7, "tasks": ["13", "14", "15", "16", "17", "18"] },
    { "wave": 8, "tasks": ["19"] },
    { "wave": 9, "tasks": ["20"] }
  ]
}
```

---

## Tasks

- [x] 1. Project scaffolding and package structure
  - [x] 1.1 Create the `audit_store/` Python package directory with `__init__.py`
  - [x] 1.2 Create the `audit_store/routers/` sub-package directory with `__init__.py`
  - [x] 1.3 Create the `tests/` directory tree: `tests/conftest.py`, `tests/unit/`, `tests/property/`, `tests/integration/`, `tests/smoke/` (all with `__init__.py`)
  - [x] 1.4 Create `requirements.txt` with pinned versions for: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `prometheus-client`, `httpx`, `pytest`, `pytest-asyncio`, `hypothesis`

- [x] 2. `config.py` — environment-driven settings
  - [x] 2.1 Implement `Settings` class using `pydantic_settings.BaseSettings` with fields: `audit_api_key: str`, `db_path: str = "/data/audit.db"`, `log_level: str = "INFO"`
  - [x] 2.2 Instantiate a module-level `settings = Settings()` singleton so other modules can import it directly

- [x] 3. `logging_config.py` — structured JSON logger
  - [x] 3.1 Implement `JSONFormatter` class (subclass of `logging.Formatter`) whose `format()` method returns a single-line JSON string containing `timestamp` (ISO-8601 UTC), `level`, and `message` fields, plus any extra fields passed via `extra={"extra_fields": {...}}`
  - [x] 3.2 Implement `get_logger(name: str) -> logging.Logger` factory that attaches a `StreamHandler(sys.stdout)` with `JSONFormatter` and sets the level from `settings.log_level`, defaulting to `INFO` for unrecognised values
  - [x] 3.3 Write unit tests in `tests/unit/test_logging.py` verifying: output is valid single-line JSON, mandatory fields are present, extra fields are included, unrecognised `LOG_LEVEL` falls back to `INFO`

- [x] 4. `database.py` — SQLite connection and schema initialisation
  - [x] 4.1 Implement `get_connection(db_path: str) -> sqlite3.Connection` that opens (or creates) the SQLite file, sets `row_factory = sqlite3.Row`, and immediately executes `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`
  - [x] 4.2 Implement `init_schema(conn: sqlite3.Connection) -> None` that executes the full `CREATE TABLE IF NOT EXISTS audit_events` DDL (all 15 columns per design) followed by the three `CREATE INDEX IF NOT EXISTS` statements (`idx_request_id`, `idx_user_id`, `idx_timestamp`) and commits
  - [x] 4.3 Write unit tests in `tests/unit/test_database.py` verifying: `init_schema` is idempotent (safe to call twice), WAL mode is active after `get_connection`, all three indexes exist after `init_schema`, missing parent directory raises a descriptive error before `get_connection` is called

- [x] 5. `models.py` — Pydantic schemas and enums
  - [x] 5.1 Define `LayerEnum(str, Enum)` with values: `api_gateway`, `security`, `router`, `cache`, `inference`, `agent`
  - [x] 5.2 Define `EventTypeEnum(str, Enum)` with values: `request_received`, `auth_pass`, `auth_fail`, `security_block`, `cache_hit`, `inference_start`, `inference_complete`, `response_sent`
  - [x] 5.3 Define `OutcomeEnum(str, Enum)` with values: `pass_` (serialised as `"pass"`), `block`, `flag`
  - [x] 5.4 Define `UUID4_RE` compiled regex pattern: `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (case-insensitive)
  - [x] 5.5 Implement `AuditEventCreate(BaseModel)` with all fields from the design (optional `audit_id`, required `request_id`, optional `timestamp_utc`, optional user/dept/model fields, integer token/latency fields defaulting to 0, enum fields, optional `error_code`, list fields `pii_actions`/`policy_decisions`)
  - [x] 5.6 Add `@field_validator("request_id")` that rejects any value not matching `UUID4_RE` with `ValueError("request_id must be a valid UUID-v4")`
  - [x] 5.7 Implement `AuditEventResponse(AuditEventCreate)` with `audit_id: str` and `timestamp_utc: str` as non-optional
  - [x] 5.8 Implement `BatchWriteRequest(BaseModel)` with `events: list[AuditEventCreate] = Field(min_length=1, max_length=500)`
  - [x] 5.9 Implement `BatchWriteResponse(BaseModel)` with `inserted: int` and `audit_ids: list[str]`
  - [x] 5.10 Implement `SummaryResponse(BaseModel)` with `total_events: int`, `by_outcome: dict[str, int]`, `by_layer: dict[str, int]`
  - [x] 5.11 Write unit tests in `tests/unit/test_models.py` verifying: valid UUIDs pass, non-UUID strings fail with 422-style `ValueError`, invalid enum values are rejected, missing required fields are rejected, optional fields accept `None`, list fields default to `[]`

- [x] 6. `auth.py` — X-API-Key middleware
  - [x] 6.1 Implement `APIKeyMiddleware(BaseHTTPMiddleware)` with class-level `WRITE_PATHS = {"/audit/events", "/audit/events/batch"}`
  - [x] 6.2 In `dispatch`: if `request.url.path in WRITE_PATHS` and `request.method == "POST"`, check for `X-API-Key` header; raise `HTTPException(401, {"error": "missing_api_key"})` if absent; raise `HTTPException(403, {"error": "invalid_api_key"})` if present but non-matching; otherwise call `call_next(request)`
  - [x] 6.3 For all other paths and methods, call `call_next(request)` unconditionally (GET endpoints bypass auth)

- [x] 7. `metrics.py` — Prometheus metric definitions
  - [x] 7.1 Define `writes_total = Counter("llm_audit_writes_total", "Total audit events successfully written", labelnames=["event_type", "layer"])`
  - [x] 7.2 Define `write_latency = Histogram("llm_audit_write_latency_seconds", "Write handler latency", labelnames=["event_type", "layer"], buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5])`

- [x] 8. `metrics_app.py` — separate ASGI metrics application
  - [x] 8.1 Create a lightweight ASGI application (using `starlette.routing.Router` or a bare Starlette app) that serves `GET /metrics` using `prometheus_client.make_asgi_app()` on port 9090
  - [x] 8.2 Ensure this app is independent of the main FastAPI app — it does not mount `APIKeyMiddleware` and does not share the lifespan

- [x] 9. `routers/write.py` — write endpoints
  - [x] 9.1 Implement `POST /audit/events` (status 201): assign `audit_id = event.audit_id or str(uuid.uuid4())` and `timestamp_utc = event.timestamp_utc or datetime.utcnow().isoformat() + "Z"`; serialise `pii_actions` and `policy_decisions` via `json.dumps`; execute `INSERT INTO audit_events ...`; on `sqlite3.IntegrityError` return HTTP 409 `{"error": "duplicate_audit_id", "audit_id": ...}`; on other `sqlite3.Error` return HTTP 500 and emit ERROR log; on success increment `writes_total`, observe `write_latency`, emit INFO log, return `AuditEventResponse`
  - [x] 9.2 Implement `POST /audit/events/batch` (status 201): iterate the input array assigning `audit_id`/`timestamp_utc` where absent; wrap all inserts in a single `BEGIN IMMEDIATE` transaction; on any `sqlite3.IntegrityError` roll back and return HTTP 409; on `sqlite3.Error` roll back and return HTTP 500; on success increment `writes_total` per record, observe `write_latency` once per batch, emit INFO log, return `BatchWriteResponse`
  - [x] 9.3 Measure latency using `time.monotonic()` from handler entry and record in `write_latency` in a `finally` block so failed writes also record elapsed time

- [x] 10. `routers/query.py` — query endpoints
  - [x] 10.1 Implement shared `_validate_time_range(from_: str | None, to: str | None)` utility: parse each as ISO-8601 requiring a `Z` or `+00:00` suffix; return HTTP 422 if malformed; if both present assert `from_ < to` or return HTTP 422 with "invalid time range"
  - [x] 10.2 Implement `GET /audit/requests/{request_id}`: validate path param against `UUID4_RE`, returning HTTP 422 with `detail.request_id` if invalid; query `SELECT * FROM audit_events WHERE request_id = ? ORDER BY timestamp_utc ASC, audit_id ASC`; deserialise `pii_actions`/`policy_decisions` via `json.loads` (emit WARNING + return raw string on parse failure); return list of `AuditEventResponse`
  - [x] 10.3 Implement `GET /audit/events`: accept optional query params `user_id`, `event_type`, `from` (alias `from_`), `to`; validate `from`/`to` via `_validate_time_range`; reject empty-string `user_id` with HTTP 422; build parameterised WHERE clause dynamically; `ORDER BY timestamp_utc DESC LIMIT 1000`; return list of `AuditEventResponse`
  - [x] 10.4 Implement `GET /audit/summary`: accept optional `from`/`to`; validate via `_validate_time_range`; execute two `GROUP BY` aggregations (by `outcome`, by `layer`) plus a `COUNT(*)`; return `SummaryResponse`
  - [x] 10.5 Implement `GET /health`: execute `SELECT 1` with a 200 ms timeout against the DB; return `{"status": "ok", "db": "connected"}` HTTP 200 on success; return `{"status": "degraded", "db": "unreachable"}` HTTP 503 on timeout or connection failure

- [x] 11. `main.py` — FastAPI app factory and lifespan
  - [x] 11.1 Define a `lifespan` async context manager that, before `yield`: validates `settings.audit_api_key` is non-empty (log ERROR + `sys.exit(1)` if not); validates parent directory of `settings.db_path` exists (log ERROR + `sys.exit(1)` if not); opens the SQLite connection via `get_connection`; calls `init_schema`; stores `conn` and `settings` on `app.state`; after `yield`: closes the connection
  - [x] 11.2 Create the FastAPI `app` with `lifespan=lifespan`
  - [x] 11.3 Add a custom exception handler for `RequestValidationError` that returns HTTP 400 (with the validation detail preserved) when the body is not parseable as JSON, and HTTP 422 for other validation errors
  - [x] 11.4 Mount `APIKeyMiddleware` on `app`
  - [x] 11.5 Include `write_router` (prefix `""`) and `query_router` (prefix `""`) on `app`
  - [x] 11.6 Define a `create_app()` factory function that returns the configured `app` — used by tests and by the entrypoint

- [x] 12. `Dockerfile` and entrypoint
  - [x] 12.1 Write a multi-stage `Dockerfile`: base stage `python:3.12-slim`; install `requirements.txt`; copy `audit_store/` package; set `CMD ["sh", "-c", "uvicorn audit_store.main:app --host 0.0.0.0 --port 9200 & uvicorn audit_store.metrics_app:metrics_app --host 0.0.0.0 --port 9090 & wait"]`
  - [x] 12.2 Add a `.dockerignore` file excluding `.git`, `__pycache__`, `tests/`, `*.pyc`, `.kiro/`

- [x] 13. Property-based tests — write properties (Properties 1–8, 11, 15, 16, 18)
  - [x] 13.1 Create `tests/conftest.py` with: an `app` fixture using in-memory SQLite (`:memory:`), a test `AUDIT_API_KEY = "test-key"`, an `httpx.AsyncClient` fixture using `ASGITransport`, and a Prometheus registry reset fixture
  - [x] 13.2 Create `tests/property/test_write_properties.py` with Hypothesis `settings` profile `ci` (`max_examples=100`)
  - [x] 13.3 **[PBT]** Property 1 — `test_valid_single_write_returns_201`: `@given` `request_id=st.uuids(version=4).map(str)`, `layer/event_type/outcome` sampled from enums; assert HTTP 201 and `audit_id` matches `UUID4_RE`
    - **Validates: Requirements 1.1, 1.2, 1.3**
  - [x] 13.4 **[PBT]** Property 2 — `test_audit_id_auto_generation_is_uuid4`: `@given` valid event with no `audit_id`; assert response `audit_id` matches full UUID-v4 regex
    - **Validates: Requirement 1.2**
  - [x] 13.5 **[PBT]** Property 3 — `test_invalid_request_id_returns_422`: `@given` `request_id=st.text().filter(lambda s: not UUID4_RE.match(s))`; assert HTTP 422 with `detail` identifying `request_id`
    - **Validates: Requirement 1.4**
  - [x] 13.6 **[PBT]** Property 4 — `test_invalid_enum_field_returns_422`: `@given` one of `layer/event_type/outcome` replaced with `st.text().filter(lambda s: s not in valid_set)`; assert HTTP 422 with `detail` identifying the bad field
    - **Validates: Requirements 1.5, 1.6, 1.7**
  - [x] 13.7 **[PBT]** Property 5 — `test_non_json_body_returns_400`: `@given` `body=st.binary().filter(lambda b: _is_not_valid_json(b))`; send as raw bytes with `Content-Type: application/json`; assert HTTP 400
    - **Validates: Requirement 1.8**
  - [x] 13.8 **[PBT]** Property 6 — `test_mutating_methods_rejected`: `@given` `method=st.sampled_from(["PUT", "PATCH", "DELETE"])`, `path=st.sampled_from(["/audit/events", "/audit/events/batch", "/audit/requests/some-id"])`; assert HTTP 404 or 405
    - **Validates: Requirement 1.10**
  - [x] 13.9 **[PBT]** Property 7 — `test_batch_atomicity_on_invalid_record`: `@given` list of valid events plus one record with invalid `layer` at a random index; assert HTTP 422 and DB count unchanged
    - **Validates: Requirements 2.1, 2.3**
  - [x] 13.10 **[PBT]** Property 8 — `test_batch_size_boundary`: `@given` `n=st.integers(min_value=501, max_value=600)` for over-limit case → assert 422; `@given` `n=st.integers(min_value=1, max_value=500)` for valid case → assert 201 and `len(audit_ids) == n`
    - **Validates: Requirements 2.1, 2.5**
  - [x] 13.11 **[PBT]** Property 11 — `test_duplicate_audit_id_returns_409`: `@given` valid event; insert once (201); insert again with same `audit_id`; assert HTTP 409 and DB count is still 1 for that `audit_id`
    - **Validates: Requirement 7.4**
  - [x] 13.12 **[PBT]** Property 15 — `test_writes_total_incremented_on_success`: `@given` N valid events; record counter before; insert all; assert counter increased by exactly N; `@given` event with invalid field; assert counter unchanged
    - **Validates: Requirements 8.2, 8.4**
  - [x] 13.13 **[PBT]** Property 16 — `test_write_latency_records_every_attempt`: `@given` mix of valid and invalid events; assert histogram sample count increases by ≥ 1 per attempt and recorded value ≥ 0
    - **Validates: Requirements 8.3, 8.4**
  - [x] 13.14 **[PBT]** Property 18 — `test_auth_enforcement_on_write_endpoints`: `@given` `path=st.sampled_from(["/audit/events", "/audit/events/batch"])`, `body=valid_event_or_batch_strategy()`; request with no header → 401 `{"error": "missing_api_key"}`; request with wrong key → 403 `{"error": "invalid_api_key"}`
    - **Validates: Requirements 10.1, 10.2**

- [x] 14. Property-based tests — query properties (Properties 9, 10, 12–14, 19)
  - [x] 14.1 Create `tests/property/test_query_properties.py` with the `ci` Hypothesis profile
  - [x] 14.2 **[PBT]** Property 9 — `test_request_lifecycle_trace_ordering`: `@given` `st.lists(valid_event_strategy(), min_size=2, max_size=10)` all sharing the same `request_id`; insert all; query `GET /audit/requests/{request_id}`; assert N events returned and array is sorted by `(timestamp_utc, audit_id)` ascending
    - **Validates: Requirement 3.1**
  - [x] 14.3 **[PBT]** Property 10 — `test_json_round_trip_pii_and_policy`: `@given` `pii_actions=st.lists(st.text())`, `policy_decisions=st.lists(st.dictionaries(st.text(), st.text()))`; insert event; query back; assert returned lists equal originals
    - **Validates: Requirements 3.4, 7.3**
  - [x] 14.4 **[PBT]** Property 12 — `test_filter_query_conjunctive_correctness`: `@given` a pool of events with varying `user_id`, `event_type`, timestamps; insert all; query with a random subset of filters; assert every returned event satisfies ALL applied filters
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**
  - [x] 14.5 **[PBT]** Property 13 — `test_filter_query_ordering_and_limit`: `@given` `st.lists(valid_event_strategy(), min_size=0, max_size=50)`; insert all; query `GET /audit/events`; assert `len(result) <= 1000` and result is sorted by `timestamp_utc` descending
    - **Validates: Requirement 4.6**
  - [x] 14.6 **[PBT]** Property 14 — `test_summary_totals_invariant`: `@given` `st.lists(valid_event_strategy(), min_size=0, max_size=50)`; insert all; call `GET /audit/summary`; assert `sum(by_outcome.values()) == total_events` AND `sum(by_layer.values()) == total_events`
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
  - [x] 14.7 **[PBT]** Property 19 — `test_get_endpoints_no_auth_required`: `@given` `path=st.sampled_from(["/audit/events", "/audit/summary", "/health"])`; send GET without `X-API-Key`; assert response is not 401 or 403
    - **Validates: Requirements 10.6, 6.4**

- [x] 15. Property-based tests — logging property (Property 17)
  - [x] 15.1 Create `tests/property/test_logging_properties.py`
  - [x] 15.2 **[PBT]** Property 17 — `test_every_log_entry_is_single_line_json`: `@given` operations from `st.sampled_from(["write_valid", "write_invalid", "query"])` performed against the app with stdout captured; for each captured log line assert: `json.loads(line)` succeeds, `"timestamp"` field is present and parses as ISO-8601, `"level"` is one of `DEBUG/INFO/WARNING/ERROR`, the line contains no embedded newlines
    - **Validates: Requirements 9.1, 9.4**

- [x] 16. Unit tests — models, database, logging
  - [x] 16.1 Extend `tests/unit/test_models.py`: verify `AuditEventCreate` rejects non-UUID `request_id`; verify valid UUID-v4 strings pass; verify all three enums reject out-of-set values; verify `BatchWriteRequest` rejects empty list and list > 500
  - [x] 16.2 Extend `tests/unit/test_database.py`: verify `init_schema` idempotency; verify WAL mode pragma returns `"wal"`; verify all three indexes present via `PRAGMA index_list`; verify `get_connection` on a non-existent parent path raises before writing any file
  - [x] 16.3 Extend `tests/unit/test_logging.py`: verify `JSONFormatter.format` output is parseable JSON; verify mandatory fields (`timestamp`, `level`, `message`) are present; verify `extra_fields` dict is merged at top level; verify invalid `LOG_LEVEL` env value defaults to INFO

- [x] 17. Integration tests
  - [x] 17.1 Create `tests/integration/test_health.py`: test `GET /health` returns 200 + `{"status":"ok","db":"connected"}` with healthy in-memory DB; test that swapping the connection for an unreachable path causes `GET /health` to return 503 + `{"status":"degraded","db":"unreachable"}`
  - [x] 17.2 Create `tests/integration/test_startup.py`: test that `create_app()` / lifespan with `AUDIT_API_KEY=""` logs ERROR and exits; test missing `DB_PATH` parent dir logs ERROR and exits; test that a fresh app with valid config has the `audit_events` table and WAL mode active after lifespan startup
  - [x] 17.3 Create `tests/integration/test_lifecycle.py`: write 6 events (one per `LayerEnum` value) for the same `request_id`; query `GET /audit/requests/{request_id}`; assert exactly 6 events returned, ordered by `timestamp_utc` asc; assert all `pii_actions`/`policy_decisions` deserialise to lists
  - [x] 17.4 Add batch rollback test: pre-insert one event with a known `audit_id`; submit a batch of 3 events where one shares that `audit_id`; assert HTTP 409 and DB event count unchanged

- [x] 18. Helm chart — `llm-platform/charts/audit-store/`
  - [x] 18.1 Create `llm-platform/charts/audit-store/Chart.yaml` with `apiVersion: v2`, `name: audit-store`, `description`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`
  - [x] 18.2 Create `llm-platform/charts/audit-store/values.yaml` with all required defaults from Requirements 11.2: `replicaCount: 1`, image fields, `service.port: 9200`, env fields (`DB_PATH`, `LOG_LEVEL`; no `AUDIT_API_KEY` value committed), persistence block, resources block, observability metrics block (`enabled: true`, `port: 9090`), `autoscaling.enabled: false`, `vault.enabled: false`
  - [x] 18.3 Create `llm-platform/charts/audit-store/templates/_helpers.tpl` defining `audit-store.fullname`, `audit-store.name`, `audit-store.chart`, `audit-store.selectorLabels`, and `audit-store.labels` template helpers following standard Helm conventions
  - [x] 18.4 Create `llm-platform/charts/audit-store/templates/deployment.yaml`: single container with ports 9200 and 9090; `AUDIT_API_KEY` sourced from `secretKeyRef` on `audit-store-secrets`; `DB_PATH` and `LOG_LEVEL` from values; liveness probe `GET /health:9200` (`initialDelaySeconds: 10`, `periodSeconds: 30`); readiness probe `GET /health:9200` (`initialDelaySeconds: 5`, `periodSeconds: 10`); conditional PVC volume mount at `/data` when `persistence.enabled`; no HPA (POC constraint)
  - [x] 18.5 Create `llm-platform/charts/audit-store/templates/service.yaml`: `ClusterIP` Service exposing port 9200 (named `http`) and port 9090 (named `metrics`), with selector from `_helpers.tpl`
  - [x] 18.6 Create `llm-platform/charts/audit-store/templates/networkpolicy.yaml`: allow ingress on port 9200 from namespaces `llm-api-gateway`, `llm-security`, `llm-router`, `llm-cache`, `llm-inference`, `llm-agent-framework`, `llm-governance`; allow ingress on port 9090 from namespace `llm-observability`; deny all other ingress
  - [x] 18.7 Create `llm-platform/charts/audit-store/templates/servicemonitor.yaml`: `ServiceMonitor` targeting port `metrics`, path `/metrics`, `interval: 30s`, selector using `audit-store.selectorLabels`
  - [x] 18.8 Create `llm-platform/charts/audit-store/README.md` documenting: purpose, port layout (9200 API / 9090 metrics), required secret (`AUDIT_API_KEY` via `audit-store-secrets`), all configurable values with types and defaults, example `helm install` command

- [x] 19. Smoke tests and Helm lint
  - [x] 19.1 Create `tests/smoke/test_helm.py` that runs `helm lint llm-platform/charts/audit-store/` via `subprocess` and asserts exit code 0
  - [x] 19.2 Add `helm template` smoke test asserting the rendered output contains a `Deployment`, `Service`, `NetworkPolicy`, and `ServiceMonitor` resource
  - [x] 19.3 Add a startup smoke test: instantiate `create_app()` with in-memory DB; run through the lifespan; assert `app.state` has `conn` set, `SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'` returns a row, and `PRAGMA journal_mode` returns `"wal"`
  - [x] 19.4 Add a startup-refusal smoke test: with `AUDIT_API_KEY` unset (or empty), assert that the lifespan raises `SystemExit` (or calls `sys.exit(1)`)

- [x] 20. Integration validation — end-to-end flow
  - [x] 20.1 Verify the full request lifecycle: a simulated client submits events from all six layers for one `request_id`; `GET /audit/requests/{request_id}` returns all six in ascending time order; `GET /audit/summary` reflects the new totals; `GET /audit/events?user_id=...` filters correctly
  - [x] 20.2 Verify Prometheus metrics endpoint: after writes, `GET http://localhost:9090/metrics` (using the metrics ASGI app directly via test client) returns `Content-Type: text/plain; version=0.0.4` and contains `llm_audit_writes_total` and `llm_audit_write_latency_seconds` lines
  - [x] 20.3 Verify auth boundary: confirm GET endpoints return data without any `X-API-Key` header; confirm POST endpoints return 401/403 without/with-wrong key respectively; confirm `/health` requires no auth
  - [x] 20.4 Verify structured log output: capture stdout during a full write+query cycle and assert every emitted line is valid single-line JSON with `timestamp` and `level` fields

---

## Notes

- **POC constraints in effect:** No HPA, no Vault, no mTLS, no hash chaining, no S3 archival — all deferred to Phase 2. `autoscaling.enabled: false` and `vault.enabled: false` in `values.yaml`.
- **Testing framework:** `pytest` + `hypothesis` (minimum 100 examples per PBT). HTTP test client is `httpx.AsyncClient` with `ASGITransport` — no real network required. SQLite tests use `:memory:` for speed and isolation.
- **Metrics isolation in tests:** Reset the Prometheus registry between test runs using a session-scoped fixture to prevent counter bleed across tests.
- **PBT tasks** are marked `[PBT]` in the task list. Each must have its status updated via `update_pbt_status` after the test run.
- **Startup validation** is enforced inside the FastAPI `lifespan` context manager (before `yield`) so the service refuses to start with a non-zero exit code if `AUDIT_API_KEY` is empty or `DB_PATH` parent directory is missing.
- **Separate ASGI apps:** The metrics app (`metrics_app.py`) runs on port 9090 independently of the main app on port 9200. Both are started in the `Dockerfile` CMD using `&` + `wait`.
- **`hpa.yaml` omitted** from the Helm chart — the Audit Store is a stateful, single-instance service in the POC; HPA would require a StatefulSet and shared storage, deferred to Phase 2.
- **`AUDIT_API_KEY` is never committed** to `values.yaml`. It must be supplied at deploy time via `--set env.AUDIT_API_KEY=<value>` or a Kubernetes `Secret` (`audit-store-secrets`).
- **Property numbering** maps directly to the 19 correctness properties defined in `design.md` (Properties 1–19). Property 19 (GET endpoints need no auth) was defined in the full design.md but the count listed in the design overview says 18 — both are covered here.
