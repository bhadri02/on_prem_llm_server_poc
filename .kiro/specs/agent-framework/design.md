# Design Document — Agent Framework (Layer 6 — POC)

## Overview

The Agent Framework (Layer 6) is a standalone FastAPI microservice that handles agentic requests from the Intelligent Router. It runs on port 8083, with a separate Prometheus metrics app on port 9090. When the Router receives an IMF with `extensions.agentic: true`, it forwards that IMF to `POST /agent/run` on this service.

The service implements a LangGraph ReAct (Reason-Act-Observe) agent loop. On each invocation it creates a new agent session, binds three registered tools, and executes the loop for up to `MAX_AGENT_STEPS` iterations (default: 10). Every LLM sub-call within the loop is routed through the Intelligent Router's `/v1/chat/completions` endpoint using a LangChain `ChatOpenAI` client — the governance, security, and audit pipeline is never bypassed. Short-term session state lives in an in-process Python dictionary and is intentionally lost on pod restart, which is acceptable for the POC.

The service returns the original IMF with `response.content`, `response.finish_reason`, and three agent-specific metadata fields populated: `metadata.agent_session_id`, `metadata.agent_steps_taken`, and `metadata.tools_called`.

**Ports:** API on 8083, Prometheus metrics on 9090.

**POC constraints in effect:** Plain HTTP between services, static API key forwarded to Router, in-memory session store (no Redis), mocked `web_search` (no real HTTP), JSON-to-stdout structured logging, `autoscaling.enabled: false`, `vault.enabled: false`.

---

## Architecture

The service is a single-process FastAPI application following the same structural pattern used by the Security & Governance Layer and the Intelligent Router: a lifespan handler performs all startup validation, two ASGI apps share the same process (main app on 8083, metrics app on 9090), all significant state is loaded once at startup and stored on `app.state`, and all downstream HTTP calls use a shared `httpx.AsyncClient` managed by the LangChain client.


```mermaid
graph TD
    subgraph Callers
        RTR[Intelligent Router\nport 8082]
    end

    subgraph Agent Framework — port 8083
        EP[POST /agent/run\nIMF endpoint]
        HLT[GET /health]

        subgraph Session Lifecycle
            INIT[1. Validate IMF\nextensions.agentic=true]
            SES[2. Create Session\nUUID v4 session_id\nSession_Store entry]
            AGENT[3. Init LangGraph Agent\ncreate_react_agent + tool bindings]
            LOOP[4. ReAct Loop\nmax MAX_AGENT_STEPS iterations]
            COMP[5. Session Completion\nwrite metadata fields]
        end

        subgraph ReAct Loop Detail
            LLM_CALL[LLM Sub-call\nChatOpenAI → Router /v1/chat/completions]
            TOOL_DISPATCH[Tool Dispatch\nTool_Executor]
            INJECT[Inject result\nrole=tool message]
        end

        subgraph Tools
            CALC[calculator.py\nAST-based safe eval]
            TIME[get_time.py\ndatetime.now UTC]
            SEARCH[web_search.py\nmocked results]
        end

        TOOL_REG[tools/registry.py\nloads catalog.yaml]
        SS[session_store.py\nPython dict + asyncio.Lock]
        AUDIT[audit.py\nstdout JSON events]
        CFG[config.py\npydantic-settings]
        LOG[logging_config.py\nJSON stdout]
    end

    subgraph Downstream
        ROUTER_LLM[Router /v1/chat/completions\nport 8082]
    end

    subgraph Observability
        PROM[Prometheus Scraper]
        MTR[metrics.py ASGI app\nport 9090]
    end

    RTR -->|POST /agent/run IMF| EP
    EP --> INIT
    INIT --> SES
    SES --> AGENT
    AGENT --> LOOP
    LOOP --> LLM_CALL
    LLM_CALL -->|tool_call in response| TOOL_DISPATCH
    LLM_CALL -->|final answer| COMP
    TOOL_DISPATCH --> CALC
    TOOL_DISPATCH --> TIME
    TOOL_DISPATCH --> SEARCH
    CALC & TIME & SEARCH --> INJECT
    INJECT --> LOOP
    LOOP -->|step count = MAX_AGENT_STEPS| COMP
    COMP --> EP
    LLM_CALL -->|HTTP POST| ROUTER_LLM
    PROM -->|GET /metrics| MTR
```


### Key Design Decisions

**LangGraph `create_react_agent` as the agent loop driver.** Rather than implementing the ReAct loop manually, the service delegates to LangGraph's `create_react_agent` prebuilt. This function accepts the LLM client and a list of `@tool`-decorated functions, wires the tool-call parsing and result injection automatically, and exposes a `graph.invoke()` / `graph.astream()` interface. The loop step count is controlled by setting `recursion_limit` on the graph config.

**LangChain `ChatOpenAI` with `base_url` pointing to the Router.** The LLM client is configured once at startup with `base_url=f"{ROUTER_URL}/v1"`, `api_key=GATEWAY_API_KEY`, and `model="llama3.2:3b"`. All LLM sub-calls within the ReAct loop are transparently routed through the Router's OpenAI-compatible `/v1/chat/completions` endpoint. The agent code has no awareness of the inference backend topology.

**Tool registry loaded from YAML at startup; fail fast if missing.** `tools/catalog.yaml` is the single source of truth for tool declarations. `tools/registry.py` reads the YAML, validates required fields (`name`, `description`), and builds a `name → callable` mapping. If the file is missing or malformed, the lifespan handler calls `sys.exit(1)` before the HTTP listener starts. This is consistent with how the Router loads `model_matrix.yaml` and `task_classifier_rules.yaml`.

**Separate ASGI app for metrics on port 9090.** Identical to the Security & Governance Layer and Intelligent Router pattern. `metrics.py` defines all `prometheus_client` Counter and Histogram objects. A lightweight Starlette app created in `main.py` imports those objects and mounts `make_asgi_app()` at `/metrics`. Uvicorn starts both apps separately.

