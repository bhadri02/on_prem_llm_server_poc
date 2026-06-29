---
inclusion: manual
---

# Layer 8 — Observability Stack (POC)

> Load this file when working on the Observability layer: `#08-layer-observability`
> **Scope:** Proof-of-Concept — basic visibility into requests, errors, and latency.

---

## POC Goal

Deploy a minimal but functional observability stack that gives visibility into what the platform is doing. For POC, Prometheus + Grafana is sufficient. Distributed tracing (Jaeger/OTel) and ELK stack are optional. The primary deliverable is a working Grafana dashboard showing request flow across layers.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Metrics | Prometheus | Scrape `/metrics` from each layer |
| Dashboards | Grafana | 1 platform overview dashboard |
| Structured Logs | stdout JSON per service | No Elasticsearch / Kibana for POC |
| Distributed Tracing | **Optional for POC** | OTel + Jaeger only if time allows |
| GPU Metrics | **Skip for POC** | No DCGM Exporter unless GPU node present |
| Alertmanager | **Optional for POC** | Configure only if Slack/email available |

---

## Prometheus Setup (POC)

Deploy Prometheus via the `kube-prometheus-stack` Helm chart (includes Grafana).

**Scrape targets:** All platform services must expose `/metrics` on their service port.

For POC, each service can use the `prometheus_client` Python library (for FastAPI services) to expose a few counters and histograms.

**Minimum metrics per service (POC):**

```python
# Python (prometheus_client)
from prometheus_client import Counter, Histogram

requests_total = Counter(
    "llm_{layer}_requests_total",
    "Total requests",
    ["status", "department"]
)

latency_seconds = Histogram(
    "llm_{layer}_latency_seconds",
    "Request latency",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)
```

Replace `{layer}` with: `api_gateway`, `security`, `router`, `cache`, `inference`, `agent`.

---

## Grafana Dashboard (POC — Platform Overview)

One dashboard: **LLM Platform POC Overview**

Panels:
1. **Request Rate** — `rate(llm_api_gateway_requests_total[1m])` — requests/sec
2. **Error Rate** — `rate(llm_*_requests_total{status="error"}[1m])` — errors/sec
3. **End-to-End Latency P95** — histogram_quantile(0.95, ...) per layer
4. **Cache Hit Rate** — `llm_cache_requests_total{outcome="hit"}` / total
5. **Security Blocks** — `rate(llm_security_requests_total{outcome="block"}[1m])`
6. **Inference Requests** — `rate(llm_inference_requests_total[1m])` by model
7. **Active Agent Sessions** (if agent layer deployed)

Dashboard JSON exported and stored in `charts/observability/dashboards/poc-overview.json`.

---

## Structured Log Format (POC — Required for All Layers)

All services MUST log to stdout in JSON format. Kubernetes captures stdout; logs can be viewed with `kubectl logs`.

```json
{
  "timestamp": "ISO-8601",
  "level": "INFO | WARN | ERROR",
  "service": "api-gateway",
  "request_id": "uuid",
  "event": "request_received",
  "message": "human readable",
  "latency_ms": 42,
  "data": {}
}
```

Use Python `structlog` or a simple JSON formatter — consistent across all layers.

**Never log:** raw prompt content, PII, API keys.
**Always log:** `request_id` for cross-layer correlation.

---

## Optional: OpenTelemetry Tracing (POC)

If time allows, add basic OTel tracing:

- Deploy OTel Collector + Jaeger (both available as Helm charts).
- Each FastAPI service instruments with `opentelemetry-instrumentation-fastapi`.
- Propagate `traceparent` header between services.
- View end-to-end trace in Jaeger UI.

**OTel Collector config (minimal):**
```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
exporters:
  jaeger:
    endpoint: "jaeger-collector:14250"
    tls:
      insecure: true
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [jaeger]
```

---

## Helm Chart: `llm-platform/charts/observability/`

```yaml
# values.yaml (POC)
# Uses kube-prometheus-stack as a dependency

kube-prometheus-stack:
  enabled: true
  prometheus:
    prometheusSpec:
      retention: "7d"
      storageSpec:
        volumeClaimTemplate:
          spec:
            accessModes: [ReadWriteOnce]
            resources:
              requests:
                storage: 10Gi
  grafana:
    enabled: true
    adminPassword: "poc-admin"   # override in cluster secret
    dashboardsConfigMaps:
      - configMapName: grafana-poc-dashboards
        folder: "LLM Platform POC"
  alertmanager:
    enabled: false   # skip alerts for POC

# Optional OTel + Jaeger
opentelemetry-collector:
  enabled: false   # set to true if tracing desired

jaeger:
  enabled: false   # set to true if tracing desired
```

---

## Prometheus ServiceMonitor (Each Layer Must Have This)

Add this to each layer's Helm chart so Prometheus auto-discovers it:

```yaml
# templates/servicemonitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "chart.fullname" . }}
  labels:
    release: observability   # must match kube-prometheus-stack release name
spec:
  selector:
    matchLabels:
      app: {{ include "chart.name" . }}
  endpoints:
    - port: metrics
      path: /metrics
      interval: 15s
```

---

## POC Non-Goals (Explicitly Out of Scope)

- Elasticsearch / Kibana log aggregation
- DCGM GPU metrics exporter
- PagerDuty / Slack alert routing
- Cost attribution dashboard
- OTel sensitive data filtering pipeline
- Jaeger/Tempo distributed tracing (unless optional time allows)
- Custom alert rules
- Long-term metric retention (>7 days)
