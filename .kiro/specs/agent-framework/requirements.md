# Requirements Document

## Introduction

This document defines the requirements for Layer 6 — Agent Framework (POC) of the Enterprise On-Premises LLM Platform. The Agent Framework is a LangGraph-based ReAct agent service that handles agentic requests — those where the client sets `request.agentic: true` in the payload. The service receives an IMF from the Router, decomposes the goal into a multi-step plan, calls a small set of registered tools across up to 10 reasoning steps, and returns a final IMF with a synthesized answer in `response.content`.

Every LLM sub-call within the agent loop is routed through the Router → Security → Inference pipeline, ensuring that the platform's governance and audit controls are never bypassed. Short-term session state is maintained in an in-process Python dictionary keyed by `session_id` and is intentionally non-persistent for the POC.

This is a Proof-of-Concept implementation. Production concerns such as Temporal/Argo workflow orchestration, MCP server integration, long-term vector memory, Redis session storage, tool sandboxing, OPA permission checks, and multi-agent coordination are explicitly deferred to Phase 2.

---

## Glossary

- **Agent_Framework**: The FastAPI-based Layer 6 service defined in this document, running on port 8083 at `services/agent-framework/`.
- **Agent_Orchestrator**: The LangGraph ReAct agent loop component inside the Agent_Framework that drives the reasoning and tool-calling cycle.
- **ReAct_Loop**: The Reason-Act-Observe agent execution pattern implemented by LangGraph's `create_react_agent`. Each iteration consists of an LLM reasoning step followed by an optional tool invocation.
- **Tool_Registry**: The set of available tools loaded from `tools/catalog.yaml` at startup. For the POC, contains exactly three tools: `calculator`, `get_current_time`, and `web_search`.
- **Tool_Executor**: The component that dispatches a tool call request from the Agent_Orchestrator to the corresponding tool implementation and returns the result as a string.
- **Session_Store**: The in-process Python dictionary keyed by `session_id` that holds the step-by-step trace of each active agent session. State is lost on pod restart.
- **IMF**: Internal Message Format — the canonical JSON structure used for all inter-layer communication on this platform. Defined in `00-platform-master-contract.md`.
- **Sub_IMF**: An IMF object constructed by the Agent_Framework for each LLM call within the ReAct_Loop. It is forwarded to the Router via HTTP POST to `/v1/chat/completions`.
- **Router**: The Intelligent Router service at `http://router:8082`. The Agent_Framework's LangChain LLM client (`ChatOpenAI`) points to its `/v1/chat/completions` endpoint.
- **session_id**: A UUID v4 generated per agent invocation. Used as the key in the Session_Store and written to `metadata.agent_session_id` in the output IMF.
- **max_steps**: The maximum number of ReAct_Loop iterations permitted in a single agent session. Fixed at 10 for the POC via the `MAX_AGENT_STEPS` environment variable.
- **Audit_Event**: A structured JSON record written to stdout representing a significant lifecycle event within the Agent_Framework.
- **Tool_Catalog**: The YAML file at `tools/catalog.yaml` that declares the name, description, and parameter schema for each registered tool.
- **calculator**: A tool that evaluates a mathematical expression string using a safe AST-based evaluator that permits only arithmetic operators. It does not invoke Python builtins or arbitrary code.
- **get_current_time**: A tool that returns the current UTC timestamp in ISO-8601 format.
- **web_search**: A tool that returns hardcoded mock results for any query string. No real HTTP calls are made.
- **Helm_Chart**: The Kubernetes deployment packaging at `llm-platform/charts/agent-framework/`.
- **ROUTER_URL**: Environment variable holding the base URL of the Router service (default: `http://router:8082`).
- **GATEWAY_API_KEY**: Environment variable holding the API key forwarded as the `api_key` parameter of the LangChain `ChatOpenAI` client when making sub-calls to the Router.
- **MAX_AGENT_STEPS**: Environment variable controlling the maximum number of ReAct_Loop iterations (default: `10`).
- **TOOL_CATALOG_PATH**: Environment variable pointing to the tool catalog YAML file (default: `/config/tools/catalog.yaml`).

---

## Requirements

### Requirement 1: Agent Entry Point

