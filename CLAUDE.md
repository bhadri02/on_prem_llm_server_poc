# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A proof-of-concept for an **on-prem enterprise LLM platform**: a chain of small FastAPI microservices that a chat request flows through in strict order, each layer enriching a shared JSON envelope (the **IMF — Internal Message Format**), plus a React admin/portal UI and a local Ollama inference backend, deployed via Docker Compose.

Everything under `.kiro/` (specs, steering docs) has been deleted from the working tree in the current branch — do not assume those design docs still apply; treat the code itself as the source of truth.

## Request flow / service topology

```
Client
  → api_gateway        (:8080)  resolves X-Api-Key via admin_portal (RBAC), rate limit, OpenAI-shaped I/O, normalizes to IMF
  → security_layer      (:8081)  pre-pipeline: injection scan → content safety → PII mask → coarse role check
  → intelligent_router   (:8082)  task classify → model select → policy/entitlement check → health check (Ollama models only) → cache lookup → inference dispatch → cache write/audit (fire-and-forget)
      → cache_service     (:8086)  exact + semantic (embedding-similarity) cache, backed by Redis
      → inference_adapter (:8087)  dispatches to Ollama (http://localhost:11434) OR a cloud provider (Anthropic today) per-request, based on `routing.backend`
  → security_layer (post-pipeline: PII mask response.content)
  → api_gateway → client (also writes audit events)

audit_store        (:9200)  write/query audit trail (SQLite via DB_PATH, one file: audit.db)
model_registry     (:5001)  JSON-file-backed model catalog (models.json / STORAGE_PATH), including provider API keys for cloud models
admin_portal       (:8084)  owns the users/roles/API-keys Postgres DB; aggregates everything else for the UI: chat + playground proxy, audit query, model CRUD, Prometheus metrics summary, config
agent_framework    (:8083)  LangGraph-based tool-calling agent (stub/POC), lives under services/agent-framework/
portal_ui          (:5173)  React + Vite admin/chat UI, talks to admin_portal
```

Every service that touches a request reads/writes the same IMF document (see `api_gateway/schemas/imf.py`, mirrored in `security_layer`, `intelligent_router`, `cache_service`, `inference_adapter`, `services/agent-framework` schemas). Key IMF blocks: `user` (now includes `key_id`, `model_entitlements`, `rate_limit_override` — see RBAC section below), `request`, `governance`, `routing`, `cache`, `response`.

