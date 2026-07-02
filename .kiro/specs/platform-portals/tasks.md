# Implementation Plan: Platform Portals (Layer 10)

## Overview

Build the two sub-components of Layer 10 — **Portal_API** (FastAPI thin reverse proxy on port 8084) and **Portal_UI** (React/Vite SPA) — together with the **Helm chart** at `llm-platform/charts/admin-portal/`. The implementation proceeds in five phases: (1) project scaffolding and shared infrastructure, (2) Portal_API core (health, config, observability, proxy helper), (3) Portal_API feature routers (playground, audit, models, metrics), (4) Portal_UI (views and components), and (5) Helm chart and integration wiring.

---

## Tasks

- [x] 1. Scaffold project structure and shared configuration
  - Create `admin_portal/` directory tree matching the design module layout: `main.py`, `config.py`, `metrics.py`, `middleware/`, `routers/`, `schemas/`, `services/`
  - Create `portal_ui/` Vite project with `src/views/`, `src/components/`, `src/api/`, `src/types/`
  - Create `admin_portal/requirements.txt` pinning: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`, `prometheus-client`, `hypothesis`, `pytest`, `pytest-asyncio`, `respx`, `httpx` (test client)
  - Create `portal_ui/package.json` with React 18, Vite, `react-router-dom`, Vitest, `@testing-library/react`, `fast-check`
  - _Requirements: 11.1_


- [x] 2. Implement Portal_API config, startup validation, and Pydantic schemas
  - [x] 2.1 Write `admin_portal/config.py` using `pydantic-settings`
    - Define `Settings` class with all env vars: `GATEWAY_API_KEY` (required), `API_GATEWAY_URL`, `AUDIT_STORE_URL`, `MODEL_REGISTRY_URL`, `PROMETHEUS_URL`, `GRAFANA_URL`, `LOG_LEVEL`
    - On load, if `GATEWAY_API_KEY` is absent call `sys.exit(1)` and log error to stdout (Req 2.9)
    - If `LOG_LEVEL` not in `{DEBUG, INFO, WARNING, ERROR}` default to `INFO` with a warning log (Req 10.6, 10.7)
    - _Requirements: 2.9, 10.6, 10.7_
  - [x] 2.2 Write all Pydantic schemas in `admin_portal/schemas/`
    - `playground.py`: `Message`, `ChatRequest` (model non-empty str, messages min 1, temperature 0.0–2.0), `ChatResponse`
    - `audit.py`: `AuditEvent`, `AuditEventList`
    - `models.py`: `ModelRecord`, `ModelStatusPatch` (status enum: active/retired/staging)
    - `metrics.py`: `MetricsSummary` (all fields Optional[float])
    - `config.py` (schema): `PortalConfig`
    - `errors.py`: `ErrorResponse` (error, message, upstream, allowed_values)
    - `health.py`: `HealthResponse`
    - _Requirements: 3.1, 5.1, 5.2, 7.2, 8.1_


- [x] 3. Implement Portal_API observability infrastructure
  - [x] 3.1 Write `admin_portal/metrics.py`
    - Define `llm_portal_requests_total` Counter labeled `endpoint`, `status` (values: `2xx`, `4xx`, `5xx`)
    - Define `llm_portal_latency_seconds` Histogram labeled `endpoint`
    - Define `llm_portal_errors_total` Counter labeled `endpoint`, `error_code` (values: `upstream_unavailable`, `validation_error`, `not_found`, `internal_error`)
    - Mount Prometheus `make_asgi_app()` on port 9090 in `main.py`
    - _Requirements: 10.2, 10.3, 10.4, 10.5_
  - [x] 3.2 Write `admin_portal/middleware/logging.py`
    - Implement Starlette middleware that records request start time, intercepts the response, then emits a single-line JSON object `{"endpoint": ..., "status_code": ..., "latency_ms": ...}` to stdout with no embedded newlines
    - Apply to every route including error responses
    - _Requirements: 3.6, 10.1_
  - [ ]* 3.3 Write property test for structured log emission (Property 4)
    - **Property 4: Every completed API request emits a valid structured log entry**
    - Use Hypothesis `@given` with `st.sampled_from` over all route paths and `st.integers` for mock status codes; capture stdout; assert exactly one valid JSON line per request containing `endpoint`, `status_code` (int), `latency_ms` (non-negative number)
    - **Validates: Requirements 3.6, 10.1**
  - [ ]* 3.4 Write property tests for Prometheus counter/histogram correctness (Properties 14, 15, 16)
    - **Property 14: llm_portal_requests_total counter accurately reflects request counts**
    - **Property 15: llm_portal_latency_seconds histogram records non-negative observations**
    - **Property 16: llm_portal_errors_total counter accurately reflects error events**
    - Use a test Prometheus registry; generate sequences of mock requests with `st.lists`; assert counter values match expected totals and histogram observation counts match request counts
    - **Validates: Requirements 10.3, 10.4, 10.5**


- [x] 4. Implement Portal_API generic proxy service and health/config routers
  - [x] 4.1 Write `admin_portal/services/proxy.py`
    - Implement `async_proxy(client, method, url, *, headers=None, json=None, timeout)` using `httpx.AsyncClient`
    - On `httpx.ConnectError` or `httpx.TimeoutException` raise a `ProxyUnavailableError(upstream_name)` that callers convert to HTTP 502 with `ErrorResponse`
    - _Requirements: 3.2, 3.3, 3.4, 4.6, 5.7, 6.4, 7.7, 8.3_
  - [x] 4.2 Write `admin_portal/routers/health.py`
    - `GET /portal/health` → returns `HealthResponse(status="ok")` with HTTP 200; no auth required (Req 1.1, 1.4)
    - `GET /portal/health` returns HTTP 503 with `status="degraded"` and `reason` on startup failure (Req 1.3)
    - _Requirements: 1.1, 1.3, 1.4_
  - [x] 4.3 Write `admin_portal/routers/config.py` (router, not settings)
    - `GET /portal/config` → returns `PortalConfig(grafana_url=settings.GRAFANA_URL)` using default `http://grafana:3000` when env var absent (Req 9.3, 9.4)
    - _Requirements: 9.3, 9.4_
  - [x] 4.4 Write `admin_portal/main.py` — FastAPI app factory
    - Register all routers under `/portal` prefix
    - Register `LoggingMiddleware`
    - Wire Prometheus metrics app to port 9090 via `Mount`
    - Add lifespan startup event that validates config (triggers exit if `GATEWAY_API_KEY` absent)
    - _Requirements: 1.5, 2.9, 10.2_