**In-memory session store with `asyncio.Lock` for concurrency safety.** The session dictionary is a plain Python dict wrapped with a single `asyncio.Lock`. Because FastAPI runs on a single asyncio event loop, this is safe for the POC. All read/write operations on the dict acquire the lock with `async with`. On pod restart the dict is lost — this is explicitly acceptable for the POC.

**Fire-and-forget audit events via `BackgroundTasks`.** Audit events (session start, tool call, session complete) are emitted by writing JSON to stdout. The `audit.py` module provides a `emit_audit_event()` function that formats the payload as a single JSON line. For events triggered mid-session (tool calls), the emission is synchronous in-process (stdout write is fast). The session-complete event is dispatched as a `BackgroundTask` so the response is not blocked.

**Step budget enforced via LangGraph `recursion_limit`.** `create_react_agent` wraps the graph with a configurable `recursion_limit`. For the POC this is set to `MAX_AGENT_STEPS * 2` (since LangGraph counts each node visit, and a tool step visits two nodes — the agent node and the tool node). The orchestrator additionally maintains its own step counter (LLM call counter) to produce the accurate `agent_steps_taken` metadata and `finish_reason: "length"` logic.

**Router sub-call errors abort the session immediately.** If the Router returns a non-200 response or a network error occurs, the orchestrator catches the exception from the LangChain client, sets `response.content` to a descriptive error message, and returns HTTP 502 with the partial IMF (including whatever metadata has been accumulated so far). The session entry is still written to the Session_Store for debugging.

---

## Components and Interfaces

### Module Layout

```
services/agent-framework/
├── main.py                   # FastAPI app factory, lifespan handler, router wiring
├── config.py                 # pydantic-settings Settings
├── metrics.py                # prometheus_client Counter / Histogram definitions
├── audit.py                  # stdout JSON audit event emitter
├── logging_config.py         # structured JSON logger factory
├── agent/
│   ├── __init__.py
│   ├── orchestrator.py       # LangGraph ReAct agent setup and run loop
│   └── session_store.py      # in-memory session dict + asyncio.Lock
├── tools/
│   ├── __init__.py
│   ├── catalog.yaml          # tool registry YAML (name, description, parameters)
│   ├── registry.py           # loads catalog + maps name → implementation callable
│   ├── calculator.py         # AST-based safe math eval
│   ├── get_time.py           # datetime.now(timezone.utc).isoformat()
│   └── web_search.py         # mocked results
├── routers/
│   ├── __init__.py
│   ├── agent.py              # POST /agent/run
│   └── health.py             # GET /health
├── schemas/
│   ├── __init__.py
│   └── imf.py                # IMF Pydantic models (aligned with API Gateway conventions)
├── requirements.txt
└── Dockerfile
```


### `config.py` — Environment-Driven Settings

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Required — startup fails if absent or empty
    router_url: str = "http://router:8082"       # ROUTER_URL
    gateway_api_key: str = "poc-secret-key"      # GATEWAY_API_KEY (must be set at deploy)
    tool_catalog_path: str = "/config/tools/catalog.yaml"  # TOOL_CATALOG_PATH

    # Optional with defaults
    log_level: str = "INFO"                      # LOG_LEVEL
    max_agent_steps: int = 10                    # MAX_AGENT_STEPS [1, 50]
    port: int = 8083                             # PORT
    metrics_port: int = 9090                     # METRICS_PORT
    agent_sub_call_timeout_seconds: float = 30.0 # per-LLM-call timeout to Router

settings = Settings()
```

The lifespan handler validates:
- `router_url`, `gateway_api_key` non-empty
- `max_agent_steps` in range `[1, 50]`
- `tool_catalog_path` file exists and is valid YAML with required fields
- If any check fails: log ERROR to stdout and `sys.exit(1)` before HTTP listener starts.

### `main.py` — App Factory and Lifespan

```python
from contextlib import asynccontextmanager
import sys
from fastapi import FastAPI
from agent_framework.tools.registry import load_tool_registry
from agent_framework.logging_config import get_logger

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Validate required env vars
    for field in ("router_url", "gateway_api_key", "tool_catalog_path"):
        if not getattr(settings, field):
            logger.error(f"{field.upper()} is not set or empty; refusing to start")
            sys.exit(1)

    # 2. Validate max_agent_steps range
    if not (1 <= settings.max_agent_steps <= 50):
        logger.error("MAX_AGENT_STEPS out of range [1, 50]; refusing to start")
        sys.exit(1)

    # 3. Load tool catalog (fail fast if missing or invalid)
    tool_registry = load_tool_registry(settings.tool_catalog_path)
    if tool_registry is None:
        sys.exit(1)  # load_tool_registry logs the specific failure

    # 4. Store on app.state
    app.state.settings = settings
    app.state.tool_registry = tool_registry
    logger.info("Agent Framework started", extra={"extra_fields": {
        "router_url": settings.router_url,
        "max_agent_steps": settings.max_agent_steps,
        "tools_loaded": list(tool_registry.keys()),
    }})
    yield
    logger.info("Agent Framework stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Framework", lifespan=lifespan)
    from agent_framework.routers import agent, health
    app.include_router(agent.router)
    app.include_router(health.router)
    return app

app = create_app()

# Separate metrics ASGI app (port 9090)
from prometheus_client import make_asgi_app as _make_metrics_app
import agent_framework.metrics  # noqa: F401 — registers counters in default registry
metrics_app = _make_metrics_app()
```

Uvicorn is started with two apps in `start.sh` or `Dockerfile CMD`:
```
uvicorn agent_framework.main:app --host 0.0.0.0 --port 8083 &
uvicorn agent_framework.main:metrics_app --host 0.0.0.0 --port 9090
```


### `agent/orchestrator.py` — LangGraph ReAct Agent

This is the core of the service. It creates a new agent graph per session, runs the loop, tracks step counts, and returns the fully-populated output IMF.

```python
import time, uuid
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage

from agent_framework.config import settings
from agent_framework.tools.registry import ToolRegistry
from agent_framework.agent.session_store import SessionStore
from agent_framework import metrics
from agent_framework.audit import emit_audit_event

