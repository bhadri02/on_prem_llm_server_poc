"""
agent_framework/metrics.py

Prometheus metrics definitions for the Agent Framework (Layer 6).

Provides the three mandatory platform metrics via `make_layer_metrics("agent")`,
plus one agent-specific extra metric (tool_calls_total).

Mandatory metrics (via shared factory):
  LAYER_METRICS.requests_total  — llm_agent_requests_total{status, department, model}
  LAYER_METRICS.latency_seconds — llm_agent_latency_seconds{department}
  LAYER_METRICS.errors_total    — llm_agent_errors_total{error_code, department}

Extra agent metric (kept as separate prometheus_client object):
  tool_calls_total — Counter tracking tool invocations by tool_name.

The agent orchestrator (agent/orchestrator.py) uses `tool_calls_total` to track
individual tool calls; the `LAYER_METRICS` are used at the session level.

Validates: Requirements 13.2–13.5
"""

from prometheus_client import Counter, Histogram

from shared.observability.metrics import make_layer_metrics

# ---------------------------------------------------------------------------
# Mandatory platform metrics (contract label schema)
# ---------------------------------------------------------------------------
LAYER_METRICS = make_layer_metrics("agent")

# ---------------------------------------------------------------------------
# Extra agent-specific metrics (kept alongside LAYER_METRICS)
# ---------------------------------------------------------------------------

# Legacy metric names for backward-compatible imports
# (agent orchestrator uses these names directly)
sessions_total = LAYER_METRICS.requests_total
errors_total = LAYER_METRICS.errors_total
session_latency = LAYER_METRICS.latency_seconds

# Agent-specific metric: tool calls counter
tool_calls_total = Counter(
    "llm_agent_framework_tool_calls_total",
    "Total tool calls",
    ["tool_name"],
)