**Partial schema deduplication:** `shared/imf.py` is the canonical definition for the leaf blocks that are genuinely identical everywhere — `IMFMessage`, `IMFUsage`, `IMFResponse`, `IMFGovernance`, `IMFRouting`, `IMFCache`, plus the shared `UUID4_RE` (case-insensitive; standardized platform-wide — a service that previously rejected uppercase UUIDs, `agent_framework`, now accepts them too). Every service imports these from `shared/imf.py` rather than redefining them. `IMFUser`, the nested request block, and the top-level document class remain **local to each service by design**, not an oversight — different services need genuinely different Pydantic strictness for the same conceptual data (e.g. `intelligent_router` requires `user`/`messages` fields other services default or omit; `inference_adapter` keeps everything lenient/optional so it can build custom-shaped 422 errors instead of relying on FastAPI's default validation-error body). All IMF classes, shared and local, use `model_config = ConfigDict(extra="allow")` so a field present in the wire payload but not yet in a given service's local schema passes through instead of being silently stripped at that service's inbound-parse boundary — this was the actual bug class this pass fixed (see the `IMFRouting.backend` note below, which is the general case of it). When adding a new IMF field, add it to `shared/imf.py` if conceptually universal, otherwise to every local schema on the field's actual request path — a field missing from any one hop's schema still gets silently stripped there even with `extra="allow"` upstream, since each service's own inbound parse is a separate boundary.

`NEXT_FEATURES_PLAN.md` (RBAC, per-user API keys, persistent DB, Chat UI) is **implemented** as of this writing — treat it as a design record of what was built and why, not a future-tense proposal. Audit Store has since also been migrated off SQLite onto Postgres (see below) — Model Registry (JSON-file-backed) is the one piece from that plan still not done.

### Audit Store is Postgres-backed (SQLAlchemy Core)

`audit_store/database.py` defines `audit_events` as a SQLAlchemy Core `Table` (not a declarative ORM model — every caller just wants rows in/out) and exposes `get_engine(database_url)`/`init_schema(engine)`/`purge_older_than(engine, cutoff)`. Production points `DATABASE_URL` at the **same Postgres instance/database admin_portal already uses** (`llm_platform`) — distinguished by table name, not a separate database, matching how this POC already runs one shared Postgres container. `pii_actions`/`policy_decisions` are native JSON columns now (no more manual `json.dumps`/`json.loads` with a parse-failure fallback — that fallback existed only to tolerate hand-written SQLite TEXT columns, which no longer apply). Tests run against `sqlite:///:memory:` for speed (via the same Core table — see `tests/audit_store_test_utils.py`); `get_engine()` pins SQLite's in-memory case to `StaticPool` so every checkout shares one connection (a plain `sqlite3` in-memory DB is otherwise private to whichever connection created it — SQLAlchemy's default pool opens a fresh one per checkout). The single-worker `ThreadPoolExecutor`/`run_db` pattern for keeping blocking DB calls off the event loop is unchanged — it was always storage-agnostic, not SQLite-specific.

A later pass added backend support for two static frontend mockups (`user-portal_8.html`, `admin-portal_6.html`) — real Anthropic cloud-model dispatch, model registration/API-key management, admin-wide key listing, and an editable (but not yet live-enforced) role-permission matrix. See `docs/FRONTEND_INTEGRATION.md` for the full endpoint-by-endpoint mapping to those mockups, including explicit known-gap callouts (no true auto-routed chat without a pinned model, no session persistence, no task-type metrics breakdown). Real end-to-end streaming is now implemented (see below) — it is no longer one of those gaps. The cloud-dispatch mechanics are covered next.

### Streaming chat (real end-to-end SSE)

`POST /v1/chat/completions` (api_gateway) and its proxies at `POST /portal/chat/completions` (admin_portal) and `POST /route/stream` / `POST /security/check/stream` / `POST /infer/stream` (the internal hops) support `"stream": true`. Every internal hop speaks one consistent newline-delimited-JSON wire protocol — `{"type": "delta", "content": "..."}`, `{"type": "done", "imf": {...}}`, `{"type": "error", "event": "...", "status_code": N}` — over HTTP 200 (failures are signaled in-band, never via HTTP status, since a streaming response has already committed to 200 by the time an error can occur). Only the api_gateway → browser hop translates this into actual OpenAI-compatible SSE (`chat.completion.chunk` events, `data: [DONE]` terminator); admin_portal's proxy relays api_gateway's already-correct SSE bytes unchanged rather than re-parsing them.

`intelligent_router/pipeline.py::run_streaming_routing_pipeline` is a **deliberate duplicate** of the non-streaming `run_routing_pipeline`'s stages 1-4 (classify → model select → policy/entitlement check → health check → cache lookup), plus a streaming Stage 5 dispatch — kept as a full duplicate rather than refactored into a shared code path to avoid destabilizing the well-tested non-streaming function; keep both in sync by hand if pipeline stage logic changes.