async def run_agent_session(
    imf: dict,
    tool_registry: ToolRegistry,
    session_store: SessionStore,
) -> tuple[dict, int]:
    """
    Runs the ReAct agent loop for the given IMF.
    Returns (output_imf, http_status_code).
    """
    t0 = time.monotonic()
    request_id = imf["request_id"]
    session_id = str(uuid.uuid4())
    user_id = imf.get("user", {}).get("user_id", "poc-user")

    # Initialize session store entry
    await session_store.init_session(session_id)

    # Emit session start audit event
    emit_audit_event({
        "audit_id": str(uuid.uuid4()),
        "request_id": request_id,
        "session_id": session_id,
        "timestamp_utc": _utcnow(),
        "user_id": user_id,
        "layer": "agent",
        "event_type": "agent_session_start",
        "outcome": "pass",
    })

    # Build LLM client pointing to Router
    llm = ChatOpenAI(
        base_url=f"{settings.router_url}/v1",
        api_key=settings.gateway_api_key,
        model="llama3.2:3b",
        timeout=settings.agent_sub_call_timeout_seconds,
    )

    # Bind tools from registry
    tools = list(tool_registry.values())
    graph = create_react_agent(llm, tools=tools)

    # Prepare initial messages from IMF
    initial_messages = [
        HumanMessage(content=m["content"])
        if m["role"] == "user"
        else _to_lc_message(m)
        for m in imf["request"]["messages"]
    ]

    step_count = 0
    tools_called: list[str] = []
    final_content: str | None = None
    finish_reason = "stop"

    try:
        # Stream events from the graph to track steps and tool calls
        async for event in graph.astream_events(
            {"messages": initial_messages},
            config={"recursion_limit": settings.max_agent_steps * 2 + 1},
            version="v2",
        ):
            kind = event.get("event")

            if kind == "on_chat_model_end":
                step_count += 1
                output = event.get("data", {}).get("output")
                if output:
                    content = _extract_content(output)
                    if content:
                        final_content = content

                if step_count >= settings.max_agent_steps:
                    finish_reason = "length"
                    break

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = event.get("data", {}).get("input", {})
                tools_called.append(tool_name)
                metrics.tool_calls_total.labels(tool_name=tool_name).inc()

                emit_audit_event({
                    "audit_id": str(uuid.uuid4()),
                    "request_id": request_id,
                    "session_id": session_id,
                    "timestamp_utc": _utcnow(),
                    "layer": "agent",
                    "event_type": "agent_tool_call",
                    "tool_name": tool_name,
                    "tool_input": _serialize_tool_input(tool_input),
                    "outcome": "pass",
                })

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                result_str = str(event.get("data", {}).get("output", ""))
                await session_store.append_step(
                    session_id=session_id,
                    step=step_count,
                    tool_called=tool_name,
                    result_summary=result_str[:200],
                )

    except Exception as exc:
        error_detail = _classify_router_error(exc)
        if error_detail is not None:
            # Router sub-call failure → HTTP 502
            output_imf = _build_output_imf(
                imf, session_id, step_count, tools_called,
                content=f"Router sub-call failed: {error_detail}",
                finish_reason=None,
            )
            _emit_session_complete(
                request_id, session_id, step_count, tools_called,
                int((time.monotonic() - t0) * 1000), "error",
            )
            metrics.sessions_total.labels(outcome="error").inc()
            metrics.errors_total.labels(error_code="502").inc()
            return output_imf, 502
        # Unhandled internal error → HTTP 500
        raise

    # Clamp step count and set finish_reason if max steps reached
    if step_count >= settings.max_agent_steps and finish_reason == "length":
        if not final_content:
            final_content = "Agent reached maximum step limit without producing a final answer."

    if not final_content:
        final_content = "Agent completed without producing a text response."

    # Build output IMF
    output_imf = _build_output_imf(
        imf, session_id, step_count, tools_called, final_content, finish_reason,
    )
    if finish_reason == "length":
        output_imf["metadata"]["max_steps_reached"] = True

    latency_ms = int((time.monotonic() - t0) * 1000)
    outcome = "max_steps_reached" if finish_reason == "length" else "pass"
    _emit_session_complete(
        request_id, session_id, step_count, tools_called, latency_ms, outcome,
    )
    metrics.sessions_total.labels(outcome=outcome).inc()
    metrics.session_latency.observe(time.monotonic() - t0)
    return output_imf, 200
```


### `agent/session_store.py` — In-Memory Session Store

```python
import asyncio
from dataclasses import dataclass, field

@dataclass
class StepRecord:
    step: int
    tool_called: str
    result_summary: str  # first 200 chars of tool result

class SessionStore:
    def __init__(self):
        self._store: dict[str, list[StepRecord]] = {}
        self._lock = asyncio.Lock()

    async def init_session(self, session_id: str) -> None:
        """Creates or replaces a session entry with an empty step list."""
        async with self._lock:
            self._store[session_id] = []

    async def append_step(
        self, session_id: str, step: int,
        tool_called: str, result_summary: str,
    ) -> None:
        """Appends a step record to the session. No-op if session_id not found."""
        async with self._lock:
            if session_id in self._store:
                self._store[session_id].append(
                    StepRecord(step=step, tool_called=tool_called,
                               result_summary=result_summary)
                )

    async def get_steps(self, session_id: str) -> list[StepRecord]:
        async with self._lock:
            return list(self._store.get(session_id, []))

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._store

# Module-level singleton — created at process start
session_store = SessionStore()
```

A single `asyncio.Lock` is sufficient because FastAPI's async handlers run on a single asyncio event loop. The lock prevents concurrent coroutines from interleaving reads and writes to the dict.

### `tools/registry.py` — Tool Loading and Mapping

```python
import yaml, pathlib, logging
from langchain_core.tools import BaseTool
from typing import Optional

logger = logging.getLogger(__name__)

ToolRegistry = dict[str, BaseTool]