**User Story:** As an enterprise application developer, I want to trigger an agent session by setting `agentic: true` in my request, so that complex multi-step tasks are handled automatically by the platform's agent layer without requiring me to manage the reasoning loop manually.

#### Acceptance Criteria

1. THE Agent_Framework SHALL expose a `POST /agent/run` HTTP endpoint that accepts a JSON body conforming to the IMF schema.
2. WHEN a POST request is received at `/agent/run` with a valid IMF where `request.messages` is a non-empty array and `extensions.agentic` is `true`, THE Agent_Framework SHALL initiate a new agent session and return HTTP 200 with the final IMF (with `response.content` populated) as the response body.
3. IF a POST request is received at `/agent/run` with a missing or empty `request.messages` array, or with `extensions.agentic` absent or set to `false`, THEN THE Agent_Framework SHALL return HTTP 400 with an error body indicating the missing or invalid field.
4. IF a POST request body cannot be parsed as valid JSON, THEN THE Agent_Framework SHALL return HTTP 400 with an error body indicating the parse failure.
5. THE Agent_Framework SHALL expose a `GET /health` endpoint that returns HTTP 200 with body `{"status": "ok"}` without requiring authentication.
6. THE Agent_Framework SHALL expose a `GET /metrics` endpoint returning Prometheus text exposition format metrics.
7. WHEN a request arrives at an undefined path, THE Agent_Framework SHALL return HTTP 404.
8. IF the agent session fails mid-execution due to an unhandled internal error, THEN THE Agent_Framework SHALL return HTTP 500 with the partial IMF and `response.finish_reason` set to `null`.

---

### Requirement 2: Session Initialization

**User Story:** As a platform engineer, I want each agent invocation to be assigned a unique session identifier, so that all steps, tool calls, and log records for that session can be correlated in the audit trail.

#### Acceptance Criteria

1. WHEN a new agent session is initiated, THE Agent_Orchestrator SHALL generate a UUID v4 value and assign it as the `session_id` for that session, and SHALL initialize an entry in the Session_Store keyed by that `session_id`.
2. WHEN a new agent session is initiated, THE Agent_Orchestrator SHALL initialize an entry in the Session_Store keyed by the `session_id`, containing an empty list of step records. IF a Session_Store entry already exists for the generated `session_id`, THE Agent_Orchestrator SHALL replace it with a new empty entry.
3. THE Agent_Orchestrator SHALL write the `session_id` to `metadata.agent_session_id` in the output IMF before returning the response.
4. WHEN a new session begins, THE Agent_Orchestrator SHALL emit an `agent_session_start` Audit_Event to stdout containing at minimum: `audit_id` (UUID v4, unique per event), `request_id`, `session_id` (the UUID v4 identifying this agent session lifetime), `timestamp_utc` (ISO-8601 UTC), `user_id`, `layer: "agent"`, `event_type: "agent_session_start"`, and `outcome: "pass"`.
5. WHEN two or more concurrent requests arrive, THE Agent_Orchestrator SHALL assign a distinct `session_id` to each concurrent session such that no two sessions active at the same time share the same `session_id`.
6. IF the Session_Store is unavailable or corrupted at session initialization time, THE Agent_Orchestrator SHALL return HTTP 500 with an error body indicating the session store failure and SHALL NOT proceed with the agent loop.

---

### Requirement 3: ReAct Loop Execution

**User Story:** As a platform engineer, I want the agent to reason and act across multiple steps until it reaches a final answer or exhausts its step budget, so that complex tasks requiring tool use can be completed automatically.

#### Acceptance Criteria

1. THE Agent_Orchestrator SHALL execute the ReAct_Loop using LangGraph's `create_react_agent`, with the Tool_Registry tools bound to the LLM client.
2. WHEN the LLM produces a response with no tool call, THE Agent_Orchestrator SHALL treat that response as a final answer, exit the ReAct_Loop, and proceed to session completion.
3. WHEN the LLM produces a tool call in its response, THE Agent_Orchestrator SHALL invoke the Tool_Executor with the specified tool name and parameters, inject the tool result back into the conversation context as a message with role `"tool"`, and continue to the next loop iteration.
4. THE Agent_Orchestrator SHALL count each LLM call as one step, starting from step 1, incrementing by 1 for each subsequent LLM call regardless of whether the step involves a tool call.
5. WHEN the step count reaches the value of `MAX_AGENT_STEPS` (default: 10) without the LLM producing a final answer, THE Agent_Orchestrator SHALL exit the ReAct_Loop, set `response.content` to the most recent non-empty LLM output available (or a descriptive fallback message if no LLM output is available), set `response.finish_reason` to `"length"`, and include `max_steps_reached: true` in the output IMF `metadata`.
6. FOR ALL valid agent sessions, THE total number of LLM calls made SHALL NOT exceed the value of `MAX_AGENT_STEPS`.
7. AFTER each tool call result is returned, THE Agent_Orchestrator SHALL inject the result into the conversation context as a message with role `"tool"` before making the next LLM call, such that the LLM's next reasoning step has access to the tool output.

