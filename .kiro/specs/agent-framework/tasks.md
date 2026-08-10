# Implementation Plan: Agent Framework (Layer 6 — POC)

## Overview

Implement the Agent Framework as a standalone FastAPI microservice (`services/agent-framework/`) that receives agentic IMF requests from the Intelligent Router, runs a LangGraph ReAct loop with three bound tools (calculator, get_current_time, web_search), and returns a fully-populated output IMF. All LLM sub-calls are routed through the Router's `/v1/chat/completions` endpoint. A separate Prometheus metrics ASGI app runs on port 9090.

## Tasks

- [x] 1. Set up project structure, configuration, and core infrastructure
  - Create the full `services/agent-framework/` directory layout as defined in the design (all sub-packages with `__init__.py` files)
  - Implement `config.py` with `pydantic-settings` `Settings` class covering all env vars (`ROUTER_URL`, `GATEWAY_API_KEY`, `TOOL_CATALOG_PATH`, `LOG_LEVEL`, `MAX_AGENT_STEPS`, `PORT`, `METRICS_PORT`, `agent_sub_call_timeout_seconds`)
  - Implement `logging_config.py` with a JSON structured logger factory respecting `LOG_LEVEL`; fall back to `INFO` and emit one WARNING for unrecognized level values
  - Create `requirements.txt` with pinned versions: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `langchain-openai`, `langgraph`, `langchain-core`, `httpx`, `prometheus-client`, `pyyaml`, `hypothesis`
  - Create `Dockerfile` using a Python base image, copying all service sources and installing `requirements.txt`
  - _Requirements: 12.3, 12.4, 14.1_

  - [x] 1.1 Write unit tests for config and logging
    - Test that `LOG_LEVEL` values `DEBUG`, `INFO`, `WARNING`, `ERROR` are accepted
    - Test that an unrecognized `LOG_LEVEL` defaults to `INFO` and emits one WARNING record
    - _Requirements: 12.3, 12.4_

- [x] 2. Implement IMF Pydantic models and schemas
  - Implement `schemas/imf.py` with all Pydantic models: `IMFMessage`, `IMFUsage`, `IMFResponse`, `IMFGovernance`, `IMFRouting`, `IMFCache`, `IMFUser`, `IMFRequest`, `IMFDocument`
  - Add UUID v4 regex validator on `request_id` and `min_length=1` on `request.messages`
  - Ensure `extensions` dict is present and accessible for the `agentic` flag gate
  - _Requirements: 1.1, 1.3, 1.4, 10.1_

  - [x] 2.1 Write unit tests for IMF models
    - Test valid IMF parses correctly
    - Test `request.messages` empty array raises validation error
    - Test `request_id` non-UUID v4 raises validation error
    - _Requirements: 1.3, 1.4, 10.1_