def load_tool_registry(catalog_path: str) -> Optional[ToolRegistry]:
    """
    Loads catalog.yaml and returns a dict mapping tool name → LangChain BaseTool.
    Returns None (and logs ERROR) on any failure.
    """
    try:
        data = yaml.safe_load(pathlib.Path(catalog_path).read_text())
        tools_data = data.get("tools", [])
        if not tools_data:
            logger.error(f"Tool catalog is empty: {catalog_path}")
            return None

        from agent_framework.tools.calculator import calculator_tool
        from agent_framework.tools.get_time import get_current_time_tool
        from agent_framework.tools.web_search import web_search_tool

        impl_map: dict[str, BaseTool] = {
            "calculator": calculator_tool,
            "get_current_time": get_current_time_tool,
            "web_search": web_search_tool,
        }

        registry: ToolRegistry = {}
        for entry in tools_data:
            name = entry.get("name")
            description = entry.get("description")
            if not name or not description:
                logger.error(
                    f"Tool catalog entry missing 'name' or 'description': {entry}"
                )
                return None
            if name not in impl_map:
                logger.error(
                    f"Tool '{name}' declared in catalog has no implementation"
                )
                return None
            registry[name] = impl_map[name]

        logger.info("Tool registry loaded", extra={"extra_fields": {
            "tools": list(registry.keys()),
        }})
        return registry

    except FileNotFoundError:
        logger.error(f"Tool catalog file not found: {catalog_path}")
    except yaml.YAMLError as exc:
        logger.error(f"Malformed tool catalog YAML at {catalog_path}: {exc}")
    return None
```


### `tools/calculator.py` — Safe AST Evaluator

The calculator uses Python's `ast` module to parse the expression and a whitelist-based AST visitor to evaluate only permitted nodes. This approach is provably safe: it never calls `eval()` or `exec()`.

```python
import ast
from langchain_core.tools import tool

# Permitted AST node types
_SAFE_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Num,          # Python ≤ 3.7 literal; kept for compatibility
    ast.Constant,     # Python 3.8+ numeric literal
    # Permitted operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
    # Unary
    ast.UAdd, ast.USub,
)