- [x] 5. Implement Portal_API Playground router
  - [x] 5.1 Write `admin_portal/routers/playground.py`
    - `POST /portal/playground/chat` accepting `ChatRequest` body; validate via Pydantic (returns HTTP 422 on invalid)
    - Forward body unchanged to `{API_GATEWAY_URL}/v1/chat/completions` with `X-API-Key: GATEWAY_API_KEY` header and 30-second timeout using `proxy.py`
    - Propagate upstream status code and body unchanged to caller (Req 3.3)
    - On upstream failure return HTTP 502 with `ErrorResponse(upstream="api-gateway")` (Req 3.4)
    - Emit JSON log entry on completion (handled by middleware)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - [ ]* 5.2 Write property test for ChatRequest field validation (Property 1)
    - **Property 1: ChatRequest field validation**
    - Use Hypothesis `st.text`, `st.lists`, `st.floats` to generate all combinations of valid/invalid `model`, `messages`, `temperature`; assert HTTP 422 when any constraint violated, forward call when all valid
    - **Validates: Requirements 2.2, 2.3, 3.1**
  - [ ]* 5.3 Write property test for playground proxy faithfulness (Properties 2 and 3)
    - **Property 2: Playground proxy faithfully forwards with API key**
    - **Property 3: Upstream response status and body are propagated unchanged**
    - Mock `httpx.AsyncClient` with `respx`; use Hypothesis to generate random valid `ChatRequest` bodies and random upstream response status codes/bodies; assert forwarded request body is byte-for-byte identical, `X-API-Key` header matches `GATEWAY_API_KEY`, and caller receives exact upstream status and body
    - **Validates: Requirements 2.4, 3.2, 3.3, 3.5**


