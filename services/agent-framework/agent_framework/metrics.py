"""
agent_framework/metrics.py

Prometheus metrics definitions for the Agent Framework (Layer 6).

All metric objects are defined here and imported by other modules that need
to increment counters or observe histograms. Importing this module registers
the metrics in the default prometheus_client registry.

Metrics (Requirements 13.2–13.5):
  - llm_agent_framework_sessions_total{outcome}
  - llm_agent_framework_tool_calls_total{tool_name}
  - llm_agent_framework_session_latency_seconds
  - llm_agent_framework_errors_total{error_code}
"""

from prometheus_client import Counter, Histogram

sessions_total = Counter(
    "llm_agent_framework_sessions_total",
    "Total agent sessions",
    ["outcome"],
)

tool_calls_total = Counter(
    "llm_agent_framework_tool_calls_total",
    "Total tool calls",
    ["tool_name"],
)

session_latency = Histogram(
    "llm_agent_framework_session_latency_seconds",
    "End-to-end session duration in seconds",
)

errors_total = Counter(
    "llm_agent_framework_errors_total",
    "Total errors by HTTP status code",
    ["error_code"],
)