class _SafeEvaluator(ast.NodeVisitor):
    """Evaluates only whitelisted arithmetic AST nodes. Raises ValueError on any other node."""

    def visit(self, node: ast.AST):
        if not isinstance(node, _SAFE_NODES):
            raise ValueError(
                f"Unsafe expression: '{type(node).__name__}' is not permitted"
            )
        return super().visit(node)

    def visit_Expression(self, node): return self.visit(node.body)
    def visit_Constant(self, node):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"Non-numeric literal: {node.value!r}")
        return node.value
    def visit_Num(self, node): return node.n  # Python ≤ 3.7 compatibility
    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = node.op
        if isinstance(op, ast.Add):    return left + right
        if isinstance(op, ast.Sub):    return left - right
        if isinstance(op, ast.Mult):   return left * right
        if isinstance(op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left / right
        if isinstance(op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left // right
        if isinstance(op, ast.Mod):
            if right == 0:
                raise ZeroDivisionError("Modulo by zero")
            return left % right
        if isinstance(op, ast.Pow):    return left ** right
        raise ValueError(f"Unsupported operator: {type(op).__name__}")
    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.USub): return -operand
        if isinstance(node.op, ast.UAdd): return +operand
        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

def _safe_eval(expression: str) -> str:
    """Parse and evaluate an arithmetic expression string. Returns result as string."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        return f"Error: invalid expression syntax: {expression!r}"

    evaluator = _SafeEvaluator()
    try:
        result = evaluator.visit(tree)
    except ValueError as exc:
        return f"Error: {exc}"
    except ZeroDivisionError as exc:
        return f"Error: {exc}"
    except OverflowError:
        return "Error: numeric overflow"

    # Format: integer result → no decimal point; float → strip trailing zeros
    if isinstance(result, float) and result.is_integer():
        return str(int(result))
    if isinstance(result, float):
        return f"{result:g}"
    return str(result)

@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression. Returns the result as a string."""
    if not expression or not expression.strip():
        return "Error: expression must be a non-empty string"
    return _safe_eval(expression)
```

**Rationale for AST approach over `eval()`:** The `ast.parse` + visitor pattern is the industry-standard safe math evaluator pattern for Python. `eval()` is never called anywhere in this module. The whitelist approach ensures that even if an attacker crafts a clever expression, only the whitelisted node types can be visited — any other node type raises `ValueError` immediately.

### `tools/get_time.py` — Current Time Tool

```python
from datetime import datetime, timezone
from langchain_core.tools import tool

@tool
def get_current_time() -> str:
    """Return the current UTC date and time in ISO-8601 format."""
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        return f"Error: could not retrieve system time: {exc}"
```

The returned string from `datetime.now(timezone.utc).isoformat()` always includes the `+00:00` UTC offset because `timezone.utc` is passed as the tz argument. Example output: `2026-06-01T14:23:45.123456+00:00`.

### `tools/web_search.py` — Mocked Web Search

```python
from langchain_core.tools import tool

_MOCK_TEMPLATE = (
    "[POC Mock] Search results for '{query}': "
    "This is a simulated result. "
    "In production this would query an enterprise search system."
)

@tool
def web_search(query: str) -> str:
    """Search for information on a topic (simulated — no real HTTP calls made)."""
    if not query or not query.strip():
        return "Error: query must be a non-empty string"
    if len(query) > 1000:
        query = query[:1000]
    return _MOCK_TEMPLATE.format(query=query)
```

No outbound HTTP calls are made. The mock template always includes the original `query` value as a substring (via `.format(query=query)`).


### `tools/catalog.yaml` — Tool Declarations

```yaml
tools:
  - name: "calculator"
    description: "Evaluate a mathematical expression using safe arithmetic operators only."
    parameters:
      expression:
        type: string
        required: true
        description: "A mathematical expression, e.g. '2 + 2 * 10' or '(3.14 * 5) ** 2'"

  - name: "get_current_time"
    description: "Get the current UTC date and time in ISO-8601 format."
    parameters: {}

  - name: "web_search"
    description: "Search for information on a topic (simulated for POC)."
    parameters:
      query:
        type: string
        required: true
        max_length: 1000
        description: "The search query string."
```

### `routers/agent.py` — POST /agent/run

```python
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from agent_framework.schemas.imf import IMFDocument
from agent_framework.agent.orchestrator import run_agent_session
from agent_framework.agent.session_store import session_store
from agent_framework import metrics
import time, logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/agent/run")
async def agent_run(
    body: IMFDocument,
    request: Request,
    background_tasks: BackgroundTasks,
):
    t0 = time.monotonic()
    imf = body.model_dump()
    request_id = imf.get("request_id", "unknown")

    # Validate agentic flag
    if not imf.get("extensions", {}).get("agentic"):
        metrics.errors_total.labels(error_code="400").inc()
        return JSONResponse(status_code=400, content={
            "error": "validation_error",
            "field": "extensions.agentic",
            "message": "extensions.agentic must be true to invoke the agent",
            "request_id": request_id,
        })

    try:
        output_imf, status_code = await run_agent_session(
            imf=imf,
            tool_registry=request.app.state.tool_registry,
            session_store=session_store,
        )
        if status_code >= 400:
            error_code = str(status_code)
            metrics.errors_total.labels(error_code=error_code).inc()
        return JSONResponse(status_code=status_code, content=output_imf)

    except Exception as exc:
        logger.error("unhandled_exception", extra={"extra_fields": {
            "request_id": request_id,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }}, exc_info=True)
        metrics.errors_total.labels(error_code="500").inc()
        # Return partial IMF on unhandled error
        imf["response"] = {"content": None, "finish_reason": None}
        return JSONResponse(status_code=500, content={
            "error": "internal_error",
            "request_id": request_id,
            "imf": imf,
        })
```

### `audit.py` — Stdout JSON Audit Event Emitter

```python
import json, sys, logging

logger = logging.getLogger(__name__)

def emit_audit_event(event: dict) -> None:
    """
    Writes a single JSON line to stdout. Never raises.
    The event dict must be JSON-serializable.
    """
    try:
        line = json.dumps(event, default=str)   # default=str handles datetime, UUID
        print(line, flush=True)
    except Exception as exc:
        logger.warning(f"Failed to emit audit event: {exc}")
```

`json.dumps` with `default=str` ensures the line is always a single flat JSON object with no embedded newlines. The `print(..., flush=True)` ensures the line is not buffered in Docker/Kubernetes log capture.

### `metrics.py` — Prometheus Definitions

```python
from prometheus_client import Counter, Histogram

sessions_total = Counter(
    "llm_agent_framework_sessions_total",
    "Total agent sessions by outcome",
    labelnames=["outcome"],  # "pass" | "max_steps_reached" | "error"
)

tool_calls_total = Counter(
    "llm_agent_framework_tool_calls_total",
    "Total tool invocations by tool name",
    labelnames=["tool_name"],
)

session_latency = Histogram(
    "llm_agent_framework_session_latency_seconds",
    "End-to-end agent session duration in seconds",
)

errors_total = Counter(
    "llm_agent_framework_errors_total",
    "Total error responses by HTTP status code",
    labelnames=["error_code"],  # "400" | "500" | "502"
)
```

---

## Data Models

### IMF Pydantic Models (`schemas/imf.py`)

The Agent Framework reuses the platform IMF schema. The Pydantic models here are structurally identical to the API Gateway's `schemas/imf.py` with the addition of `extensions` validation for `agentic`.

```python
from pydantic import BaseModel, Field, field_validator
import re

UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

class IMFMessage(BaseModel):
    role: str
    content: str

class IMFUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class IMFResponse(BaseModel):
    content: str | None = None
    finish_reason: str | None = None
    usage: IMFUsage = Field(default_factory=IMFUsage)

class IMFGovernance(BaseModel):
    pii_masked: bool = False
    pii_fields_detected: list[str] = Field(default_factory=list)
    injection_score: float = 0.0
    jailbreak_score: float = 0.0
    content_safety_passed: bool = True
    human_approval_required: bool = False
    human_approval_status: str = "not_required"
    policy_decisions: list = Field(default_factory=list)

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
    roles: list[str] = Field(default_factory=lambda: ["developer"])
    auth_method: str = "api_key"

class IMFRequest(BaseModel):
    model: str | None = None
    task_type: str | None = None
    messages: list[IMFMessage] = Field(min_length=1)
    stream: bool = False
    max_tokens: int = 2048
    temperature: float = 0.7

class IMFDocument(BaseModel):
    request_id: str
    trace_id: str
    span_id: str = ""
    timestamp_utc: str
    user: IMFUser = Field(default_factory=IMFUser)
    request: IMFRequest
    governance: IMFGovernance = Field(default_factory=IMFGovernance)
    routing: IMFRouting = Field(default_factory=IMFRouting)
    cache: IMFCache = Field(default_factory=IMFCache)
    response: IMFResponse = Field(default_factory=IMFResponse)
    metadata: dict = Field(default_factory=dict)
    extensions: dict = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, v: str) -> str:
        if not UUID4_RE.match(v):
            raise ValueError("request_id must be a valid UUID-v4")
        return v
```


### IMF Field Contract

**Fields the Agent Framework reads from the inbound IMF:**

| Field | Validation | Used for |
|---|---|---|
| `request_id` | UUID v4 regex | Session correlation, audit events |
| `trace_id` | Non-empty string | Passed through unchanged |
| `request.messages` | Non-empty array | Initial agent conversation context |
| `extensions.agentic` | Must be `true` | Entry gate; HTTP 400 if absent/false |
| `user.user_id` | Optional string | Audit event `user_id` field |
| `user.department` | Optional string | Passed through unchanged |
| `user.roles` | Optional list | Passed through unchanged |
| `user.auth_method` | Optional string | Passed through unchanged |

**Fields the Agent Framework writes to the outbound IMF:**

| Field | Value |
|---|---|
| `metadata.agent_session_id` | UUID v4 string — the session identifier |
| `metadata.agent_steps_taken` | Integer — total LLM calls made in the loop |
| `metadata.tools_called` | Ordered list of tool name strings (duplicates preserved) |
| `response.content` | Final synthesized answer (non-empty string on success) |
| `response.finish_reason` | `"stop"` on natural completion; `"length"` at MAX_AGENT_STEPS |

**Fields the Agent Framework does not touch:** All `governance`, `routing`, `cache`, `request.*` (except `messages` consumed as input), `trace_id`, `span_id`, `timestamp_utc`, `user.*`.

### Audit Event Schema (Agent Layer)

All agent audit events follow the platform audit record schema with agent-specific `event_type` values:

```python
# agent_session_start
{
    "audit_id": "uuid-v4",
    "request_id": "uuid-v4",
    "session_id": "uuid-v4",
    "timestamp_utc": "ISO-8601",
    "user_id": "string",
    "layer": "agent",
    "event_type": "agent_session_start",
    "outcome": "pass"
}

