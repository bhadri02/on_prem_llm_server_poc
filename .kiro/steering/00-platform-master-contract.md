---
inclusion: manual
---

# Enterprise On-Prem LLM Platform — Master Integration Contract

> **Inclusion:** Always (applies to all layers and all sessions)
> **Version:** 1.0 | June 2026
> **Current Development Phase:** POC (Proof-of-Concept)

---

## POC Phase Constraints

All layer steering files have been scoped to POC level. The following production features are **globally deferred** for all layers until the POC is validated:

| Deferred Feature | Production Target |
|---|---|
| Istio mTLS / service mesh | Phase 2 |
| HashiCorp Vault secret management | Phase 2 |
| OPA / Rego policy engine | Phase 2 |
| Redis HA (Sentinel/Cluster) | Phase 2 |
| Elasticsearch / ClickHouse audit store | Phase 2 |
| Horizontal Pod Autoscaling (HPA) | Phase 2 |
| gRPC inter-service transport | Phase 2 |
| OIDC / LDAP / SSO authentication | Phase 2 |
| ML classifiers (injection, jailbreak) | Phase 2 |
| LlamaGuard content moderation | Phase 2 |
| Milvus / Qdrant vector DB | Phase 2 |
| MLflow model registry | Phase 2 |
| Argo CD / GitOps | Phase 2 |
| Argo Rollouts canary deployment | Phase 2 |
| Cryptographic audit hash chaining | Phase 2 |
| GPU metrics (DCGM Exporter) | Phase 2 |
| Multiple replicas / HA | Phase 2 |

**POC uses:** Plain HTTP between services, static API key auth, rule-based security checks, SQLite audit store, single-instance Redis, Ollama inference, in-process caching fallback, and JSON-to-stdout logging.

**The IMF structure and Helm chart conventions remain the same** — POC code uses the same IMF JSON schema so production upgrades are additive, not breaking.

---

## Purpose

This steering file is the binding contract for all parallel development across the Enterprise On-Premises LLM Platform. Every layer team MUST follow this contract so that layers integrate cleanly without rework.

Reference document: `#[[file:enterprise_onprem_LLM_platform_framework.md]]`

---

## Platform Overview

The platform is a Kubernetes-native, production-grade AI governance and operations stack. It is NOT a simple LLM proxy. It is a full enterprise AI platform with:

- Secure, governed LLM inference
- Zero-trust security across all layers
- Full observability and compliance audit trail
- Intelligent model routing and lifecycle management
- Agentic workload support

---

## Non-Negotiable Principles (All Layers Must Follow)

### 1. Zero-Trust
- Every inter-service call must use mTLS (Istio service mesh).
- No implicit trust. Every component validates caller identity.
- Short-lived JWTs (15-minute expiry). Automatic OIDC refresh.
- Network policies restrict pod-to-pod traffic to declared routes only.

### 2. API-First
- All layer capabilities are exposed as versioned REST APIs.
- OpenAI-compatible API contract at the consumer surface (`/v1/chat/completions`, `/v1/embeddings`).
- Internal inter-layer APIs use the **Internal Message Format** (see below).
- API schemas are defined in OpenAPI 3.x and committed to the repo before implementation.

### 3. Kubernetes-Native
- All components are containerized (Docker images, no host dependencies).
- All deployments are Helm charts under `llm-platform/charts/<layer>/`.
- Horizontal Pod Autoscaling (HPA) must be defined for every stateless service.
- GPU workloads use `nvidia.com/gpu` resource limits and GPU node taints.

### 4. Immutable Audit
- Every request/response pair MUST be written to the audit store (pre- and post-processing).
- Audit records are append-only, never updated or deleted in normal operation.
- Audit record schema is fixed (see Shared Data Contracts below).

### 5. Pluggable Backends
- Inference engines (vLLM, Ollama, TGI, Triton) are interchangeable behind a common interface.
- No layer above the Inference Layer may depend on a specific inference engine implementation.
- Cache, routing, and governance layers operate on the **Internal Message Format**, not engine-specific formats.