- [x] 6. Implement Portal_API Audit proxy router
  - [x] 6.1 Write `admin_portal/routers/audit.py`
    - `GET /portal/audit/events` with optional query params: `from` (ISO-8601), `to` (ISO-8601), `limit` (int, default 50)
    - Validate `limit` ∈ [1, 200]; return HTTP 400 with `allowed_values: ["1", "200"]` if out of range (Req 5.3)
    - Validate `from`/`to` as ISO-8601 and `from` ≤ `to`; return HTTP 400 on failure (Req 5.6)
    - Proxy to `{AUDIT_STORE_URL}/events?from=&to=&limit=`; return sorted-descending result (Req 4.3, 4.4)
    - `GET /portal/audit/requests/{request_id}`
    - Validate `request_id` matches UUID v4 regex; return HTTP 400 if invalid (Req 5.4)
    - Proxy to `{AUDIT_STORE_URL}/requests/{request_id}`; return HTTP 200 with empty list if no records (Req 5.5)
    - On upstream failure return HTTP 502 with `ErrorResponse(upstream="audit-store")` (Req 5.7)
    - _Requirements: 4.3, 4.4, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - [ ]* 6.2 Write property test for audit limit validation (Property 6)
    - **Property 6: Audit limit parameter is validated in [1, 200]**
    - Use `st.integers()` to generate limit values across full integer range; assert HTTP 400 with correct error body for values outside [1, 200], forwarding for values within range
    - **Validates: Requirements 5.1, 5.3**
  - [ ]* 6.3 Write property test for request_id UUID v4 validation (Property 7)
    - **Property 7: Audit request_id validated as UUID v4 format**
    - Use `st.from_regex` to generate UUID v4 conforming strings and `st.text` for non-conforming strings; assert forward vs HTTP 400 respectively
    - **Validates: Requirements 5.2, 5.4**
  - [ ]* 6.4 Write property test for date range validation (Property 8)
    - **Property 8: Date range parameters are validated for format and ordering**
    - Generate ISO-8601 datetime pairs (including `from > to` cases) and malformed strings; assert HTTP 400 on invalid format or inverted range
    - **Validates: Requirements 5.6**
  - [ ]* 6.5 Write property test for audit results sort order (Property 5)
    - **Property 5: Audit event results are always sorted descending by timestamp_utc**
    - Mock Audit Store to return `AuditEvent` lists with shuffled timestamps generated by `st.lists(st.datetimes())`; assert returned list is sorted descending for any filter combination
    - **Validates: Requirements 4.4**


- [x] 7. Implement Portal_API Models proxy router
  - [x] 7.1 Write `admin_portal/routers/models.py`
    - `GET /portal/models` → proxy to `{MODEL_REGISTRY_URL}/` with 5-second timeout; return model list unchanged; HTTP 502 with `upstream="model-registry"` on failure (Req 6.4, 7.7)
    - `PATCH /portal/models/{name}/status` accepting `ModelStatusPatch` body
    - Validate `status` ∈ `{active, retired, staging}`; return HTTP 422 with `allowed_values` list if invalid (Req 7.5)
    - Forward to `{MODEL_REGISTRY_URL}/models/{name}/status`; propagate response unchanged (Req 7.3)
    - Return HTTP 404 with `ErrorResponse` identifying model name if registry returns 404 (Req 7.6)
    - Return HTTP 502 with `upstream="model-registry"` on connectivity failure (Req 7.7)
    - _Requirements: 6.4, 7.1, 7.2, 7.3, 7.5, 7.6, 7.7_
  - [ ]* 7.2 Write property test for model proxy round-trip (Property 9)
    - **Property 9: Model proxy round-trip preserves request and response**
    - Mock Model Registry with `respx`; use `st.text` for model names and `st.sampled_from(["active","retired","staging"])` for status; assert body forwarded unchanged and response propagated unchanged for both `GET /portal/models` and `PATCH`
    - **Validates: Requirements 6.4, 7.1, 7.2, 7.3**
  - [ ]* 7.3 Write property test for model status enum validation (Property 11)
    - **Property 11: Model status PATCH rejects invalid enum values**
    - Use `st.text()` to generate arbitrary status strings; assert HTTP 422 with `allowed_values` for any string not in `{active, retired, staging}`, and forwarding for valid values
    - **Validates: Requirements 7.5**


