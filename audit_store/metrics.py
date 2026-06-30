from prometheus_client import Counter, Histogram

writes_total = Counter(
    "llm_audit_writes_total",
    "Total audit events successfully written",
    labelnames=["event_type", "layer"],
)

write_latency = Histogram(
    "llm_audit_write_latency_seconds",
    "Write handler latency",
    labelnames=["event_type", "layer"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