### 6. Defense-in-Depth
- Security controls are applied independently at network, application, and model layers.
- A bypass of one control must NOT compromise the whole system.
- Each layer does its own input validation; do not assume an upstream layer has sanitized input.

---

## Canonical Layer Order (Request Flow)

```
Consumer → [1] API Gateway → [2] Security & Governance → [3] Intelligent Router
        → [4a] Cache (lookup) → [4b] Inference → [4c] Agent Framework
        → [5] Post-Generation Governance → [6] Cache (write) → [7] Audit (post)
        → Consumer
```

**Platform Services** (Model Registry, Observability, Admin Portal) are cross-cutting and serve all layers.

---

## Internal Message Format (IMF)

All layers communicate using this canonical structure. No layer may add proprietary fields outside the `metadata` and `extensions` envelopes.

```json
{
  "request_id": "uuid-v4",
  "trace_id": "otel-trace-id",
  "span_id": "otel-span-id",
  "timestamp_utc": "ISO-8601",
  "user": {
    "user_id": "string",
    "department": "string",
    "roles": ["string"],
    "auth_method": "oidc | ldap | api_key | mtls"
  },
  "request": {
    "model": "string | null",
    "task_type": "chat | code | reasoning | summarization | translation | vision | embeddings | null",
    "messages": [{"role": "system|user|assistant", "content": "string"}],
    "stream": false,
    "max_tokens": 2048,
    "temperature": 0.7
  },
  "governance": {
    "pii_masked": false,
    "pii_fields_detected": [],
    "injection_score": 0.0,
    "jailbreak_score": 0.0,
    "content_safety_passed": true,
    "human_approval_required": false,
    "human_approval_status": "not_required | pending | approved | rejected",
    "policy_decisions": []
  },
  "routing": {
    "selected_model": "string | null",
    "routing_mode": "auto | pinned | policy | experiment",
    "fallback_level": 0
  },
  "cache": {
    "lookup_hit": false,
    "cache_key": "string | null"
  },
  "response": {
    "content": "string | null",
    "finish_reason": "stop | length | tool_call | null",
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "total_tokens": 0
    }
  },
  "metadata": {},
  "extensions": {}
}
```

---

## Audit Record Schema (Mandatory)

Every layer that modifies the request or response MUST append to the audit record.

```json
{
  "audit_id": "uuid-v4",
  "request_id": "uuid-v4 (FK to IMF)",
  "timestamp_utc": "ISO-8601",
  "user_id": "string",
  "department": "string",
  "model_used": "string",
  "layer": "api_gateway | security | router | cache | inference | agent | governance | platform",
  "event_type": "request_received | auth_pass | auth_fail | rate_limited | security_block | policy_deny | cache_hit | inference_start | inference_complete | governance_flag | response_sent",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "latency_ms": 0,
  "pii_actions": [],
  "policy_decisions": [],
  "outcome": "pass | block | flag | fallback",
  "error_code": "string | null"
}
```

---

## Helm Chart Conventions

All layers follow this Helm chart layout:

```
llm-platform/charts/<layer-name>/
├── Chart.yaml             # name, version, appVersion, dependencies
├── values.yaml            # default config; all secrets via Vault references
├── templates/
│   ├── deployment.yaml    # or StatefulSet for stateful services
│   ├── service.yaml
│   ├── hpa.yaml           # required for all stateless services
│   ├── networkpolicy.yaml # restrict ingress/egress to declared routes
│   ├── servicemonitor.yaml # Prometheus ServiceMonitor
│   └── _helpers.tpl
└── README.md              # layer description, values reference
```

**Mandatory values.yaml fields for every chart:**

```yaml
replicaCount: 2
image:
  repository: registry.internal/<layer>
  tag: ""  # always set via CI; never hardcode latest
  pullPolicy: IfNotPresent
resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "2"
    memory: "4Gi"
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
vault:
  enabled: true
  role: "<layer>-role"
  secretPath: "secret/llm-platform/<layer>"
observability:
  tracing:
    enabled: true
    endpoint: "http://otel-collector:4317"
  metrics:
    enabled: true
    port: 9090
```