- [x] 8. Implement Portal_API Metrics Summary router
  - [x] 8.1 Write `admin_portal/routers/metrics_summary.py`
    - `GET /portal/metrics/summary`
    - Issue three Prometheus instant queries via `{PROMETHEUS_URL}/api/v1/query` with 5-second timeout:
      - `request_rate`: `rate(llm_api_gateway_requests_total[60s])`
      - `error_rate`: `rate(llm_api_gateway_errors_total[60s]) / rate(llm_api_gateway_requests_total[60s])` — return `null` if denominator is zero
      - `cache_hit_rate`: cache hits / total cache lookups from `llm_cache_requests_total` — return `null` if denominator is zero
    - Return `MetricsSummary`; HTTP 502 with `upstream="prometheus"` on failure (Req 8.3)
    - _Requirements: 8.1, 8.2, 8.3_
  - [ ]* 8.2 Write property test for metrics computation correctness (Property 12)
    - **Property 12: Metrics summary computes rates correctly from Prometheus values**
    - Use `st.floats(min_value=0)` to generate `(numerator, denominator)` pairs including zero denominators; assert `error_rate` and `cache_hit_rate` are `null` when denominator is zero, and that non-null values are in [0.0, 1.0]
    - **Validates: Requirements 8.1, 8.2**

- [x] 9. Checkpoint — Portal_API complete
  - Ensure all Portal_API tests pass with `pytest admin_portal/tests/ -v`
  - Verify `GET /portal/health` returns 200, `/portal/config` returns grafana_url, `/metrics` on port 9090 returns Prometheus text format
  - Ensure all tests pass, ask the user if questions arise.


- [x] 10. Implement Portal_UI shared infrastructure
  - [x] 10.1 Write `portal_ui/src/types/index.ts`
    - Define TypeScript interfaces: `Message`, `ChatReq`, `AuditEvent`, `ModelRecord`, `MetricsSummary`, `PortalConfig`, `ErrorResponse` matching Pydantic schemas exactly
    - _Requirements: 12.1_
  - [x] 10.2 Write `portal_ui/src/api/portalClient.ts`
    - Implement typed `fetch` wrapper for all Portal_API calls: `postChat`, `getAuditEvents`, `getAuditRequest`, `getModels`, `patchModelStatus`, `getMetricsSummary`, `getConfig`
    - On non-2xx response, extract `message` from JSON body (fall back to raw body text) and throw a typed `ApiError` with `status` and `message`
    - _Requirements: 12.3_
  - [x] 10.3 Write `portal_ui/src/components/ErrorBanner.tsx`
    - Dismissible banner that accepts `statusCode` and `message` props; renders until user dismisses; scoped per view (does not bubble across route boundaries)
    - _Requirements: 12.3_
  - [x] 10.4 Write `portal_ui/src/components/LoadingSpinner.tsx`
    - Simple accessible loading indicator component used by Playground and Model Viewer while fetching
    - _Requirements: 2.5, 6.7_
  - [x] 10.5 Write `portal_ui/src/main.tsx` and `portal_ui/src/App.tsx`
    - Configure `react-router-dom` with routes: `/` and `/playground` → `PlaygroundView`, `/audit` → `AuditView`, `/models` → `ModelView`, `/metrics` → `MetricsView`
    - `App.tsx`: persistent nav bar with links + `<Outlet>` for view content; no login/auth screen
    - _Requirements: 12.1, 12.2, 12.4_


- [x] 11. Implement Portal_UI Playground view
  - [x] 11.1 Write Playground components and view
    - `portal_ui/src/components/playground/ModelSelector.tsx`: dropdown populated from `GET /portal/models`; on fetch error show error message and disable Send button (Req 2.1)
    - `portal_ui/src/components/playground/TemperatureInput.tsx`: float input 0.0–2.0, default 0.7 (Req 2.2)
    - `portal_ui/src/components/playground/ChatWindow.tsx`: message input (max 4000 chars), Send button, response display area, `request_id` display, "View Audit Trail" button
    - `portal_ui/src/views/PlaygroundView.tsx`: compose the three components; disable Send while in-flight or models not loaded; show `LoadingSpinner` during request (Req 2.3, 2.5)
    - On success: display assistant response and `request_id`; show "View Audit Trail" button that navigates to `/audit?request_id=<uuid>` (Req 2.6, 2.7)
    - On HTTP error: display status code and error detail text via `ErrorBanner` (Req 2.8)
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8_
  - [ ]* 11.2 Write unit tests for PlaygroundView components
    - Test: model selector disables Send on fetch error; temperature input rejects out-of-range values; Send button disabled while in-flight; "View Audit Trail" button navigates to correct URL; error banner shows status and detail on HTTP error
    - _Requirements: 2.1, 2.5, 2.7, 2.8_