# agent_tool_call (success)
{
    "audit_id": "uuid-v4",
    "request_id": "uuid-v4",
    "session_id": "uuid-v4",
    "timestamp_utc": "ISO-8601",
    "layer": "agent",
    "event_type": "agent_tool_call",
    "tool_name": "calculator",
    "tool_input": "{\"expression\": \"2+2\"}",  # JSON string, max 4096 chars
    "outcome": "pass"  # or "error"
    # "error_detail": "..." when outcome="error"
}

# agent_session_complete
{
    "audit_id": "uuid-v4",
    "request_id": "uuid-v4",
    "session_id": "uuid-v4",
    "timestamp_utc": "ISO-8601",
    "layer": "agent",
    "event_type": "agent_session_complete",
    "steps_taken": 3,
    "tools_called": ["calculator", "get_current_time"],
    "total_latency_ms": 1250,
    "outcome": "pass"  # or "max_steps_reached" | "error"
}
```

---

## Helm Chart Structure

```
llm-platform/charts/agent-framework/
├── Chart.yaml
├── values.yaml
├── README.md
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── networkpolicy.yaml
    ├── servicemonitor.yaml
    ├── hpa.yaml             # autoscaling.enabled: false for POC
    └── configmap.yaml       # mounts tools/catalog.yaml at TOOL_CATALOG_PATH
```

### `Chart.yaml`

```yaml
apiVersion: v2
name: agent-framework
description: "Layer 6 — Agent Framework for the Enterprise On-Prem LLM Platform (POC)"
type: application
version: 0.1.0
appVersion: "0.1.0"
```

### `values.yaml`

```yaml
replicaCount: 1

image:
  repository: registry.local/agent-framework
  tag: ""          # MUST be overridden at deploy time
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8083

env:
  ROUTER_URL: "http://router:8082"
  LOG_LEVEL: "INFO"
  MAX_AGENT_STEPS: "10"
  TOOL_CATALOG_PATH: "/config/tools/catalog.yaml"
  # GATEWAY_API_KEY has no default; must be set at deploy time
  GATEWAY_API_KEY: "poc-secret-key"   # placeholder; replace before non-dev deployment

resources:
  requests:
    cpu: "200m"
    memory: "512Mi"
  limits:
    cpu: "1"
    memory: "1Gi"

autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

vault:
  enabled: false
  role: "agent-framework-role"
  secretPath: "secret/llm-platform/agent-framework"

observability:
  metrics:
    enabled: true
    port: 9090
  tracing:
    enabled: false
    endpoint: "http://otel-collector:4317"
```

### NetworkPolicy (key rules)

The NetworkPolicy restricts:
- **Ingress:** Only from pods with label `app: router` (port 8083)
- **Egress:**
  - Pods with label `app: router` on TCP port 8082 (LLM sub-calls)
  - DNS on port 53 (UDP/TCP)

No direct egress to inference backends, Audit Store, or Cache Layer — all inference traffic flows through the Router.

### `templates/configmap.yaml`

The `catalog.yaml` content is mounted as a ConfigMap so the file can be updated without rebuilding the image:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "agent-framework.fullname" . }}-tools
data:
  catalog.yaml: |
    {{ .Files.Get "files/catalog.yaml" | nindent 4 }}
```

The ConfigMap is mounted at `/config/tools/` and `TOOL_CATALOG_PATH` defaults to `/config/tools/catalog.yaml`.


---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The Agent Framework contains significant pure-function logic — the calculator evaluator, the tool dispatch mapping, session metadata accumulation, IMF field preservation, and audit event construction — that is well-suited to property-based testing. The property-based testing library used is **Hypothesis** (Python).

**Property reflection note:** After completing the prework analysis, the following consolidations were applied:
- Requirements 3.4, 3.5, 3.6 all concern the step budget invariant → merged into Property 1.
- Requirements 3.3 and 3.7 are the same tool injection requirement → merged into Property 2.
- Requirements 2.1 and 2.3 both verify the `session_id` UUID v4 output → merged into Property 3.
- Requirements 10.3 and 10.4 are the same IMF preservation requirement → Property 8.
- Requirements 6.7 and 6.8 both concern calculator output formatting → merged into Property 6.
- Requirements 8.2 and 8.5 are identical web_search query-as-substring requirements → Property 7.

---

### Property 1: Step count never exceeds MAX_AGENT_STEPS

*For any* valid agent session and any positive integer value of `MAX_AGENT_STEPS`, the total number of LLM sub-calls made by the Agent_Orchestrator during the ReAct loop SHALL NOT exceed `MAX_AGENT_STEPS`. When the budget is exhausted, `response.finish_reason` SHALL be `"length"` and `metadata.max_steps_reached` SHALL be `true`.

**Validates: Requirements 3.5, 3.6**

---

### Property 2: Tool results always injected with role "tool"

*For any* tool call produced by the LLM during the ReAct loop, the result returned by the Tool_Executor SHALL be injected back into the conversation context as a message with `role: "tool"` before the next LLM sub-call is initiated. The LLM's subsequent reasoning step SHALL have access to the tool output.

**Validates: Requirements 3.3, 3.7**

---

### Property 3: session_id is always a valid UUID v4

*For any* call to `POST /agent/run` with a valid IMF, the `metadata.agent_session_id` field in the response SHALL be a non-null string matching the UUID v4 format (`[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`). No two concurrently active sessions SHALL share the same `session_id`.

**Validates: Requirements 2.1, 2.3, 2.5**

---

### Property 4: response.content always non-empty on HTTP 200

