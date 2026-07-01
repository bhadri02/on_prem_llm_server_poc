---
inclusion: manual
---

# Layer 6 — Agent Framework (POC)

> Load this file when working on the Agent Framework layer: `#06-layer-agent-framework`
> **Scope:** Proof-of-Concept — demonstrate a working single-agent loop with 2–3 tools.

---

## POC Goal

Show that the platform can execute a multi-step agentic task where the model calls tools and synthesizes a final response. For POC, implement a simple ReAct loop with a small tool registry (2–3 tools), in-memory short-term memory, and no Temporal/Argo workflow engine.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Agent Orchestrator | LangGraph (Python) | Single-agent ReAct loop; no DAG pipelines |
| Tool Registry | Python dict / YAML config | 2–3 hardcoded tools; no sandbox container |
| Function Calling Handler | LangGraph tool node | Parse tool call → execute → inject result |
| Short-Term Memory | In-process Python dict | No Redis; session state in memory |
| Long-Term Memory | **Skip for POC** | Not implemented |
| MCP Server Integration | **Skip for POC** | Not implemented |
| Workflow Engine | **Skip for POC** | No Temporal/Argo |

---

## Agent Entry Condition (POC)

Route to Agent Framework when:
- IMF `request.agentic: true` (client sets this flag), OR
- The Router detects the task type as `agentic` (optional for POC — Router can always pass to agent on explicit flag)

For POC, the client must explicitly set `"agentic": true` in the request payload.

---

## Agent Execution Loop (POC — LangGraph ReAct)

```
Receive IMF from Router
  │
  ├─ 1. Initialize LangGraph ReAct agent
  │       → Load tools from registry
  │       → Set initial state: { messages: IMF.request.messages }
  │
  ├─ 2. AGENT LOOP (max 10 steps for POC)
  │     │
  │     ├─ a. Model call → via Router → Security → Inference
  │     │       (forward sub-IMF to Router HTTP endpoint)
  │     │
  │     ├─ b. Parse model response
  │     │       → If tool_call: execute tool, inject result, loop
  │     │       → If final answer: exit loop
  │     │
  │     └─ c. On max steps: return partial result with warning
  │
  └─ 3. Return final IMF with synthesized response.content
```

> **POC Note:** Every model call within the agent loop goes through the Router (which calls Security → Inference). This proves the governance pipeline is not bypassed.

---

## Tool Registry (POC — 3 Tools)

```yaml
# tools/catalog.yaml (POC)
tools:
  - name: "web_search"
    description: "Search for information on a topic (simulated for POC)"
    parameters:
      query: { type: string, required: true }
    # POC: return a hardcoded/mocked search result; no real HTTP call

  - name: "calculator"
    description: "Evaluate a mathematical expression"
    parameters:
      expression: { type: string, required: true, example: "2 + 2 * 10" }
    # POC: eval() with safe math-only sandbox

  - name: "get_current_time"
    description: "Get the current UTC date and time"
    parameters: {}
    # POC: return datetime.utcnow().isoformat()
```

**Tool permission check (POC):** all tools allowed for any authenticated user — no OPA check.

---

## LangGraph Setup (POC)

```python
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

# Tools defined as Python functions decorated with @tool

# LLM client points to the Router's /v1/chat/completions
# Use langchain_community.chat_models.ChatOpenAI with base_url pointing to Router

llm = ChatOpenAI(
    base_url="http://router:8082/v1",
    api_key="poc-secret-key",
    model="llama3.2:3b",
)

agent = create_react_agent(llm, tools=[web_search, calculator, get_current_time])
```

---

## Short-Term Memory (POC — In-Process)

```python
# In-memory session store (Python dict)
# Keyed by session_id from IMF metadata
sessions: dict[str, list] = {}

# On each agent step, append step result
sessions[session_id].append({
    "step": step_number,
    "tool_called": tool_name,
    "result_summary": result[:200]  # truncate for memory
})
```

Sessions are lost on pod restart — acceptable for POC.

---

## IMF Fields This Layer Reads and Writes

**Reads:**
- `request.messages` — initial goal
- `request.agentic` — entry condition flag
- `user.*` — passed through to sub-IMFs

**Writes:**
```json
{
  "metadata": {
    "agent_session_id": "uuid",
    "agent_steps_taken": 3,
    "tools_called": ["calculator", "get_current_time"]
  },
  "response": {
    "content": "Final synthesized answer from agent"
  }
}
```

---

## Helm Chart: `llm-platform/charts/agent-framework/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/agent-framework
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8083

env:
  LOG_LEVEL: "INFO"
  ROUTER_URL: "http://router:8082"
  GATEWAY_API_KEY: "poc-secret-key"
  MAX_AGENT_STEPS: "10"
  TOOL_CATALOG_PATH: "/config/tools/catalog.yaml"

resources:
  requests:
    cpu: "200m"
    memory: "512Mi"
  limits:
    cpu: "1"
    memory: "1Gi"

autoscaling:
  enabled: false

vault:
  enabled: false
```

---

## Observability (POC)

- Structured JSON logs to stdout.
- Log per session: `session_id`, `steps_taken`, `tools_called`, `outcome`, `total_latency_ms`.
- Log per step: `step_number`, `tool_name`, `tool_result_summary`.

---

## Audit Events (POC)

Log to stdout:
- `agent_session_start`
- `agent_tool_call` — include tool name and input
- `agent_session_complete` — include steps taken

---

## POC Non-Goals (Explicitly Out of Scope)

- Temporal / Argo Workflows
- MCP server integration
- Long-term vector memory (Milvus)
- Redis session storage
- Tool sandboxing (isolated containers)
- OPA tool permission checks
- Multi-agent coordination
- DAG-based multi-model pipelines
- gRPC transport