---

### Requirement 4: LLM Sub-Call Routing

**User Story:** As a platform operator, I want every LLM call within the agent loop to pass through the Router → Security → Inference pipeline, so that governance, PII masking, and audit controls apply to all agent-generated prompts — not just the initial user request.

#### Acceptance Criteria

1. THE Agent_Framework SHALL configure its LangChain `ChatOpenAI` LLM client with `base_url` set to `{ROUTER_URL}/v1`, `api_key` set to the value of `GATEWAY_API_KEY`, and `model` set to `llama3.2:3b`.
2. WHEN the Agent_Orchestrator makes an LLM call during the ReAct_Loop, THE LLM client SHALL send an HTTP POST request to `{ROUTER_URL}/v1/chat/completions` with the messages array from the current conversation context as the request body.
3. IF the Router returns a non-200 HTTP status code for a sub-call, THEN THE Agent_Orchestrator SHALL abort the current session, set `response.content` to an error message that includes the Router's HTTP status code and any error detail from the Router's response body, and return HTTP 502 to the caller.
4. IF a network or connection error occurs during a Router sub-call (including timeout after 30 seconds, or connection refused), THEN THE Agent_Orchestrator SHALL abort the current session, set `response.content` to an error message indicating the connection failure, and return HTTP 502 to the caller.
5. THE Agent_Framework SHALL NOT call the Inference Adapter, Ollama, or any inference backend directly. All LLM calls SHALL go through the Router endpoint.
6. THE Agent_Framework SHALL enforce a 30-second per-sub-call timeout on HTTP requests to the Router, after which the call is treated as a connection error per criterion 4.

---

### Requirement 5: Tool Registry and Loading

**User Story:** As a platform engineer, I want tools to be declared in a YAML catalog and loaded at startup, so that the tool set can be inspected and extended without modifying the agent orchestration code.

#### Acceptance Criteria

1. THE Agent_Framework SHALL load the Tool_Catalog from the file path specified by `TOOL_CATALOG_PATH` at service startup.
2. THE Tool_Catalog SHALL declare the `calculator`, `get_current_time`, and `web_search` tools, each with a `name` (unique string), `description` (non-empty string), and `parameters` block (non-empty, with at least one parameter definition per tool that requires parameters).
3. WHEN the Tool_Catalog file is missing, unparseable YAML, or structurally valid YAML with missing or empty required fields (`name`, `description`), THE Agent_Framework SHALL fail to start and log an error message to stdout indicating the path and failure reason.
4. THE Agent_Orchestrator SHALL bind all tools loaded from the Tool_Catalog to the LangGraph `create_react_agent` instance at session creation time. IF any tool fails to bind, the session creation SHALL be rejected with an error response identifying the unbound tool name.
5. WHEN the Agent_Orchestrator calls a tool by name, THE Tool_Executor SHALL locate the corresponding implementation by matching the `name` field in the Tool_Catalog and invoke it with the provided parameters.
6. IF the Agent_Orchestrator requests a tool that is not present in the Tool_Catalog, THEN THE Tool_Executor SHALL return an error string indicating the tool name was not found, and continue the ReAct_Loop without aborting the session.
7. IF a tool implementation raises an unhandled exception during execution, THEN THE Tool_Executor SHALL return an error string containing the tool name and the failure reason, and continue the ReAct_Loop without aborting the session.

---

### Requirement 6: Calculator Tool

**User Story:** As an end user, I want the agent to evaluate mathematical expressions accurately and safely, so that I can ask arithmetic questions without the agent hallucinating results.

#### Acceptance Criteria