- [x] 3. Implement tool implementations and catalog
  - [x] 3.1 Implement `tools/calculator.py` with `_SAFE_NODES` whitelist, `_SafeEvaluator` AST visitor, `_safe_eval()` function, and `@tool`-decorated `calculator()` function
    - Guard empty/whitespace-only expressions with a fast error return
    - Handle `SyntaxError`, `ValueError`, `ZeroDivisionError`, `OverflowError`
    - Format integer results without decimal point; float results without trailing zeros using `f"{result:g}"`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [x] 3.2 Write property test for calculator safety (Property 5)
    - **Property 5: calculator never calls eval or exec — pure AST evaluation**
    - **Validates: Requirements 6.2, 6.3, 6.4**
    - Use `@given(st.text())` to generate arbitrary expression strings including builtins, `__import__`, attribute access, subscript access, function calls
    - Patch `builtins.eval` and `builtins.exec` to assert they are never called
    - Verify `_safe_eval()` returns an error string for all non-permitted constructs

  - [ ]* 3.3 Write property test for calculator output formatting (Property 6)
    - **Property 6: calculator output formatting is correct for all result types**
    - **Validates: Requirements 6.7, 6.8**
    - Use `@given(st.integers(), st.integers())` and `@given(st.floats(allow_nan=False, allow_infinity=False))` to generate permitted arithmetic expressions
    - Verify integer results contain no `"."` character; float results contain no unnecessary trailing zeros

  - [ ] 3.4 Write unit tests for calculator
    - `"2 + 2"` → `"4"` (integer, no decimal)
    - `"1/0"` → error string containing "zero"
    - `"2.5 * 2"` → `"5"` (integer result of float arithmetic)
    - `"1.5 + 1.0"` → `"2.5"` (no trailing zeros)
    - `"__import__('os').system('ls')"` → error string, never executes
    - Empty string → error string
    - _Requirements: 6.1–6.8_

  - [x] 3.5 Implement `tools/get_time.py` with `@tool`-decorated `get_current_time()` function
    - Return `datetime.now(timezone.utc).isoformat()` which includes `+00:00` offset
    - Wrap in try/except to return error string if system clock is unavailable
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 3.6 Write unit tests for get_current_time
    - Verify return value is non-empty string
    - Verify `datetime.fromisoformat(result)` parses without error
    - Verify result contains `+00:00` UTC offset
    - _Requirements: 7.2, 7.3, 7.4_

  - [x] 3.7 Implement `tools/web_search.py` with `@tool`-decorated `web_search()` function
    - Return mock template string embedding the query value
    - Truncate query to 1000 chars before formatting
    - Guard empty/whitespace-only queries with error return
    - No outbound HTTP calls
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 3.8 Write property test for web_search (Property 7)
    - **Property 7: web_search result always contains query as substring**
    - **Validates: Requirements 8.2, 8.3, 8.5**
    - Use `@given(st.text(min_size=1).filter(lambda s: s.strip()))` to generate non-empty non-whitespace-only query strings including unicode and special characters
    - Verify `query in result` is `True` for all generated queries
    - Verify no outbound HTTP calls are made (mock `httpx.AsyncClient`)

  - [x] 3.9 Write unit tests for web_search
    - `web_search(query="hello")` → result contains `"hello"`
    - `web_search(query="")` → error string
    - `web_search(query="   ")` → error string
    - Query > 1000 chars is truncated, result still contains first 1000 chars as substring
    - _Requirements: 8.1–8.5_

- [x] 4. Checkpoint — Ensure all tool tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement tool registry and catalog
  - Create `tools/catalog.yaml` declaring `calculator`, `get_current_time`, and `web_search` with `name`, `description`, and `parameters` blocks
  - Implement `tools/registry.py` with `load_tool_registry()` that reads YAML, validates `name`/`description` fields, maps names to LangChain `BaseTool` implementations, logs ERROR and returns `None` on any failure
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 5.1 Write unit tests for tool registry
    - Loading a valid `catalog.yaml` returns a dict with all three tool entries
    - Missing `name` field in an entry → `None` returned and ERROR logged
    - Missing `description` field → `None` returned and ERROR logged
    - Tool name in catalog with no implementation → `None` returned and ERROR logged
    - File not found → `None` returned and ERROR logged
    - Malformed YAML → `None` returned and ERROR logged
    - _Requirements: 5.2, 5.3_

- [x] 6. Implement session store
  - Implement `agent/session_store.py` with `StepRecord` dataclass, `SessionStore` class using `asyncio.Lock`, and module-level singleton `session_store`
  - `init_session()` replaces any existing entry with an empty list
  - `append_step()` is a no-op for unknown `session_id`
  - `get_steps()` returns a copy of the step list
  - _Requirements: 9.1, 9.2, 9.4, 9.5_

  - [x] 6.1 Write unit tests for session store
    - Two `init_session()` calls with the same `session_id` produce an empty list (old entry replaced)
    - `append_step()` with unknown `session_id` is a no-op
    - Concurrent `init_session()` calls produce distinct, independent entries
    - `get_steps()` returns records in insertion order
    - _Requirements: 9.1, 9.5_