*For any* agent session that completes with HTTP 200, the `response.content` field in the returned IMF SHALL be a non-null, non-empty string.

**Validates: Requirements 1.2, 10.5**

---

### Property 5: calculator never calls eval or exec — pure AST evaluation

*For any* expression string containing Python builtins, `__import__`, attribute access (`obj.attr`), subscript access (`obj[k]`), or function/method calls, the `calculator` tool SHALL return an error string without executing the expression. The `eval()` and `exec()` functions SHALL never be called anywhere in the calculator implementation, regardless of the expression content.

**Validates: Requirements 6.2, 6.3, 6.4**

---

### Property 6: calculator output formatting is correct for all result types

*For any* arithmetic expression containing only permitted constructs that evaluates to an integer result, the `calculator` tool SHALL return a string with no decimal point (e.g., `"4"` not `"4.0"`). For expressions evaluating to a floating-point result, the tool SHALL return a decimal string with no unnecessary trailing zeros (e.g., `"2.5"` not `"2.50000"`).

**Validates: Requirements 6.7, 6.8**

---

### Property 7: web_search result always contains query as substring

*For any* non-empty, non-whitespace-only query string, the string returned by the `web_search` tool SHALL contain the original `query` value as a substring and SHALL contain at least one non-whitespace character. No outbound HTTP requests SHALL be made during the invocation.

**Validates: Requirements 8.2, 8.3, 8.5**

---

### Property 8: IMF field preservation — only metadata and response blocks are modified

*For any* inbound IMF, the outbound IMF returned by `POST /agent/run` (on both 200 and error responses) SHALL have all fields outside `metadata` and `response` blocks identical to the inbound IMF. Specifically: `request_id`, `trace_id`, `span_id`, `timestamp_utc`, `user.*`, `request.*`, `governance.*`, `routing.*`, `cache.*`, and `extensions.*` SHALL be preserved without modification.

**Validates: Requirements 10.3, 10.4**

---

### Property 9: Every audit event has mandatory invariant fields

*For any* audit event emitted to stdout by the Agent Framework (session start, tool call, or session complete), the JSON object SHALL contain: `audit_id` matching the UUID v4 format regex, `layer` equal to `"agent"`, and `outcome` equal to one of `"pass"`, `"block"`, `"error"`, or `"max_steps_reached"`. Each event SHALL be a single JSON line with no embedded newlines.

**Validates: Requirements 11.4, 11.5**

---

### Property 10: Router sub-call failure returns 502 with non-empty partial metadata

*For any* Router HTTP sub-call that returns a non-200 status code or raises a connection/timeout exception, the Agent Framework SHALL return HTTP 502 with a non-empty `response.content` error message that includes the failure cause. The response SHALL still include `metadata.agent_session_id`, `metadata.agent_steps_taken`, and `metadata.tools_called` reflecting all work completed before the abort.

**Validates: Requirements 4.3, 10.6, 10.7**

---

### Property 11: Invalid IMF inputs always return HTTP 400

*For any* POST to `/agent/run` where `request.messages` is absent, empty, or `extensions.agentic` is absent or `false`, the Agent Framework SHALL return HTTP 400 with an error body identifying the specific invalid field. No agent session SHALL be created.

**Validates: Requirements 1.3, 10.1, 10.8**


---

## Error Handling

### Error Response Format

All error responses use the canonical format consistent with other platform layers:
```json
{"error": "error_code", "request_id": "uuid", "message": "human-readable description"}
```

### Error Taxonomy

| Condition | HTTP Status | Error code | Notes |
|---|---|---|---|
| `extensions.agentic` absent or false | 400 | `"validation_error"` | Field: `extensions.agentic` |
| `request.messages` absent or empty | 400 | `"validation_error"` | Field: `request.messages` |
| `request_id` absent or not UUID v4 | 422 | `"validation_error"` | Raised by Pydantic |
| Invalid JSON body | 400 | `"invalid_json"` | Raised by FastAPI before handler |
| Required IMF field missing/malformed | 400 | `"validation_error"` | Field name in error body |
| Router sub-call non-200 HTTP response | 502 | `"router_error"` | Router status in message |
| Router sub-call timeout (30s) | 502 | `"router_timeout"` | Per-call timeout |
| Router sub-call connection refused | 502 | `"router_unreachable"` | |
| Unhandled internal exception | 500 | `"internal_error"` | Partial IMF returned |
| Undefined path | 404 | FastAPI default | |

### Router Sub-call Error Classification

The orchestrator wraps the LangChain `ChatOpenAI` call in a `try/except` block:

```python
def _classify_router_error(exc: Exception) -> str | None:
    """
    Returns a human-readable error detail string for known Router failure modes,
    or None for unexpected exceptions that should propagate.
    """
    import httpx
    from langchain_core.exceptions import OutputParserException
    if isinstance(exc, httpx.TimeoutException):
        return "Router sub-call timed out after 30s"
    if isinstance(exc, httpx.ConnectError):
        return "Router is unreachable"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Router returned HTTP {exc.response.status_code}"
    return None  # let unexpected exceptions bubble to the 500 handler
```

### Startup Failures

The lifespan handler validates all configuration before the HTTP listener starts. If any check fails, `sys.exit(1)` is called with a structured ERROR log identifying the exact failure. This prevents the service from starting in a partially-configured state that would silently fail on the first request.

### Unhandled Exception Handler

A FastAPI global exception handler catches any `Exception` not handled by route-level try/except, logs a structured `ERROR` record with `session_id`, `request_id`, `exception_type`, `exception_message`, `traceback`, and `latency_ms`, increments `errors_total{error_code="500"}`, and returns HTTP 500 with the partial IMF.

### Max Steps Reached

When the step budget is exhausted:
- `response.finish_reason` is set to `"length"`
- `metadata.max_steps_reached` is set to `true`
- `response.content` is the most recent non-empty LLM output, or a descriptive fallback message if no LLM text output was captured
- `sessions_total{outcome="max_steps_reached"}` is incremented
- The `agent_session_complete` audit event emits `outcome: "max_steps_reached"`
- HTTP 200 is returned (this is a valid, handled terminal state, not an error)