- [x] 12. Implement Portal_UI Audit Viewer
  - [x] 12.1 Write Audit Viewer components and view
    - `portal_ui/src/components/audit/AuditFilters.tsx`: from/to datetime pickers, layer dropdown, outcome dropdown (`pass`, `block`); triggers re-fetch on change (Req 4.2, 4.4)
    - `portal_ui/src/components/audit/AuditTable.tsx`: table with columns `timestamp_utc`, `request_id` (clickable), `layer`, `event_type`, `user_id`, `outcome`, `latency_ms`; empty-state message when no records (Req 4.1, 4.8)
    - `portal_ui/src/components/audit/AuditDetailPanel.tsx`: overlay panel showing all records for a clicked `request_id`, fetched from `GET /portal/audit/requests/{request_id}` (Req 4.5)
    - `portal_ui/src/views/AuditView.tsx`: compose components; on load fetch last 24h with limit 50; read `?request_id=` URL param to pre-populate detail panel (Req 4.3, 2.7)
    - On Audit Store error display `ErrorBanner` with status and detail (Req 4.6, 4.7)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_
  - [ ]* 12.2 Write unit tests for AuditView components
    - Test: table renders correct columns; filter change triggers re-fetch; empty-state shown when list empty; clicking request_id opens detail panel; error banner shown on 502; detail panel pre-populated from URL param
    - _Requirements: 4.1, 4.4, 4.5, 4.7, 4.8_


- [x] 13. Implement Portal_UI Model Viewer
  - [x] 13.1 Write Model Viewer components and view
    - `portal_ui/src/components/models/StatusBadge.tsx`: renders `active` as green badge, `staging` as yellow badge, `retired` as grey badge (Req 6.3)
    - `portal_ui/src/components/models/ModelTable.tsx`: table with columns `name`, `version`, `backend`, `tasks` (comma-separated), `status` (StatusBadge), action buttons; [Retire] shown for `active`/`staging`; [Activate] shown for `retired`/`staging` (Req 6.1, 6.2, 6.5, 6.6)
    - `portal_ui/src/views/ModelView.tsx`: fetch models on load with `LoadingSpinner` (disables buttons while loading); on [Retire]/[Activate] click call `PATCH /portal/models/{name}/status` then re-fetch model list within 2 seconds; on success update row immediately; on error show dismissible error message and leave row unchanged (Req 6.7, 6.8, 6.9, 7.4)
    - Display empty-state or error-state when list is empty or fetch fails (Req 6.10)
    - _Requirements: 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 7.4_
  - [ ]* 13.2 Write property test for model status action button rendering (Property 10)
    - **Property 10: Model status action buttons follow lifecycle rules**
    - Use `fast-check` `fc.constantFrom("active","retired","staging")` combined with arbitrary model name/version/backend/tasks values; render `ModelTable` with each `ModelRecord`; assert [Retire] present iff status ∈ {active, staging} and [Activate] present iff status ∈ {retired, staging}
    - **Validates: Requirements 6.5, 6.6**
  - [ ]* 13.3 Write unit tests for ModelView components
    - Test: StatusBadge renders correct colour classes; loading spinner shown and buttons disabled while fetching; re-fetch triggered after successful PATCH; error message shown on PATCH failure; empty-state shown on empty list
    - _Requirements: 6.3, 6.7, 6.8, 6.9, 6.10, 7.4_