- [x] 7. Implement audit emitter and metrics
  - Implement `audit.py` with `emit_audit_event()` that writes a single JSON line to stdout using `json.dumps(..., default=str)` and `print(..., flush=True)`; never raises
  - Implement `metrics.py` defining `sessions_total`, `tool_calls_total`, `session_latency`, and `errors_total` prometheus_client objects
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 13.2, 13.3, 13.4, 13.5_

  - [x] 7.1 Write property test for audit event invariant fields (Property 9)
    - **Property 9: Every audit event has mandatory invariant fields**
    - **Validates: Requirements 11.4, 11.5**
    - Use `@given(st.sampled_from(["agent_session_start", "agent_tool_call", "agent_session_complete"]))` and `@given(st.dictionaries(...))` for variable fields
    - Capture stdout, parse JSON line, verify `audit_id` matches UUID v4 regex, `layer == "agent"`, `outcome` is one of `"pass"`, `"block"`, `"error"`, `"max_steps_reached"`
    - Verify the JSON line contains no embedded newlines

- [x] 8. Implement the agent orchestrator
  - Implement `agent/orchestrator.py` with `run_agent_session()` as the main entry point
  - Initialize session via `session_store.init_session()` and emit `agent_session_start` audit event
  - Build `ChatOpenAI` client with `base_url=f"{settings.router_url}/v1"`, `api_key`, `model`, and `timeout`
  - Build LangGraph agent via `create_react_agent(llm, tools=list(tool_registry.values()))`
  - Stream events with `graph.astream_events(..., config={"recursion_limit": max_agent_steps * 2 + 1}, version="v2")`
  - Increment `step_count` on `on_chat_model_end`, append to `tools_called` on `on_tool_start`, append step record to session store on `on_tool_end`
  - Emit `agent_tool_call` audit event on tool start; emit `agent_session_complete` audit event on completion
  - Handle Router errors via `_classify_router_error()` → return HTTP 502 with partial metadata
  - Build output IMF via `_build_output_imf()` preserving all non-metadata/non-response fields
  - Set `finish_reason="length"` and `max_steps_reached=True` when step budget exhausted
  - Set `finish_reason="stop"` on natural completion
  - Update Prometheus counters (`sessions_total`, `tool_calls_total`, `session_latency`, `errors_total`)
  - Emit session completion structured log at INFO level
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 9.2, 9.3, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 12.1_

  - [ ]* 8.1 Write property test for step count budget invariant (Property 1)
    - **Property 1: Step count never exceeds MAX_AGENT_STEPS**
    - **Validates: Requirements 3.5, 3.6**
    - Use `@given(st.integers(min_value=1, max_value=20))` for `MAX_AGENT_STEPS`
    - Mock the LangGraph graph to always emit `on_chat_model_end` events with a tool call (never a final answer)
    - Verify `metadata.agent_steps_taken <= MAX_AGENT_STEPS` for all generated values
    - Verify `response.finish_reason == "length"` and `metadata.max_steps_reached == True` when budget is exhausted

  - [ ]* 8.2 Write property test for session_id UUID v4 invariant (Property 3)
    - **Property 3: session_id is always a valid UUID v4**
    - **Validates: Requirements 2.1, 2.3, 2.5**
    - Use `@given(st.fixed_dictionaries({...}))` to generate arbitrary valid IMF inputs
    - Mock the orchestrator's LLM response to return a direct answer immediately
    - Verify each `metadata.agent_session_id` matches the UUID v4 regex `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`

  - [ ]* 8.3 Write property test for response.content non-empty on HTTP 200 (Property 4)
    - **Property 4: response.content always non-empty on HTTP 200**
    - **Validates: Requirements 1.2, 10.5**
    - Use `@given(st.text(min_size=1))` for the mocked LLM final answer content
    - Verify `response.content` is a non-null, non-empty string whenever the endpoint returns HTTP 200

  - [ ]* 8.4 Write property test for IMF field preservation (Property 8)
    - **Property 8: IMF field preservation — only metadata and response blocks are modified**
    - **Validates: Requirements 10.3, 10.4**
    - Use `@given(from_type(IMFDocument))` or a custom Hypothesis strategy to generate valid `IMFDocument` instances with arbitrary field values
    - Post to `/agent/run` with a mocked orchestrator that returns immediately
    - Verify `request_id`, `trace_id`, `span_id`, `timestamp_utc`, all `user.*`, `request.*`, `governance.*`, `routing.*`, `cache.*`, `extensions.*` are unchanged in the response body

  - [ ]* 8.5 Write property test for Router error → 502 with partial metadata (Property 10)
    - **Property 10: Router sub-call failure returns 502 with non-empty partial metadata**
    - **Validates: Requirements 4.3, 10.6, 10.7**
    - Use `@given(st.integers(min_value=400, max_value=599))` for non-200 Router status codes plus sampled timeout/connection error types
    - Mock `ChatOpenAI` to raise `httpx.HTTPStatusError`, `httpx.TimeoutException`, `httpx.ConnectError`
    - Verify HTTP 502, `response.content` is non-empty, `metadata.agent_session_id` is present, `metadata.agent_steps_taken` is an integer, `metadata.tools_called` is a list