1. THE calculator tool SHALL accept a single string parameter named `expression` and return the evaluated result as a string.
2. THE calculator tool SHALL evaluate `expression` using a Python AST-based parser. The only permitted operation categories are: arithmetic binary operators (addition, subtraction, multiplication, division, exponentiation, modulo) and arithmetic unary operators (negation, unary plus). Numeric literals and parenthesized sub-expressions are permitted. No other constructs are permitted.
3. THE calculator tool SHALL NOT execute any Python builtin functions, import statements, attribute access, subscript access, or function/method calls during expression evaluation.
4. IF an `expression` string contains any construct outside the permitted set, THEN THE calculator tool SHALL return an error string indicating the expression is unsafe, without evaluating the expression.
5. IF an `expression` string is syntactically invalid (cannot be parsed as a valid arithmetic expression), THEN THE calculator tool SHALL return an error string indicating the expression is invalid.
6. IF evaluation of a permitted expression raises a runtime error (including but not limited to division by zero or numeric overflow), THEN THE calculator tool SHALL return an error string identifying the type of error that occurred.
7. FOR ALL expression strings containing only permitted constructs that evaluate to an integer result, THE calculator tool SHALL return a string representation with no decimal point (e.g., `"4"` not `"4.0"`).
8. FOR ALL expression strings containing only permitted constructs that evaluate to a floating-point result, THE calculator tool SHALL return a decimal string representation with no unnecessary trailing zeros (e.g., `"2.5"` not `"2.50"`).

---

### Requirement 7: get_current_time Tool

**User Story:** As an end user, I want the agent to accurately report the current date and time when asked, so that time-sensitive questions are answered with real data rather than a hallucinated timestamp.

#### Acceptance Criteria

1. THE get_current_time tool SHALL accept no input parameters.
2. WHEN invoked, THE get_current_time tool SHALL return the current UTC date and time as a string in the format `YYYY-MM-DDTHH:MM:SS+00:00`, accurate to within 1 second of the actual UTC wall-clock time at the moment of invocation.
3. THE get_current_time tool SHALL always return a non-empty string.
4. THE string returned by THE get_current_time tool SHALL include an explicit UTC timezone offset (`+00:00`) and SHALL be parseable as a valid ISO-8601 datetime with timezone information.
5. IF the system clock is unavailable at the time of invocation, THE get_current_time tool SHALL return an error string indicating the time could not be retrieved.

---

### Requirement 8: web_search Tool (Mocked)

**User Story:** As an end user, I want the agent to simulate a web search so that the platform demonstrates end-to-end tool-calling capability during the POC, without requiring a live search API integration.

#### Acceptance Criteria

1. THE web_search tool SHALL accept a single string parameter named `query` with a maximum length of 1000 characters.
2. WHEN invoked with any non-empty, non-whitespace-only `query` string, THE web_search tool SHALL return a mock result string containing at least one non-whitespace character that includes the original `query` value as a substring, formatted as: `"[POC Mock] Search results for '<query>': This is a simulated result. In production this would query an enterprise search system."`.
3. THE web_search tool SHALL NOT make any outbound HTTP requests.
4. IF `query` is an empty string, a whitespace-only string, or absent (null), THEN THE web_search tool SHALL return an error string indicating the query must be a non-empty string.
5. FOR ALL non-empty, non-whitespace-only `query` strings, the string returned by THE web_search tool SHALL contain the `query` value as a substring and SHALL contain at least one non-whitespace character.

---

### Requirement 9: Session State Management

**User Story:** As a platform operator, I want the agent to maintain a per-session step trace in memory during execution, so that each step's tool call and result are available for logging and debugging within the session lifetime.

#### Acceptance Criteria