---

## Testing Strategy

### Dual Testing Approach

Both unit/example-based tests and property-based tests are required. Unit tests cover concrete integration points, specific examples, and edge cases. Property tests verify universal correctness guarantees across a wide input space using **Hypothesis**.

### Property-Based Testing (Hypothesis)

**Library:** `hypothesis` (Python)
**Configuration:** minimum 100 examples per property test (`@settings(max_examples=100)`)

Each property test references its design property with a comment:
```python
# Feature: agent-framework, Property 1: Step count never exceeds MAX_AGENT_STEPS
@given(st.integers(min_value=1, max_value=20), ...)
@settings(max_examples=100)
def test_step_count_never_exceeds_max_agent_steps(max_steps, ...):
    ...
```

Property tests to implement (one Hypothesis test per property):

1. `test_step_count_never_exceeds_max_agent_steps` — Generate `MAX_AGENT_STEPS` values and mock LLMs that always return tool calls. Verify `agent_steps_taken <= MAX_AGENT_STEPS` and `finish_reason = "length"` when budget is exhausted.

2. `test_tool_results_injected_as_role_tool` — Intercept LangGraph message flow. For any tool call, verify the injected message has `role = "tool"` before the next LLM call.

3. `test_session_id_always_valid_uuid_v4` — Generate arbitrary valid IMF inputs. Verify each `metadata.agent_session_id` matches `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`.

4. `test_response_content_nonempty_on_200` — Generate valid IMF inputs with mocked Router returning a response. Verify `response.content` is a non-empty string when HTTP 200 is returned.

5. `test_calculator_never_calls_eval_exec` — Generate expression strings containing builtins, `__import__`, `exec`, attribute access. Verify `_safe_eval()` returns an error string and `eval`/`exec` were never called (use `unittest.mock.patch` to instrument).

6. `test_calculator_output_formatting` — Generate integer and float arithmetic expressions. Verify integer results have no decimal point; float results have no trailing zeros.

7. `test_web_search_contains_query_as_substring` — Generate arbitrary non-empty, non-whitespace query strings (including unicode, long strings up to 1000 chars, strings with special characters). Verify the result always contains the query as a substring.

8. `test_imf_field_preservation` — Generate valid `IMFDocument` instances with arbitrary field values. Post to `/agent/run` with a mocked orchestrator. Verify all non-`metadata`/non-`response` fields in the response body are identical to the inbound IMF.

9. `test_audit_event_invariant_fields` — For any of the three event types (session_start, tool_call, session_complete), capture stdout and parse the JSON line. Verify `audit_id` is UUID v4, `layer = "agent"`, `outcome` is a valid enum value, and the line contains no embedded newlines.

10. `test_router_error_returns_502_with_partial_metadata` — Generate different Router error scenarios (non-200 codes 400–599, timeout, connection refused). Verify HTTP 502, `response.content` non-empty, and `metadata.agent_session_id` is present.

11. `test_invalid_imf_returns_400` — Generate invalid IMF variants (empty messages, missing messages, `agentic=false`, `agentic` absent). Verify HTTP 400 and error body identifies the specific field.

### Unit / Example-Based Tests

- `GET /health` returns 200 with `{"status": "ok"}` (no auth required)
- `GET /metrics` on port 9090 returns 200 with Prometheus text format
- Request to undefined path returns 404
- Startup with missing `TOOL_CATALOG_PATH` file calls `sys.exit(1)`
- Startup with malformed YAML in catalog calls `sys.exit(1)`
- `MAX_AGENT_STEPS=0` causes startup failure
- LLM returns direct answer (no tool call): session completes in 1 step with `finish_reason="stop"`
- Calculator with valid expression `"2 + 2"` returns `"4"`
- Calculator with `"1/0"` returns an error string containing "zero"
- Calculator with `"__import__('os').system('ls')"` returns an error string, never executes
- `get_current_time()` returns a string parseable by `datetime.fromisoformat()`
- `web_search(query="hello")` returns a string containing `"hello"` and no HTTP calls made
- `web_search(query="")` returns an error string
- Session store: concurrent `init_session` calls produce distinct entries
- `LOG_LEVEL=WARNING` suppresses `DEBUG` and `INFO` records in stdout
- Unrecognized `LOG_LEVEL` value defaults to `INFO` and emits one WARNING log

### Integration Tests

- Full end-to-end: mock Router returns a direct answer → verify HTTP 200, `response.content` non-empty, all three metadata fields present
- Full end-to-end: mock Router returns tool call then direct answer → verify `tools_called` list is correct, `agent_steps_taken = 2`
- Max steps reached: mock Router always returns tool call → verify `finish_reason="length"`, `max_steps_reached=true`, HTTP 200
- Router non-200 mid-session → verify HTTP 502, partial metadata present
- Prometheus counter `llm_agent_framework_sessions_total{outcome="pass"}` increments on successful session
- Prometheus counter `llm_agent_framework_tool_calls_total{tool_name="calculator"}` increments on each calculator invocation

### Test File Layout

```
services/agent-framework/tests/
├── conftest.py                        # TestClient fixtures, mock Router setup
├── unit/
│   ├── test_calculator.py             # calculator PBT + unit tests
│   ├── test_web_search.py             # web_search PBT + unit tests
│   ├── test_get_time.py               # get_current_time unit tests
│   ├── test_tool_registry.py          # registry loading unit tests
│   ├── test_session_store.py          # session store unit tests (concurrency)
│   ├── test_imf_models.py             # IMF Pydantic model validation
│   └── test_audit.py                  # audit event field invariant PBT
├── integration/
│   ├── test_agent_run_endpoint.py     # /agent/run full pipeline with mocked Router
│   ├── test_imf_preservation.py       # IMF field preservation PBT
│   ├── test_session_lifecycle.py      # session_id UUID, metadata fields
│   ├── test_error_handling.py         # Router error → 502, invalid IMF → 400
│   └── test_metrics.py                # Prometheus counter increments
└── smoke/
    ├── test_health.py
    ├── test_startup.py
    └── test_metrics_endpoint.py
```