- [x] 9. Implement FastAPI app, routers, and lifespan
  - Implement `routers/health.py` with `GET /health` returning `{"status": "ok"}` without authentication
  - Implement `routers/agent.py` with `POST /agent/run` that validates `extensions.agentic`, delegates to `run_agent_session()`, handles HTTP 500 via a global exception handler, and increments error metrics
  - Implement `main.py` with `create_app()`, `lifespan()` handler (validates env vars, validates `max_agent_steps` range, loads tool registry via `load_tool_registry()`, stores state on `app.state`), and separate `metrics_app` ASGI app using `prometheus_client.make_asgi_app()`
  - Wire `start.sh` (or Dockerfile `CMD`) to start both uvicorn instances (port 8083 for main app, port 9090 for metrics app)
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 5.3, 13.1_

  - [ ]* 9.1 Write property test for invalid IMF → HTTP 400 invariant (Property 11)
    - **Property 11: Invalid IMF inputs always return HTTP 400**
    - **Validates: Requirements 1.3, 10.1, 10.8**
    - Use `@given(st.booleans(), st.booleans())` to generate IMF variants: empty `messages`, missing `messages`, `extensions.agentic=False`, `extensions.agentic` absent
    - Verify HTTP 400 is returned for all invalid variants
    - Verify no agent session is created (session store remains empty)

  - [x] 9.2 Write unit tests for startup validation
    - Missing `ROUTER_URL` env var → `sys.exit(1)` before HTTP listener starts
    - Missing `GATEWAY_API_KEY` → `sys.exit(1)`
    - `MAX_AGENT_STEPS=0` → `sys.exit(1)`
    - `MAX_AGENT_STEPS=51` → `sys.exit(1)`
    - Missing `TOOL_CATALOG_PATH` file → `sys.exit(1)`
    - Malformed YAML at `TOOL_CATALOG_PATH` → `sys.exit(1)`
    - _Requirements: 5.3_

  - [x] 9.3 Write smoke tests for endpoint availability
    - `GET /health` returns 200 with `{"status": "ok"}`
    - `GET /metrics` on port 9090 returns 200 with `Content-Type: text/plain`
    - Request to an undefined path returns 404
    - _Requirements: 1.5, 1.6, 1.7, 13.1_