1. THE Session_Store SHALL be an in-process Python dictionary keyed by `session_id`, initialized to an empty dict at service startup. IF a new session begins with a `session_id` that already exists in the Session_Store, THE Agent_Orchestrator SHALL replace the existing entry with a new empty step list.
2. WHEN a ReAct_Loop step that includes a tool call completes, THE Agent_Orchestrator SHALL append a record to the session's step list in the Session_Store containing: `step` (integer, 1-indexed, representing the ReAct loop iteration number), `tool_called` (string tool name), and `result_summary` (the first 200 characters of the tool result string).
3. WHEN a session completes (either by reaching a final answer or exhausting `MAX_AGENT_STEPS`), THE Agent_Orchestrator SHALL write to the output IMF: `metadata.agent_steps_taken` as the total count of ReAct loop iterations executed (including steps with no tool call), and `metadata.tools_called` as an ordered list of tool name strings preserving call order (with duplicates if the same tool was called multiple times).
4. THE Agent_Framework SHALL NOT persist session state to any external store (Redis, database, file system). All session data is in-memory and SHALL be lost on pod restart — this is acceptable for the POC.
5. WHEN the session completes, THE Agent_Orchestrator SHALL retain the session entry in the Session_Store for the duration of the process lifetime but SHALL NOT append further records to the session entry after completion.

---

### Requirement 10: IMF Input and Output Contract

**User Story:** As a platform engineer, I want the Agent_Framework to read and write only the IMF fields specified in the layer contract, so that upstream and downstream layers can process the envelope without unexpected field mutations.

#### Acceptance Criteria

1. THE Agent_Framework SHALL read the following IMF fields from the inbound request: `request.messages`, `extensions.agentic`, `user.user_id`, `user.department`, `user.roles`, `user.auth_method`, `request_id`, and `trace_id`. IF any of these required fields is absent or malformed, THE Agent_Framework SHALL return HTTP 400 with an error body identifying the missing or invalid field.
2. THE Agent_Framework SHALL write the following IMF fields in the outbound response: `metadata.agent_session_id` (UUID v4), `metadata.agent_steps_taken` (integer), `metadata.tools_called` (ordered list of strings), `response.content` (final synthesized answer), and `response.finish_reason` (`"stop"` on success, `"length"` when `MAX_AGENT_STEPS` is reached).
3. THE Agent_Framework SHALL NOT modify any IMF fields other than the `metadata` and `response` blocks.
4. THE Agent_Framework SHALL preserve all other inbound IMF fields in the outbound response without alteration.
5. WHEN a session completes successfully, `response.content` in the output IMF SHALL be a non-empty string.
6. IF a session is aborted due to a Router sub-call error (non-2xx HTTP response or connection failure), THEN `response.content` SHALL be a non-empty error message indicating the cause of the failure, and the HTTP response status SHALL be 502.
7. WHEN a session is aborted due to a Router sub-call error, the output IMF SHALL still include `metadata.agent_session_id`, `metadata.agent_steps_taken` (reflecting the steps completed before the abort), and `metadata.tools_called` (reflecting tools called before the abort).
8. IF a required inbound IMF field is absent or malformed, THE Agent_Framework SHALL return HTTP 400 before initiating any agent session, with an error body identifying the specific field that is missing or invalid.

---

### Requirement 11: Audit Event Emission

**User Story:** As a compliance engineer, I want agent lifecycle events written as structured JSON to stdout, so that the audit trail captures the agent's actions alongside those of other platform layers.

#### Acceptance Criteria

1. WHEN a session begins, THE Agent_Framework SHALL emit an `agent_session_start` Audit_Event to stdout containing: `audit_id` (UUID v4, unique per event), `request_id`, `session_id` (the UUID v4 identifying this agent session lifetime), `timestamp_utc` (ISO-8601 UTC), `user_id`, `layer: "agent"`, `event_type: "agent_session_start"`, and `outcome: "pass"`.
2. WHEN a tool is invoked during the ReAct_Loop, THE Agent_Framework SHALL emit an `agent_tool_call` Audit_Event to stdout containing: `audit_id` (UUID v4), `request_id`, `session_id`, `timestamp_utc`, `layer: "agent"`, `event_type: "agent_tool_call"`, `tool_name` (the name of the tool called), `tool_input` (the parameters passed, serialized as a JSON string, truncated to 4096 characters if longer), and `outcome: "pass"`.
3. WHEN a session completes (successfully or at max steps), THE Agent_Framework SHALL emit an `agent_session_complete` Audit_Event to stdout containing: `audit_id` (UUID v4), `request_id`, `session_id`, `timestamp_utc`, `layer: "agent"`, `event_type: "agent_session_complete"`, `steps_taken` (integer), `tools_called` (list of strings), `total_latency_ms` (integer, from session start to completion), and `outcome` (`"pass"` or `"max_steps_reached"`).
4. THE Agent_Framework SHALL write all Audit_Events to stdout as a single JSON line per event, with no multi-line formatting.
5. EVERY Audit_Event emitted by THE Agent_Framework SHALL include an `audit_id` (UUID v4, unique per event), a `layer` field set to `"agent"`, and an `outcome` field set to one of `"pass"`, `"block"`, `"error"`, or `"max_steps_reached"`. The `outcome: "block"` value SHALL be used when a tool call is refused by governance before execution.
6. IF a tool invocation fails (raises an exception or returns an error), THE Agent_Framework SHALL emit an `agent_tool_call` Audit_Event with `outcome: "error"` containing the same fields as criterion 2 plus an `error_detail` field with the error description.