---

## Service Mesh and Networking Rules

- All inter-layer communication goes through **Istio sidecar proxies**.
- Every service MUST have a corresponding `VirtualService` and `DestinationRule`.
- **Declared allowed routes only:**

| Source Layer | Destination Layer | Protocol |
|---|---|---|
| Consumer (external) | API Gateway | HTTPS/443 |
| API Gateway | Security & Governance | gRPC/mTLS |
| Security & Governance | Router | gRPC/mTLS |
| Router | Cache | gRPC/mTLS |
| Router | Inference | gRPC/mTLS |
| Router | Agent Framework | gRPC/mTLS |
| Agent Framework | Inference | gRPC/mTLS |
| All layers | Observability (OTel) | gRPC/4317 |
| All layers | Audit Store | gRPC/mTLS |
| All layers | Vault | HTTPS/8200 |
| Platform Services | All layers (health/metrics) | HTTP/9090 |

---

## Observability Contract (All Layers)

Every layer MUST emit:

1. **Traces** — OpenTelemetry spans with `request_id` and `trace_id` propagated in headers.
2. **Metrics** — Expose `/metrics` on port `9090` for Prometheus scraping.
3. **Structured Logs** — JSON format, log level configurable via env var `LOG_LEVEL`.

**Mandatory span attributes:**
```
llm.request_id, llm.user_id, llm.department, llm.layer, llm.model,
llm.task_type, http.status_code, llm.latency_ms
```

**Mandatory Prometheus metrics per layer:**
```
llm_<layer>_requests_total{status, department, model}
llm_<layer>_latency_seconds{quantile, department}
llm_<layer>_errors_total{error_code, department}
```

---

## Secret Management Rules

- **No hardcoded secrets** anywhere in code or Helm values.
- All secrets injected at runtime via **HashiCorp Vault Agent** sidecar.
- Each layer has its own Vault role scoped to minimum required secrets.
- Secret rotation must not require pod restarts (use dynamic secrets with lease renewal).

---

## Layer Summary Reference

| Layer | Helm Chart | Primary Tech | Port |
|---|---|---|---|
| API Gateway | `api-gateway` | Kong / NGINX | 443, 8080 |
| Security & Governance | `security-layer` | OPA, Presidio, LlamaGuard | 8081 |
| Intelligent Router | `router` | Custom Go/Python service | 8082 |
| Cache | `cache` | Redis + Milvus/Qdrant | 6379, 19530 |
| Inference (vLLM) | `inference-vllm` | vLLM | 8000 |
| Inference (Ollama) | `inference-ollama` | Ollama | 11434 |
| Agent Framework | `agent-framework` | LangGraph / Temporal | 8083 |
| Model Registry | `model-registry` | MLflow | 5000 |
| Observability | `observability` | Prometheus/Grafana/Jaeger/OTel | 3000, 9090, 16686 |
| Audit Store | `audit-store` | Elasticsearch / ClickHouse | 9200 |
| Admin Portal | `admin-portal` | React + Admin API | 8084 |
| Developer Portal | `developer-portal` | OpenAPI docs, Playground | 8085 |

---

## Integration Testing Requirements

After each layer is developed independently, integration MUST be validated by:

1. Passing a request through the full 13-step request flow (section 4.1 of the framework).
2. Verifying the audit record contains entries from every layer.
3. Verifying the OTel trace spans all layers as a single distributed trace.
4. Verifying a blocked request (injection detected) returns `400` and is fully audited.
5. Verifying a cache hit skips the inference layer and is logged correctly.

---

## Questions to Resolve Before Starting Each Layer

Before beginning development of any layer, confirm:

- [ ] OpenAPI schema for this layer's endpoints is defined and reviewed.
- [ ] IMF fields this layer reads and writes are documented.
- [ ] Vault secret paths for this layer are allocated.
- [ ] Prometheus metrics names for this layer are registered in the shared metrics registry.
- [ ] Istio `NetworkPolicy` and `VirtualService` routes are declared.