The one genuinely hard problem streaming introduces is PII masking (`security_layer`'s post-pipeline step), which normally only runs once a response is complete. `security_layer/pii.py::StreamingPiiMasker` solves this with chunk-level re-scanning: it holds back a trailing window of un-emitted text (`HOLD_BACK_CHARS`), re-runs the existing `mask_text()` over the full accumulated buffer on each flush (throttled via `MIN_SCAN_INTERVAL_CHARS`, not new detection logic), and only emits text once enough trailing context has accumulated to be confident no PII entity spans the still-held-back boundary. This is a deliberate, documented trade-off (per an explicit user choice of "true real-time streaming with chunk-level re-scan" over simpler alternatives): in rare cases an entity that isn't fully resolvable even within the hold-back window can still leak into a chunk unmasked (and, as with the non-streaming path, Presidio's own NLP model occasionally over-flags a generic noun as an entity — e.g. "Earth" as `LOCATION` — a pre-existing detection-accuracy characteristic, not a streaming-specific bug). `security_layer/routers/pre_check.py::_stream_after_pre_check` wraps this, still calls `run_post_pipeline`-equivalent audit dispatch once the stream ends (via `background_tasks.add_task`, never awaited inline on the streaming path), and updates the final IMF's `response.content`/governance fields to the fully-masked text.

**Real-deployment bug caught by containerized testing (fixed):** `forward_to_router_stream` (unlike the non-streaming `forward_to_router`, which opens its own ephemeral `httpx.AsyncClient` per call) takes a caller-managed shared client — matching the convention `api_gateway`/`intelligent_router`/`inference_adapter` already use via `app.state.http_client`. `security_layer/main.py`'s lifespan never actually created one, since its non-streaming path never needed it; every unit/integration test for the streaming path injected a mock client directly onto `app.state` rather than exercising the real lifespan, so this was never exercised end-to-end until a real request was run through actual Docker containers — where it surfaced as `AttributeError: 'State' object has no attribute 'http_client'` on every real streaming request. Fixed by having `security_layer/main.py` create and store a shared `httpx.AsyncClient` at startup (closed at shutdown), same as its sibling services.

`portal_ui`'s Chat view (`views/ChatView.tsx`) and Playground view (`views/PlaygroundView.tsx`) both consume this via `api/portalClient.ts`'s shared `streamSSE()` loop (`streamChatCompletion()` / `streamPlaygroundChat()` — same wire format, different endpoint), which parses the browser SSE response and invokes `onDelta`/`onDone`/`onError` callbacks (plus an optional `onId`, which Playground uses to recover `request_id` from the first chunk's `id` field for its "View Audit Trail" link), appending each delta to the in-progress assistant message in React state. `admin_portal/routers/playground.py`'s `/playground/chat` and `routers/chat.py`'s `/chat/completions` both stream through the same `services/proxy.py::sse_relay_with_inband_error()` helper — a byte-for-byte relay of api_gateway's SSE that converts an unreachable upstream into an in-band error frame rather than raising. The Playground proxy skips the `llm_portal_*` Prometheus metrics on its streaming branch (consistent with the Chat proxy's streaming branch) — durable accounting for a streamed request lives in api_gateway's own `response_sent` audit event, not here.

### RBAC + per-user API keys + Postgres (admin_portal-owned)

`admin_portal` owns a Postgres DB (`admin_portal/db/models.py`: `users`, `roles`, `user_roles`, `api_keys`, `key_model_entitlements`, `role_permissions`) seeded idempotently at startup (`admin_portal/db/seed.py`) — 4 roles (`viewer`/`analyst`/`developer`/`admin`), the role→task_type permission matrix, and an `admin` user whose key is a SHA-256 hash of whatever `GATEWAY_API_KEY` currently is (so the legacy shared secret keeps working after this change, with unrestricted — i.e. empty — model entitlements).

`api_gateway`'s `AuthMiddleware` no longer compares `X-Api-Key` to a static secret — it calls `api_gateway/services/key_resolver.py::resolve_key`, which hits `GET {ADMIN_PORTAL_URL}/portal/keys/resolve` (guarded by `ADMIN_PORTAL_INTERNAL_KEY`, service-to-service only) and caches both hits and misses in-process for `KEY_CACHE_TTL_SECONDS`. 401 = key not found/revoked/expired, 403 = valid key but zero roles, 503 = Admin Portal unreachable (fails closed — never silently bypasses auth). The resolved profile becomes `request.state.user_profile`, which `api_gateway/services/normalizer.py::build_imf` uses to populate the IMF `user` block server-side (roles/entitlements are never trusted from the client payload).

Enforcement is split across two layers because `task_type` isn't classified until the Router (Stage 1), which runs *after* the Security Layer's pre-pipeline. **These two layers are differently dynamic — don't conflate them:**
- `security_layer/policy.py` — coarse check ("does this identity have any role permitted to call the platform at all") against `ALLOWED_ROLES`, a **hardcoded Python frozenset** (`{"developer", "analyst", "admin"}` — `viewer` is permanently excluded). Not backed by any DB table; changing it requires a code change + `security_layer` restart, full stop.
- `intelligent_router/pipeline.py` Stage 2b (between Model Selection and Health Check) — the real `(role, task_type)` matrix, now **live**: `intelligent_router/services/policy_resolver.py::get_policy_matrix()` polls `GET /portal/policy/matrix` (admin_portal, internal-key-gated) on a TTL cache (`POLICY_CACHE_TTL_SECONDS`, default 15s), falling back to the static `policy_matrix.yaml` seed on any failure. `PATCH /portal/roles/{role}/permissions` therefore takes effect on real enforcement within that TTL window, no restart needed — **except for `viewer`**, which never reaches this stage at all because it's blocked by the coarse gate above. Also runs the model-entitlement check (`user.model_entitlements` empty = all models allowed, backward-compat). Both return 403 (`policy_denied` / `model_not_entitled`) — the two `policy_denied` sources are visually identical except `security_layer`'s nests the body under `"detail"` (FastAPI's `HTTPException` default) while the Router's is flat.

**Portal UI now has a real login screen (Phase 6)** — this used to be a known gap ("no login system"); it no longer is. `admin_portal/routers/auth.py::login` verifies a username/password against `admin_portal/db/models.py::User.password_hash` and sets an httpOnly session cookie (`admin_portal/services/session_auth.py`); `portal_ui/src/views/LoginView.tsx` gates the whole app shell on `GET /portal/auth/me` before rendering. Every `/portal/*` router except `POST /portal/auth/login`, `GET /portal/health`, `GET /portal/config`, and the internal-key-gated `GET /portal/keys/resolve` requires a valid session (`get_current_session`), with `users.py`/`audit.py`/`governance.py`/`metrics_summary.py` additionally requiring the `admin` role (`require_admin`). The session cookie is **not yet marked `Secure`** by default (`SESSION_COOKIE_SECURE=false`) — flip it once a TLS-terminating reverse proxy fronts the deployment, since browsers silently drop `Secure` cookies sent over plain HTTP.

A user's effective model access is **not** a stored field — it's the union of `model_entitlements` across all of that user's *active* API keys (empty entitlements on a key = access to everything). There is no single-call way to grant/revoke a model for a user; it means fanning out to every active key that user owns via `PATCH /portal/users/{id}/keys/{key_id}/models`.

### Cloud model dispatch (Anthropic) + dual source-of-truth gotchas

Models aren't all Ollama anymore. `model_registry` can store a per-model `api_key` (write-only — `PATCH /models/{name}/api-key` / `POST /models` `api_key` field; never echoed back, only `api_key_set: bool` is public) for cloud-backed entries like `claude-sonnet-5` (`backend: anthropic` in `model_matrix.yaml`). Dispatch flow:

1. `intelligent_router/pipeline.py` Stage 2 looks up the selected model's `backend` in `model_matrix.yaml` and stamps `imf["routing"]["backend"]` (`"ollama"` by default) directly onto the IMF — this is the Router telling Inference Adapter which client to use, decided once, in-memory, with no extra network round-trip. Stage 3's live health probe (`check_model_health`) only runs for `backend == "ollama"`; cloud backends are assumed healthy and only actually fail at real dispatch time.
2. `inference_adapter/routers/infer.py` reads `imf.routing.backend`; anything other than `"ollama"` goes through `_dispatch_cloud_backend()` instead of the Ollama client path.
3. `inference_adapter/services/model_secret_resolver.py::resolve_api_key()` fetches the model's provider key from `model_registry`'s internal `GET /models/{name}/secret` endpoint (never the public list/get routes), with an in-process TTL cache (`MODEL_BACKEND_CACHE_TTL_SECONDS`) — same pattern as `api_gateway/services/key_resolver.py`.
4. `inference_adapter/services/anthropic_client.py::AnthropicClient.messages()` calls the real Anthropic Messages API (`POST {ANTHROPIC_BASE_URL}/v1/messages`); `imf_mapper.py::to_anthropic_request`/`to_imf_response_from_anthropic` translate to/from the IMF shape (system prompt pulled out of `messages` into a top-level `system` field, `max_tokens` defaulted since Anthropic requires it, `stop_reason` mapped to the IMF's `finish_reason` vocabulary).

**IMF schema note:** `IMFRouting.backend` had to be added to *every* service's local copy of the IMF Pydantic models on the request's actual path (`intelligent_router`, `inference_adapter`) — a field missing from any one service's schema gets silently stripped by FastAPI at that service's inbound-parse boundary, so it never reaches the next hop. This is the general rule for any new IMF field, not specific to this one.

**`model_matrix.yaml` vs. `model_registry` — FIXED, now live** (same shape of gap as the policy matrix below, same fix). `intelligent_router/services/model_registry_resolver.py::get_model_matrix()` polls `model_registry`'s public `GET /models/` on a TTL cache (`MODEL_REGISTRY_CACHE_TTL_SECONDS`, default 30s) and overlays every `status="active"` record onto the static `model_matrix.yaml`-loaded models (registry entries win on a name collision), falling back to the YAML-only set on any failure. Registering a model via `POST /portal/models` with `status="active"` is now routable — by pinning (`request.model = <name>`) or via a user's model entitlements — within that TTL window, no `model_matrix.yaml` edit or Router restart needed. `task_defaults` (which model auto-routing picks per task type) is deliberately **not** derived from the registry — that's a routing-policy decision, not a fact about a model — so promoting a model to the auto-routed default for a task type still requires a `model_matrix.yaml` edit + restart.

**`policy_matrix.yaml` vs. the `role_permissions` DB table — FIXED, now live** (was the same shape of gap as above). `PATCH /portal/roles/{role}/permissions` (see `admin_portal/routers/roles.py` docstring) persists to Postgres; `intelligent_router` polls it via `services/policy_resolver.py` on a TTL cache instead of only reading the static YAML once at startup — see the RBAC section above for the mechanism and the `security_layer` coarse-gate caveat that still applies to `viewer`.

**Pinned-model routing bug (fixed):** `api_gateway/services/normalizer.py::build_imf()` previously never set `imf.routing.routing_mode = "pinned"` based on the client's `model` field, so `intelligent_router/model_selector.py::select_model()` always took the `"auto"` branch and silently ignored an explicit `model` unless it coincidentally matched the task's auto-default — meaning cloud models like `claude-sonnet-5` were effectively unreachable via pinning. Now fixed: a non-empty `payload.model` sets `routing_mode="pinned"`. Covered by `tests/unit/api_gateway/test_normalizer.py`.

### AI governance/security summary — `GET /portal/governance/summary` (audit-trail-based, not Prometheus)

`admin_portal` exposes a consolidated governance/security/usage endpoint built entirely from `audit_store`'s real SQLite audit trail (`audit_store/routers/query.py::get_governance_summary`, proxied by `admin_portal/routers/governance.py`) — blocked-request counts by reason (`injection_detected`, `content_safety_violation`, `policy_denied`, `model_not_entitled`), PII detection counts, token totals, and per-model served-request counts. It deliberately does **not** depend on Prometheus like `GET /portal/metrics/summary` does — that endpoint's rate fields are `null` whenever no Prometheus server is reachable (the default in local dev), while this one is always populated as long as the platform has processed any traffic. Surfaced in `portal_ui` as `views/GovernanceView.tsx` (nav: "Governance", admin-only). Full field-by-field mapping in `docs/FRONTEND_INTEGRATION.md` §2.1.1.

Building this surfaced two pre-existing bugs in the audit pipeline, both now fixed:
- `intelligent_router`'s Stage 2b denial audit events used `event_type="policy_denied"`/`"model_not_entitled"`, but `audit_store`'s `EventTypeEnum` didn't include either value — every one of those audit POSTs was silently rejected with 422 and swallowed by the fire-and-forget writer (`intelligent_router/audit_client.py` never raises), so these denials never actually reached the audit trail. Fixed by adding both values to `audit_store/models.py::EventTypeEnum`.
- Several audit event builders had schema fields that were always sent empty: `security_layer`'s block events never set `error_code` (so injection/content-safety/policy blocks were indistinguishable in stored data — all just `event_type="security_block"`), and `intelligent_router`'s success events never set `prompt_tokens`/`completion_tokens` (both already present on the IMF's `response.usage` at that point). Fixed in `security_layer/routers/pre_check.py` and `intelligent_router/pipeline.py::_build_routing_audit`/`_build_cache_hit_audit`.

**`api_gateway` audit gap — FIXED, now posts to `audit_store`.** `api_gateway`'s own audit events (`auth_fail`, `auth_pass`, `rate_limited`, `request_received`, `response_sent`) used to be written to stdout only (`api_gateway/services/audit.py::emit_audit_event`) and never reach `audit_store`, making 401/403/429 gateway-layer rejections invisible to both `GET /portal/governance/summary` and `GET /portal/audit/events`. Fixed via `api_gateway/services/audit_client.py::post_audit_event` (same fire-and-forget pattern `security_layer`/`intelligent_router` already used) — `emit_audit_event()` still writes to stdout for local tail/debug visibility, and every call site *additionally* schedules a durable Audit Store write: `chat.py`'s route handler uses FastAPI's injected `BackgroundTasks`; `AuthMiddleware`/`RateLimitMiddleware` (which have no such injection, being middleware not route handlers) attach one via `response.background` instead (`schedule_audit_post()`, careful to `.add_task()` onto a `BackgroundTasks` the route handler may have already put there, not overwrite it). Required two supporting fixes to actually work:
- `audit_store/models.py::EventTypeEnum` was missing `rate_limited`, and `OutcomeEnum` was missing `error` (api_gateway's own `AuditEvent.outcome` allows `"error"` for its 502 case) — both would have hit the exact same "silently swallowed by the fire-and-forget writer" failure mode already documented above for `policy_denied`/`model_not_entitled`. Added both.
- `api_gateway/middleware/logging.py` used to fall back to the literal string `"none"` as `request_id` when the client sent no `X-Request-ID` header (i.e. virtually always) — `audit_store` requires a valid UUID-v4 `request_id` and would reject `"none"`, and separately this meant `AuthMiddleware`/`RateLimitMiddleware`'s own audit events never actually correlated with the *same* request's `request_received`/`response_sent` events from `chat.py` (which mint their own real UUID via `build_imf()`). Fixed by generating a real UUID-v4 fallback in `LoggingMiddleware` and having `build_imf()` accept/reuse `request.state.request_id` (via a new optional `request_id` param) instead of always minting a second, uncorrelated one.

Each service's core control flow lives in a `pipeline.py` (security_layer, intelligent_router) that runs an explicit ordered list of stages with short-circuit blocking semantics — read the module docstring there before changing stage order or return codes; it documents exactly which HTTP status/error code each stage produces.

`shared/` (repo root) holds cross-service code actually imported at runtime: `shared.observability.logging` (structlog setup, `configure_structlog`/`emit`), `shared.observability.metrics`, `shared.observability.middleware` (optional OTel tracing, no-ops if otel packages absent). All services call `configure_structlog(<service_name>, log_level)` at import time in `main.py` and exit(1) with a structured JSON error if required settings are missing (fail-fast startup validation pattern — replicate this pattern if you add a new service).

### Known duplication in `services/agent-framework/`

There are two parallel copies of the agent code: a flat legacy layout directly under `services/agent-framework/` (`main.py`, `config.py`, `agent/`, `tools/`, `routers/`, `schemas/`) and the actual package used at runtime, `services/agent-framework/agent_framework/` (imported as `agent_framework.main:app`, per `run-local.ps1`). **The nested `agent_framework/` package is the one that runs and gets tested** (`agent_framework.main:app`) — treat the flat top-level files as stale/dead unless told otherwise, and double-check which copy you're editing.

## Running the stack locally (Windows / PowerShell)

```powershell
pip install -r requirements.txt                              # one venv covers all services
docker compose -f docker-compose.local.yml up -d              # Redis + Postgres
ollama serve                                                   # separate terminal
ollama pull llama3.2:3b

.\scripts\run-local.ps1                     # starts all 9 services, each in its own terminal window
.\scripts\run-local.ps1 -Service audit_store # start just one
.\scripts\run-local.ps1 -Stop                # kill everything (by process match + port fallback)
```

`run-local.ps1` loads `local.env` into the process environment, then launches each service via `python -m uvicorn <module>:app --reload` in its own window, in dependency order (leaf services → gateway last). It overrides `METRICS_PORT` per service to avoid collisions (inference_adapter=9090, cache_service=9091, agent_framework=9092) and sets `PYTHONPATH` to repo root for `agent_framework` (it runs from `services/agent-framework/` but imports `shared/`). Health-checks every service afterward.

`cache_service`'s embedding model (`sentence-transformers`, `all-MiniLM-L6-v2`) must be cached locally once (first run with internet — it's typically already in `~/.cache/huggingface` after `pip install`'s test run or a prior startup) before running fully offline. `local.env` sets `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` and `cache_service/services/embedding.py::load()` also defaults them via `os.environ.setdefault(...)` — without this, `SentenceTransformer(...)` silently makes a network call to the HF Hub on **every** startup to check for updates, even when the model is already cached, which breaks on a genuinely air-gapped host. Same "fetch once, run offline forever after" pattern as `ollama pull` above — if you ever need to pull a *new* embedding model for the first time, temporarily export `HF_HUB_OFFLINE=0` before starting.

Service ports: `api_gateway:8080`, `security_layer:8081`, `intelligent_router:8082`, `agent_framework:8083`, `admin_portal:8084`, `cache_service:8086`, `inference_adapter:8087`, `audit_store:9200`, `model_registry:5001` (5000 is reserved by Docker Desktop on this machine), Postgres `:5432` (docker-compose only, not a Python service).

Config for every service is driven by `local.env`, loaded fresh by each service's own `pydantic-settings` `Settings` class — when adding a config value, add it to `local.env` **and** the relevant `config.py`. `local.env` is git-tracked (POC placeholder secrets only, e.g. `poc-secret-key`) — for a real credential (an external `DATABASE_URL`, etc.), put it in `local.env.local` instead (gitignored, loaded by `run-local.ps1` *after* `local.env` so it overrides).

## Portal UI (React/Vite)

```bash
cd portal_ui
npm install
npm run dev          # vite dev server, :5173
npm run build         # tsc -b && vite build
npm test              # vitest (watch mode)
npm run test:run       # vitest run (CI mode, single pass)
```

## Tests

Python tests are split across several independent pytest roots — there is no single "run everything" command; run within the relevant root:

```bash
# Root-level test suite (covers most services: api_gateway, security_layer, intelligent_router,
# inference_adapter, cache_service, audit_store, model_registry — run from repo root so imports resolve)
pytest                                    # uses ./pytest.ini -> testpaths=tests
pytest tests/unit/test_policy.py          # single file
pytest tests/unit/test_policy.py::test_x  # single test
pytest tests/cache_service -k semantic    # by keyword, one subtree
pytest tests/integration                  # end-to-end / lifecycle tests
pytest tests/property                     # hypothesis-based property tests

# Admin Portal has its own tests colocated under admin_portal/tests/
pytest admin_portal/tests

# Agent Framework has its own pytest.ini + tests dir, run from its own directory
cd services/agent-framework && pytest
```

`asyncio_mode = auto` is set everywhere pytest-asyncio is used — no need for `@pytest.mark.asyncio` decorators. Tests commonly use `respx` to mock outbound `httpx` calls to downstream services rather than spinning up real dependencies, `fakeredis[aioredis]` for the cache service, and `hypothesis` for property-based tests (see `tests/property/`, `services/agent-framework/tests/test_property_*.py`). **`respx` must be `>=0.22.0`** — `0.21.x` silently fails to match any request at all under the `httpx>=0.28` pinned in this repo (every mocked call falls through as unmocked); if respx-based tests mysteriously all fail, check `pip show respx` before assuming a code regression.

`admin_portal` tests that hit its DB-backed routers (`admin_portal/tests/test_users_and_keys_api.py`) override the `get_db` FastAPI dependency with a temp-file SQLite session per test — they never touch the real Postgres `DATABASE_URL`.

## Kubernetes / Helm deployment (removed)

There used to be an `llm-platform/` Helm chart tree here (10 sub-charts, one per service) plus automation scripts (`scripts/deploy.sh`, `scripts/build-and-push-all.ps1`, `scripts/test-connectivity.py`, `scripts/run-demos.ps1`) for deploying it. Both the charts and those scripts have been **removed** — the charts were stale relative to the current application code (predated the RBAC/Postgres/policy-matrix/governance work documented elsewhere in this file; e.g. `router`'s chart never got the now-required `MODEL_MATRIX_PATH`/`ADMIN_PORTAL_INTERNAL_KEY`/`POLICY_MATRIX_PATH` env vars, `admin-portal`'s chart had no Postgres wiring at all, there was no `portal_ui` chart despite it having a working `Dockerfile`) and had no working deploy target. `git log` has the full pre-removal state if a Kubernetes path is ever revisited — it would need to be rebuilt against the current app, not resurrected as-is.

**`docs/DEPLOYMENT.md`** (automated by `scripts/deploy-onprem.sh`) documents the real, current path: Docker Compose (`docker-compose.prod.yml`), built and verified against the current code. See `scripts/README.md` for what every current script in that directory does.

## Conventions to preserve when editing a service

- Middleware order matters and is documented in each service's `main.py` docstring (e.g. api_gateway: `PrometheusMiddleware → LoggingMiddleware → AuthMiddleware → RateLimitMiddleware → Router`, registered in *reverse* of that order because Starlette wraps last-added-outermost).
- Settings/config loading happens at import time; a missing required env var must fail fast with a structured JSON log line and `sys.exit(1)`, not raise later mid-request.
- Pipeline stages mutate the IMF dict in place and return a small dataclass (`PipelineResult`) carrying `success`/`blocked`, HTTP status, error code, and latency — new stages should follow that shape rather than raising HTTP exceptions directly from deep in the pipeline.
- Fire-and-forget side effects (audit posts, cache writes) go through FastAPI `BackgroundTasks`, not awaited inline on the request path.
- Metrics are exposed via `prometheus_client` mounted at `/metrics` per service (a separate `METRICS_PORT` app for services that also expose a metrics_app.py).