---

### Requirement 12: Structured Logging and Per-Step Observability

**User Story:** As a platform operator, I want structured JSON logs for each session and each step, so that I can diagnose agent behavior and trace tool call sequences in the log aggregation system.

#### Acceptance Criteria

1. WHEN a session completes, THE Agent_Framework SHALL emit one structured JSON log record to stdout at INFO level containing: `session_id`, `request_id`, `steps_taken`, `tools_called` (list), `outcome` (one of `"success"`, `"error"`, `"timeout"`, `"max_steps_exceeded"`), `total_latency_ms`, `timestamp_utc` (ISO-8601), and `level: "INFO"`.
2. WHEN each ReAct_Loop step executes, THE Agent_Framework SHALL emit one structured JSON log record to stdout at DEBUG level containing: `session_id`, `step_number`, `tool_name` (or `null` if the step was a final answer), `tool_result_summary` (first 200 characters of the tool result, or `null`), `timestamp_utc` (ISO-8601), and `level: "DEBUG"`.
3. THE Agent_Framework SHALL respect the `LOG_LEVEL` environment variable, emitting records at or above the configured level. Level ordering is `DEBUG < INFO < WARNING < ERROR`. Default: `INFO`. Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
4. IF `LOG_LEVEL` is set to an unrecognized value, THE Agent_Framework SHALL default to `INFO` and emit one `WARNING`-level log record to stdout indicating the unrecognized value and the fallback level.
5. WHEN an unhandled exception occurs during request processing, THE Agent_Framework SHALL log a JSON record with `level: "ERROR"` to stdout containing: `session_id`, `request_id`, `exception_type`, `exception_message`, `traceback`, `timestamp_utc` (ISO-8601), and `latency_ms` measured from the start of request processing to the point of failure.

---

### Requirement 13: Prometheus Metrics Exposure

**User Story:** As a platform operator, I want basic Prometheus metrics exposed at `/metrics`, so that agent session throughput, tool usage, and error rates can be monitored during the POC.

#### Acceptance Criteria

1. THE Agent_Framework SHALL expose a `GET /metrics` endpoint that returns HTTP 200 with `Content-Type: text/plain; version=0.0.4; charset=utf-8` and a body in Prometheus text exposition format.
2. THE Agent_Framework SHALL maintain a counter `llm_agent_framework_sessions_total` labeled by `outcome`. Valid label values are: `"pass"` (session completed with a final answer within step budget), `"max_steps_reached"` (session terminated at step limit), `"error"` (session aborted due to an internal error or Router failure). This counter SHALL be incremented exactly once per completed or aborted session using the label value corresponding to the session's outcome.
3. THE Agent_Framework SHALL maintain a counter `llm_agent_framework_tool_calls_total` labeled by `tool_name`, incremented once per tool invocation at the moment the Tool_Executor is called, regardless of whether the invocation succeeds or fails. Each retry of a failed tool call SHALL be counted as a separate increment.
4. THE Agent_Framework SHALL maintain a histogram `llm_agent_framework_session_latency_seconds` recording end-to-end session duration from receipt of the `/agent/run` request to final response delivery, using the default `prometheus_client` histogram buckets for the POC.
5. THE Agent_Framework SHALL maintain a counter `llm_agent_framework_errors_total` labeled by `error_code` (the numeric HTTP status code as a string, e.g., `"400"`, `"502"`, `"500"`), incremented on every 4xx and 5xx response returned by the `/agent/run` endpoint. Responses from the `/metrics` endpoint SHALL NOT be counted.

---

### Requirement 14: Helm Chart Deployment

