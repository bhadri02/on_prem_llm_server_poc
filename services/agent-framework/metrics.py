"""
services/agent-framework/metrics.py

Prometheus metrics definitions for the Agent Framework (Layer 6).

All metric objects are defined here and imported by other modules that need
to increment counters or observe histograms.  Importing this module registers
the metrics in the default prometheus_client registry.

Metrics (Requirement 13.2–13.5):
  - llm_agent_framework_sessions_total{outcome}
  - llm_agent_framework_tool_calls_total{tool_name}
  - llm_agent_framework_session_latency_seconds
  - llm_agent_framework_errors_total{error_code}
"""

from prometheus_client import Counter, Histogram

# Incremented exactly once per completed or aborted session.
# Valid outcome label values: "pass", "max_steps_reached", "error"
sessions_total = Counter(
    "llm_agent_framework_sessions_total",
    "Total number of agent sessions by outcome",
    ["outcome"],
)

# Incremented once per tool invocation regardless of success/failure.
tool_calls_total = Counter(
    "llm_agent_framework_tool_calls_total",
    "Total number of tool invocations by tool name",
    ["tool_name"],
)

# End-to-end session duration from receipt of /agent/run to final response.
session_latency = Histogram(
    "llm_agent_framework_session_latency_seconds",
    "Agent session end-to-end latency in seconds",
)

# Incremented on every 4xx and 5xx response from /agent/run.
# error_code is the numeric HTTP status code as a string ("400", "502", "500").
errors_total = Counter(
    "llm_agent_framework_errors_total",
    "Total number of error responses by HTTP status code",
    ["error_code"],
)