- [x] 10. Checkpoint — Ensure all unit and smoke tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement integration tests for full agent pipeline
  - [x] 11.1 Write integration tests for successful session flows
    - Mock Router returns a direct answer (no tool call): verify HTTP 200, `response.content` non-empty, `response.finish_reason == "stop"`, all three metadata fields present, `agent_steps_taken == 1`
    - Mock Router returns one tool call then a direct answer: verify `metadata.tools_called` list is correct, `metadata.agent_steps_taken == 2`
    - _Requirements: 1.2, 2.3, 9.3, 10.2_

  - [x] 11.2 Write integration tests for max steps and Router error paths
    - Mock Router always returns a tool call: verify `finish_reason == "length"`, `max_steps_reached == True`, HTTP 200
    - Mock Router returns non-200 mid-session: verify HTTP 502, `response.content` non-empty, `metadata.agent_session_id` present
    - Mock Router raises timeout: verify HTTP 502
    - Mock Router raises connection refused: verify HTTP 502
    - _Requirements: 3.5, 4.3, 4.4, 10.6, 10.7_

  - [x] 11.3 Write integration tests for Prometheus metrics
    - `llm_agent_framework_sessions_total{outcome="pass"}` increments by 1 on successful session
    - `llm_agent_framework_tool_calls_total{tool_name="calculator"}` increments per calculator invocation
    - `llm_agent_framework_errors_total{error_code="400"}` increments on invalid IMF request
    - `llm_agent_framework_errors_total{error_code="502"}` increments on Router error
    - _Requirements: 13.2, 13.3, 13.5_

- [x] 12. Implement Helm chart
  - Create `llm-platform/charts/agent-framework/Chart.yaml` with `name: agent-framework`, `version: 0.1.0`, `appVersion: "0.1.0"`
  - Create `llm-platform/charts/agent-framework/values.yaml` with all required fields: `replicaCount: 1`, image repository/tag/pullPolicy, service `ClusterIP` on port 8083, env vars (`ROUTER_URL`, `LOG_LEVEL`, `MAX_AGENT_STEPS`, `TOOL_CATALOG_PATH`, `GATEWAY_API_KEY: "poc-secret-key"`), resource requests/limits (`cpu: 200m`/`memory: 512Mi` requests; `cpu: 1`/`memory: 1Gi` limits), `autoscaling.enabled: false`, `vault.enabled: false`, observability metrics port 9090
  - Create `templates/deployment.yaml`, `templates/service.yaml`, `templates/networkpolicy.yaml` (ingress from `app: router` only; egress to `app: router` on 8082 and DNS port 53), `templates/servicemonitor.yaml`, `templates/hpa.yaml` (disabled), `templates/configmap.yaml` (mounts `catalog.yaml` at `/config/tools/`), `templates/_helpers.tpl`
  - Create `README.md` documenting that image tag and `GATEWAY_API_KEY` must be overridden at deploy time
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10_

- [x] 13. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP delivery
- Each task references specific requirements for traceability
- The design uses Python with Hypothesis for property-based testing
- Property tests require `@settings(max_examples=100)` per the design's testing strategy
- Each property test must include a comment citing the property number: e.g. `# Feature: agent-framework, Property 1: Step count never exceeds MAX_AGENT_STEPS`
- Tool result injection (Property 2) is verified at the LangGraph framework level via `on_tool_end` event streaming — LangGraph guarantees this invariant internally; the integration test validates it end-to-end
- The `metrics_app` is a separate ASGI app on port 9090 sharing the same process as the main app on 8083
- POC constraints in effect: plain HTTP, static API key, in-memory session store, mocked `web_search`, `autoscaling.enabled: false`, `vault.enabled: false`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "3.5", "3.7"] },
    { "id": 2, "tasks": ["3.2", "3.3", "3.4", "3.6", "3.8", "3.9", "5.1", "6.1"] },
    { "id": 3, "tasks": ["7.1"] },
    { "id": 4, "tasks": ["8.1", "8.2", "8.3", "8.4", "8.5", "9.1", "9.2", "9.3"] },
    { "id": 5, "tasks": ["11.1", "11.2", "11.3"] }
  ]
}
```