- [x] 14. Implement Portal_UI Metrics view (Grafana embed)
  - [x] 14.1 Write `portal_ui/src/views/MetricsView.tsx`
    - On mount call `GET /portal/config` to retrieve `grafana_url`
    - Render `<iframe>` with `src={grafana_url}/d/poc-overview/llm-platform-poc?orgId=1&kiosk`, full container width, minimum height 600px (Req 9.1, 9.2)
    - Attach `onError` handler to iframe: on error replace iframe area with static fallback message (Req 9.5, 12.5)
    - _Requirements: 9.1, 9.2, 9.5, 12.5_
  - [ ]* 14.2 Write property test for Grafana iframe src construction (Property 13)
    - **Property 13: Grafana iframe src is constructed from Portal config value**
    - Use `fast-check` `fc.webUrl()` to generate arbitrary `grafana_url` values; mock `GET /portal/config` to return each; assert rendered `<iframe src>` equals `{grafana_url}/d/poc-overview/llm-platform-poc?orgId=1&kiosk` exactly
    - **Validates: Requirements 9.1, 9.4**
  - [ ]* 14.3 Write unit test for Grafana fallback behaviour
    - Simulate iframe `error` event; assert fallback message replaces iframe and other views are unaffected
    - _Requirements: 9.5, 12.5_


- [ ] 15. Implement Portal_UI error banner property test
  - [ ]* 15.1 Write property test for non-2xx error banner (Property 17)
    - **Property 17: Non-2xx Portal_API responses always trigger the error banner**
    - Use `fast-check` `fc.integer({min:400, max:599})` and `fc.record({message: fc.string()})` to generate random error responses; render each view with mocked `portalClient` returning the error; assert `ErrorBanner` displays correct status code and message; assert banner persists until dismissed
    - **Validates: Requirements 12.3**

- [x] 16. Checkpoint — Portal_UI complete
  - Run `npx vitest --run` and confirm all component and property tests pass
  - Verify SPA routes: `/`, `/playground`, `/audit`, `/models`, `/metrics` each render without errors
  - Ensure all tests pass, ask the user if questions arise.


- [x] 17. Build Docker images for Portal_API and Portal_UI
  - [x] 17.1 Write `admin_portal/Dockerfile`
    - Multi-stage: `python:3.12-slim` build stage installs deps from `requirements.txt`; final stage copies app, runs as UID 1000, exposes port 8084 and 9090, sets `CMD ["uvicorn", "admin_portal.main:app", "--host", "0.0.0.0", "--port", "8084"]`
    - No `root` user; `securityContext.runAsNonRoot: true` compatible (Req 11.7)
    - _Requirements: 11.7_
  - [x] 17.2 Write `portal_ui/Dockerfile`
    - Multi-stage: `node:20-alpine` build stage runs `npm ci && npm run build`; final stage uses `nginx:alpine` to serve `dist/` as static files on port 80; configure nginx to serve `index.html` for all paths (SPA fallback)
    - Run nginx as non-root user (Req 11.7)
    - _Requirements: 11.7, 12.2_


- [x] 18. Create Helm chart at `llm-platform/charts/admin-portal/`
  - [x] 18.1 Write `Chart.yaml`, `_helpers.tpl`, and `values.yaml`
    - `Chart.yaml`: name `admin-portal`, POC `appVersion`, standard metadata
    - `values.yaml`: `replicaCount: 1`, `autoscaling.enabled: false`, image, service port 8084, ingress host `llm-portal.local`, resource requests/limits as specified, all env vars (`API_GATEWAY_URL`, `AUDIT_STORE_URL`, `MODEL_REGISTRY_URL`, `GRAFANA_URL`, `GATEWAY_API_KEY`, `LOG_LEVEL`), `vault.enabled: false` (Req 11.1–11.6)
    - `_helpers.tpl`: standard label and selector helpers
    - _Requirements: 11.1, 11.4, 11.5, 11.6_
  - [x] 18.2 Write `templates/deployment.yaml`
    - Single container with env vars sourced from `values.yaml` (or `secretKeyRef` for `GATEWAY_API_KEY`)
    - `securityContext.runAsNonRoot: true`, `runAsUser: 1000`; no `hostNetwork`, `hostPID`, `hostIPC`, `privileged` (Req 11.7)
    - Liveness and readiness probes: `GET /portal/health`, `initialDelaySeconds: 5`, `periodSeconds: 10`, `failureThreshold: 3` (Req 11.8)
    - Resource requests `cpu: 100m`, `memory: 256Mi`; limits `cpu: 500m`, `memory: 512Mi` (Req 11.6)
    - _Requirements: 11.5, 11.6, 11.7, 11.8_
  - [x] 18.3 Write `templates/service.yaml`, `templates/ingress.yaml`, `templates/servicemonitor.yaml`
    - `service.yaml`: ClusterIP, port 8084 (Req 11.2)
    - `ingress.yaml`: NGINX Ingress class, host `llm-portal.local`, all paths to portal service (Req 11.3)
    - `servicemonitor.yaml`: ServiceMonitor targeting `/metrics` endpoint on port 9090 for Prometheus scraping (Req 11.9)
    - _Requirements: 11.2, 11.3, 11.9_
  - [ ]* 18.4 Run `helm lint` and write template snapshot assertions
    - Run `helm lint llm-platform/charts/admin-portal/` and assert it passes with no errors
    - Write snapshot test using `helm template` output: assert service port is 8084, ingress host is `llm-portal.local`, resource limits match spec, securityContext has `runAsNonRoot: true`, liveness/readiness probe path is `/portal/health`, ServiceMonitor target port is 9090
    - _Requirements: 11.1–11.9_