**User Story:** As a platform operator, I want the Agent_Framework packaged as a Helm chart following platform conventions, so that it can be deployed and configured consistently with all other platform layers.

#### Acceptance Criteria

1. THE Helm_Chart SHALL be located at `llm-platform/charts/agent-framework/` and SHALL include `Chart.yaml`, `values.yaml`, `README.md`, and a `templates/` directory containing `deployment.yaml`, `service.yaml`, `networkpolicy.yaml`, `servicemonitor.yaml`, `hpa.yaml`, and `_helpers.tpl`.
2. THE Helm_Chart SHALL default `replicaCount` to `1` for the POC.
3. THE Helm_Chart SHALL configure the Kubernetes Service as type `ClusterIP` on port `8083`.
4. THE Helm_Chart SHALL accept the following configurable environment variables injected into the pod via the values file, with these defaults: `ROUTER_URL` (default: `http://router:8082`), `LOG_LEVEL` (default: `INFO`), `MAX_AGENT_STEPS` (default: `10`), `TOOL_CATALOG_PATH` (default: `/config/tools/catalog.yaml`). `GATEWAY_API_KEY` SHALL have no default value and must be explicitly supplied at deploy time.
5. THE Helm_Chart SHALL set pod resource requests to `cpu: 200m` and `memory: 512Mi`, and limits to `cpu: 1` and `memory: 1Gi`.
6. THE Helm_Chart SHALL set `autoscaling.enabled` to `false` for the POC.
7. THE Helm_Chart SHALL set `vault.enabled` to `false` for the POC; secrets are supplied via environment variables.
8. THE Helm_Chart SHALL configure the container image repository as `registry.local/agent-framework` with an empty default tag. The chart's `README.md` SHALL document that the image tag must be explicitly overridden at deploy time.
9. IF `GATEWAY_API_KEY` is not overridden at deploy time, THE Helm_Chart SHALL use the placeholder value `poc-secret-key`. The chart's `README.md` SHALL document that this value must be replaced before any deployment outside a local development environment.
10. THE Helm_Chart SHALL configure a NetworkPolicy that restricts ingress to traffic from pods with label `app: router` only, and restricts egress to: pods with label `app: router` on TCP port 8082, pods with label `app: audit-store` on TCP port 9200, and DNS resolution on both UDP and TCP port 53.

---

### Requirement 15: Correctness Properties

**User Story:** As a platform engineer, I want the Agent_Framework's core logic to be verified against property-based tests, so that correctness guarantees hold across the full space of valid inputs and tool call combinations.

#### Acceptance Criteria

1. FOR ALL agent session executions with any combination of tool-calling LLM responses, THE Agent_Orchestrator SHALL NOT make more than `MAX_AGENT_STEPS` (default: 10) LLM sub-calls within a single session. (Property: step count is invariant-bounded regardless of LLM response content.)
2. FOR ALL agent session executions where at least one tool call is made, THE Agent_Orchestrator SHALL inject the tool result into the conversation context as a message with role `"tool"` before making the subsequent LLM call, such that the message list passed to the next LLM call contains the tool result. (Property: tool results are always present in context before the next reasoning step.)
3. FOR ALL successfully completed agent sessions (those that return HTTP 200), `response.content` in the output IMF SHALL be a non-empty string. (Property: successful sessions never return an empty response.)
4. FOR ALL expression strings submitted to the calculator tool, the calculator tool SHALL NOT invoke `eval()`, `exec()`, `compile()`, `__import__()`, or any Python builtin function. The calculator SHALL only evaluate expressions using the AST node whitelist defined in Requirement 6.2. (Property: calculator never executes arbitrary code.)
5. FOR ALL agent sessions initiated via `POST /agent/run`, `metadata.agent_session_id` in the output IMF SHALL be a valid UUID v4 string matching the regex `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`. (Property: session_id is always a valid UUID v4.)
6. FOR ALL non-empty expression strings containing only permitted AST node types, THE calculator tool SHALL return the same string result for the same expression on repeated invocations (no side effects). Equality of floating-point results is determined by their string representations. (Property: calculator is a pure function — idempotent for identical inputs.)
7. FOR ALL non-empty, non-whitespace-only `query` strings, the string returned by THE web_search tool SHALL contain the original `query` value as a substring. (Property: web_search result always echoes back the query.)