- [x] 19. Integration wiring — wire Portal_UI to Portal_API and verify end-to-end
  - [x] 19.1 Configure Vite dev proxy and production API base URL
    - In `portal_ui/vite.config.ts` add proxy rule: `/portal` → `http://localhost:8084` for local development
    - Ensure `portalClient.ts` uses relative paths (`/portal/...`) so the Vite proxy handles routing in dev and nginx reverse-proxy handles it in production
    - _Requirements: 12.1, 12.2_
  - [ ]* 19.2 Write integration smoke tests using `pytest` with `respx` downstream stubs
    - Spin up Portal_API with all downstream calls mocked via `respx`; exercise full round-trips: Playground → API Gateway stub → response displayed; Audit Viewer → Audit Store stub; Model Viewer → Model Registry stub; `/portal/metrics/summary` → Prometheus stub; assert each returns expected shape
    - _Requirements: 3.2, 4.3, 6.4, 8.1_

- [x] 20. Final checkpoint — full stack
  - Run `pytest admin_portal/ -v` and `npx vitest --run` and confirm all tests pass
  - Run `helm lint llm-platform/charts/admin-portal/` and confirm no errors
  - Ensure all tests pass, ask the user if questions arise.


---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP build.
- Each task references specific requirements for traceability.
- Checkpoints (tasks 9, 16, 20) ensure incremental validation before moving to the next phase.
- Property tests use **Hypothesis** (`@given`, `@settings(max_examples=100)`) for Portal_API and **fast-check** (`fc.assert`, `fc.property`) for Portal_UI.
- Unit tests use **pytest + httpx test client** (Portal_API) and **Vitest + React Testing Library** (Portal_UI).
- All Portal_API property test functions must include a comment `# Feature: platform-portals, Property N: <title>` matching the design document.
- The `GATEWAY_API_KEY` env var must always be set when running Portal_API — tests should set it to a dummy value (e.g., `"test-key"`) via environment or fixture.
- Downstream service base URLs default to cluster-internal DNS names; override with `API_GATEWAY_URL`, `AUDIT_STORE_URL`, `MODEL_REGISTRY_URL`, `PROMETHEUS_URL`, `GRAFANA_URL` env vars for local development.
- The Portal_UI is served as static files; no SSR. The nginx SPA fallback (`try_files $uri /index.html`) is required for react-router deep links to work.


## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2"] },
    { "id": 2, "tasks": ["3.1", "3.2", "4.1", "10.1"] },
    { "id": 3, "tasks": ["3.3", "3.4", "4.2", "4.3", "4.4", "10.2", "10.3", "10.4"] },
    { "id": 4, "tasks": ["5.1", "6.1", "7.1", "8.1", "10.5"] },
    { "id": 5, "tasks": ["5.2", "5.3", "6.2", "6.3", "6.4", "6.5", "7.2", "7.3", "8.2", "11.1", "12.1", "13.1", "14.1"] },
    { "id": 6, "tasks": ["11.2", "12.2", "13.2", "13.3", "14.2", "14.3", "15.1"] },
    { "id": 7, "tasks": ["17.1", "17.2"] },
    { "id": 8, "tasks": ["18.1"] },
    { "id": 9, "tasks": ["18.2", "18.3"] },
    { "id": 10, "tasks": ["18.4", "19.1"] },
    { "id": 11, "tasks": ["19.2"] }
  ]
}
```
