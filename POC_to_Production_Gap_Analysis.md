# Enterprise On-Prem LLM Platform — POC to Production Gap Analysis

**Document Purpose:** Explain to leadership what has been built in the POC, what is intentionally simplified, and what engineering work is required to reach production-grade quality.

**Author:** For review with Technical Lead / Architecture Team
**Date:** June 2026
**Platform Version:** 1.0 (POC Phase)

---

## Executive Summary

We have built a **fully functional, end-to-end Proof-of-Concept** of the Enterprise On-Premises LLM Platform on Kubernetes. Every architectural layer described in the framework document exists, communicates correctly, and a request flows through the complete governance pipeline.

The POC proves the architecture works. It is **not** production-ready. This document explains exactly what was simplified for speed, and what needs to be hardened before we go live.

**Bottom line:** The POC covers roughly 35–40% of the production engineering effort. The remaining 60–65% is hardening — security, resilience, compliance, and operational tooling — not new architecture.

---

## Platform Layer Status Overview

| Layer | POC Status | Production Readiness | Effort to Productionize |
|---|---|---|---|
| 1. API Gateway | ✅ Working | 🔶 Partial | Medium |
| 2. Security & Governance | ✅ Working (simplified) | 🔴 Low | High |
| 3. Intelligent Router | ✅ Working | 🔶 Partial | Medium |
| 4. Cache Layer | ✅ Working (lightweight) | 🔶 Partial | Medium |
| 5. Inference Layer | ✅ Working (Ollama) | 🔶 Partial | Medium–High |
| 6. Agent Framework | ✅ Working (basic) | 🔶 Partial | Medium |
| 7. Model Lifecycle | ✅ Working (minimal) | 🔴 Low | High |
| 8. Observability | ✅ Working (Prometheus+Grafana) | 🔶 Partial | Medium |
| 9. Audit Store | ✅ Working (SQLite) | 🔴 Low | High |
| 10. Platform Portals | ✅ Working (combined UI) | 🔶 Partial | Medium |
| 11. Kubernetes Deployment | ✅ Working (single cluster) | 🔶 Partial | High |

**Legend:** ✅ Done for POC &nbsp;|&nbsp; 🔶 Needs significant hardening &nbsp;|&nbsp; 🔴 Needs near-complete rebuild for production

---

## Layer-by-Layer Breakdown

---

### Layer 1 — API Gateway

#### What We Built (POC)
- Single ingress point accepting OpenAI-compatible API (`POST /v1/chat/completions`)
- Static API key authentication via `X-Api-Key` header
- In-memory rate limiting (60 req/min per key)
- Request normalisation into the Internal Message Format (IMF)
- Response serialisation back to OpenAI schema
- HTTP (no TLS) via NGINX Ingress on Kubernetes
- Streaming support via Server-Sent Events

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| TLS/HTTPS termination | All enterprise traffic must be encrypted in transit | Low — cert-manager + Let's Encrypt or internal CA |
| OIDC / OAuth2 / SAML authentication | SSO with corporate identity (Azure AD, Okta, Keycloak) | Medium |
| LDAP / Active Directory bind | User directory integration for employee accounts | Medium |
| Redis-backed rate limiting | In-memory counters don't survive pod restarts and don't scale across replicas | Low |
| WAF rules (OWASP CRS) | Protect against web-layer attacks before they hit the platform | Medium |
| Kong Gateway or enterprise proxy | Plugin ecosystem, advanced routing, API versioning | Medium–High |
| mTLS for downstream calls | Encrypted and mutually authenticated calls to Security layer | High (requires Istio) |
| JWT expiry validation + refresh | Short-lived tokens (15 min) with automatic OIDC refresh | Medium |
| Request body size enforcement | Prevent abuse via oversized payloads | Low |
| Multiple replicas + HPA | Handle production traffic load | Low |

---

### Layer 2 — Security and Governance

> **This is the highest-risk layer for production.** The POC proves the pipeline shape but the actual security enforcement is minimal.

#### What We Built (POC)
- Keyword/regex-based prompt injection detection (pattern list)
- Simple blocklist content safety filter
- Microsoft Presidio PII detection (EMAIL, PHONE, PERSON) with masking
- Basic role check (must have `developer` role)
- Stdout-based audit logging

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| Open Policy Agent (OPA) with Rego policies | Centralised, auditable RBAC + ABAC enforcement; department-level model restrictions | High |
| Fine-tuned ML injection classifier | Regex misses novel injection patterns; ML catches semantic attacks | High |
| Fine-tuned jailbreak classifier | Multi-classifier ensemble with configurable sensitivity per department | High |
| LlamaGuard (self-hosted) content moderation | On-prem hate/violence/adult content screening; no cloud dependency | High |
| Full Presidio entity coverage | Add SSN, credit card, medical record ID, IP address, custom enterprise terms | Medium |
| Per-department PII handling modes | HR gets `block` mode; Finance gets `pseudonymize`; not just global `mask` | Medium |
| Hallucination detector (post-gen) | For RAG use cases, verify factual claims against source documents | High |
| Human approval workflow | High-risk requests queued for human review before response is sent | High |
| IP allowlist enforcement | Restrict which internal networks can call the platform | Low |
| gRPC transport with mTLS | Encrypted internal service calls; replaces plain HTTP | High (requires Istio) |
| Elasticsearch audit storage | Searchable, indexed, durable audit store instead of stdout | High |

---

### Layer 3 — Intelligent Router

#### What We Built (POC)
- Keyword-based task classifier (chat, code, reasoning, summarization, translation)
- Static YAML capability matrix (model → tasks)
- HTTP health check before routing
- Simple primary → fallback model cascade
- Cache lookup before inference dispatch
- `auto` and `pinned` routing modes

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| ML-based task classifier (fastText or small LLM) | Keyword matching fails on ambiguous or mixed-type requests | Medium |
| Prometheus GPU availability probe | Route away from saturated GPU nodes in real time | Medium |
| Cost/latency composite scoring | Rank models by p95 latency + cost + GPU headroom dynamically | High |
| OPA routing policy queries | Department-level model restrictions enforced at routing | Medium (requires OPA) |
| Circuit breaker with Redis state | Shared state across router replicas; exponential backoff | Medium |
| A/B testing engine | Split traffic between model versions for quality comparison | High |
| MLflow integration for capability matrix | Pull model capabilities from the registry dynamically vs static YAML | Medium |
| Weighted load balancing across inference replicas | Distribute across multiple pods of the same model | Medium |
| `policy` and `experiment` routing modes | Support department overrides and A/B experiments | Medium |

---

### Layer 4 — Cache Layer

#### What We Built (POC)
- Exact-match cache using Redis (SHA256 key, TTL per task type)
- Semantic cache using `sentence-transformers` (all-MiniLM-L6-v2) with linear cosine scan stored in Redis lists
- Cache write after inference miss
- Department-agnostic (shared cache for all users)

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| Milvus or Qdrant vector database | Linear scan over Redis lists breaks beyond ~500 entries; need proper ANN index for scale | High |
| BGE-M3 or E5-Mistral embedding model | Higher-quality embeddings = better semantic match accuracy | Medium |
| Redis Sentinel or Cluster mode | Single Redis pod is a single point of failure | Medium |
| Department-namespaced cache isolation | Finance cache must not serve HR users; data segregation requirement | Medium |
| Model-version event-driven invalidation | When a model is updated, its cached responses are stale and must be purged | Medium |
| Explicit cache invalidation API | Allow admins to manually flush cache entries | Low |
| Embedding cache for document chunks | Avoid recomputing embeddings for RAG document retrieval | Medium |
| KV cache prefix sharing hints with vLLM | Route requests with shared system prompts to the same vLLM instance | High |
| TLS on Redis connections | Encrypted cache traffic in a zero-trust environment | Medium |

---

### Layer 5 — Inference Layer

#### What We Built (POC)
- Ollama deployed on Kubernetes with persistent model storage
- Models: llama3:8b, mistral:7b, deepseek-coder:6.7b (CPU-capable)
- IMF-to-Ollama adapter for request/response translation
- Basic health check via Ollama `/api/tags`
- Optional vLLM deployment spec (GPU-only)

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| vLLM as primary production backend | PagedAttention + continuous batching = 5–10x throughput vs Ollama for concurrent users | High |
| Multi-GPU tensor parallelism | Required for 70B+ parameter models | High |
| NVIDIA GPU Operator + DCGM Exporter | GPU metrics, driver management, time-slicing for small models | Medium |
| NFS shared model storage (ReadWriteMany) | All inference pods read the same weights; avoid downloading per pod | Medium |
| TGI / Triton backend support | HuggingFace production models; NVIDIA-optimised throughput | High |
| gRPC inference API contract | Standardised interface so Router never calls backend-specific APIs | High |
| MIG (Multi-Instance GPU) configuration | Share A100/H100 across smaller models efficiently | Medium |
| Multiple inference replicas per model | High availability; Router load-balances across them | Medium |
| Model weight versioning and sync | When a new model version is registered, sync weights to NFS | High |

---

### Layer 6 — Agent Framework

#### What We Built (POC)
- LangGraph ReAct agent loop (max 10 steps)
- 3 tools: web_search (mocked), calculator, get_current_time
- In-process session memory (Python dict, lost on restart)
- Every agent model call routes back through the full governance pipeline

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| Redis-backed short-term session memory | Survive pod restarts; share state across agent replicas | Low |
| Milvus long-term semantic memory | Persist significant outcomes across sessions; user-scoped | High |
| MCP (Model Context Protocol) server integration | Connect enterprise knowledge bases, databases, and APIs as agent context | High |
| Temporal or Argo Workflows engine | Durable execution for long-running multi-step pipelines; resume after failure | High |
| Tool sandboxing (isolated containers) | Execute untrusted tool code in a container sandbox with resource limits | High |
| OPA tool permission checks | Enforce which roles can call which tools | Medium (requires OPA) |
| Real web search tool (not mocked) | Actual enterprise intranet or approved external search | Medium |
| Enterprise tool integrations | SQL query runner, file reader, internal API caller with proper auth | High |
| Multi-agent coordination | Multiple specialised agents collaborating on a task | High |
| Max step limit enforcement at infrastructure level | Not just application-level; prevent runaway costs | Medium |

---

### Layer 7 — Model Lifecycle Management

> **The POC only scratches the surface here.** Production model lifecycle is a significant engineering investment.

#### What We Built (POC)
- FastAPI + JSON file model registry
- CRUD API for model metadata
- Router polls registry every 60 seconds for capability matrix
- Manual status updates via API

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| MLflow tracking server | Industry-standard model registry with experiment tracking, artifact store, evaluation scores | Medium |
| MinIO / NFS model weight storage | Centralised, versioned storage for model weights, LoRA adapters, quantized variants | High |
| Canary deployment via Argo Rollouts | Gradual traffic shift (5% → 25% → 50% → 100%) with automated SLA-based rollback | High |
| Automated benchmark jobs (CronJob) | Nightly accuracy, latency, and toxicity regression tests | High |
| A/B testing configuration | Route % of traffic to a challenger model; track quality metrics | High |
| Kubernetes Operator for auto-deployment | Watch registry for new model versions → trigger rolling redeployments | High |
| Semantic versioning enforcement | Track breaking vs non-breaking model updates | Low |
| Cache invalidation event publishing | Notify cache layer when a model version changes | Medium |
| LoRA adapter management | Track and deploy fine-tuned adapters per use case | High |

---

### Layer 8 — Observability Stack

#### What We Built (POC)
- Prometheus scraping `/metrics` from all services
- Grafana with 1 POC overview dashboard
- Structured JSON logs to stdout on all services
- `prometheus_client` counters and histograms in each layer

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| OpenTelemetry distributed tracing | Single trace spanning all layers for debugging and SLA measurement | High |
| Jaeger / Grafana Tempo trace UI | Visualise end-to-end request traces | Medium |
| Elasticsearch + Kibana log aggregation | Search and analyse logs across all pods; not just per-pod `kubectl logs` | High |
| Fluent Bit DaemonSet log shipper | Collect stdout from all pods and forward to Elasticsearch | Medium |
| DCGM Exporter for GPU metrics | GPU utilisation, VRAM, power consumption per model per node | Medium |
| Cost attribution dashboard | Token usage × cost unit per user, department, model; charge-back reporting | High |
| Security events dashboard | Injection detections, PII masks, policy denials over time | Medium |
| Alertmanager with PagerDuty / Slack | Production on-call alerting for error spikes, GPU OOM, SLA breach | Medium |
| OTel sensitive data filter | Strip raw prompt content from trace exports | Medium |
| Long-term metric retention (30+ days) | 7-day POC retention is insufficient for trend analysis | Low |
| Model performance dashboard | Latency, throughput, error rate, eval scores per model | Medium |

---

### Layer 9 — Audit Store

> **The POC audit store is for demonstration only.** It is not tamper-evident and does not meet any compliance standard.

#### What We Built (POC)
- FastAPI + SQLite audit store
- Append-only by convention (no enforcement)
- Query API (by request_id, user_id, time range)
- All layers write audit events asynchronously

#### What Needs to Be Added for Production

| Gap | Why It Matters | Compliance Requirement | Effort |
|---|---|---|---|
| Elasticsearch or ClickHouse backend | SQLite cannot handle production write volume or complex compliance queries | SOC 2, HIPAA | High |
| Cryptographic hash chaining | Detect any tampering with historical records | SOC 2, ISO 27001 | High |
| S3 / MinIO Object Lock (COMPLIANCE mode) | Legally immutable backup; tamper-proof long-term storage | HIPAA, GDPR, PCI | High |
| Index Lifecycle Management (ILM) | Automatic warm/cold/delete tiering for 7-year retention | HIPAA, SOC 2 | Medium |
| GDPR right-to-erasure API | Pseudonymize user data on request without breaking audit chain | GDPR | High |
| Role-based access (auditor/compliance roles) | Not everyone should query the full audit trail | SOC 2 | Medium |
| Compliance report templates | Pre-built SOC 2, HIPAA, GDPR, PCI report exports | All | High |
| Audit integrity verification API | Programmatically verify hash chain is unbroken over a time range | ISO 27001 | Medium |
| Encrypted audit record storage | PII in audit records must be encrypted at rest | GDPR, HIPAA | Medium |
| 7-year default retention policy | Regulatory minimum for financial and healthcare sectors | HIPAA, SOC 2 | Low |

---

### Layer 10 — Platform Portals

#### What We Built (POC)
- Single combined portal (admin + developer merged)
- Playground: send chat requests and see responses
- Audit viewer: browse recent audit events
- Model viewer: see registered models, activate/retire them
- Grafana embedded via iframe
- No authentication

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| OIDC authentication (Keycloak) | Portal must require login; no open access | High |
| Role separation (admin vs developer vs auditor vs compliance) | Different users see different features | Medium |
| Human review queue UI | Approvers need a UI to approve/reject/modify high-risk requests | High |
| API key management UI | Developers self-serve their own API keys | Medium |
| User and department management | Admins manage roles, departments, routing policies | High |
| Compliance report generation UI | Trigger SOC 2 / HIPAA / GDPR reports on demand | High |
| Prompt template library | Browse and use approved prompt templates | Medium |
| Evaluation framework UI | Submit and view model evaluation runs | High |
| Separate Admin and Developer portal deployments | Different security boundaries; admin portal not exposed to developers | Medium |
| Cost attribution UI | Per-user and per-department spend dashboards | Medium |

---

### Layer 11 — Kubernetes Deployment

#### What We Built (POC)
- Single namespace (`llm-poc`), all services
- Plain HTTP between services (no TLS)
- Single replica per service
- Static secrets via Kubernetes Secrets
- `helm install` deployment (manual)
- k3s / kind / minikube compatible
- NGINX Ingress (HTTP only)

#### What Needs to Be Added for Production

| Gap | Why It Matters | Effort |
|---|---|---|
| Istio service mesh | mTLS between all services; zero-trust network | High |
| Istio AuthorizationPolicies | Declare which service can call which; deny-by-default | High |
| HashiCorp Vault + Vault Agent Injector | No hardcoded or Kubernetes-Secret-stored credentials; dynamic secrets | High |
| Argo CD (GitOps) | All deployments via Git; no manual `helm install` in production | Medium |
| Argo Rollouts | Canary deployments for all services, not just models | Medium |
| Horizontal Pod Autoscaling (HPA) | Scale services under load; required for production SLAs | Low |
| Multiple node pools (CPU / GPU / Observability) | Isolate GPU workloads; prevent noisy-neighbour problems | Medium |
| Dedicated namespaces per concern | `llm-platform`, `llm-inference`, `llm-observability`, `llm-storage` | Low |
| Kubernetes NetworkPolicies | Pod-level traffic restrictions complementing Istio | Medium |
| OPA Gatekeeper (admission controller) | Enforce platform standards on every resource deployed | High |
| ResourceQuotas + LimitRanges per namespace | Prevent runaway resource consumption | Low |
| Production TLS with internal CA (cert-manager) | All ingress traffic encrypted; internal CA for service certs | Medium |
| NVIDIA GPU Operator | Production GPU driver management, time-slicing, MIG | Medium |
| Rook-Ceph / Longhorn distributed storage | Production-grade PersistentVolumes with replication | High |
| Multi-master control plane (3x etcd HA) | Single control plane is a single point of failure | High |
| Disaster recovery and backup strategy | etcd backups, volume snapshots, runbook | High |

---

## Cross-Cutting Production Requirements

These span all layers and are not tied to a single component.

| Requirement | Current POC State | Production Requirement | Effort |
|---|---|---|---|
| **Secret Management** | Env vars / K8s Secrets | HashiCorp Vault with dynamic secrets, lease rotation, per-layer policies | High |
| **Authentication** | Static API key | OIDC + LDAP + API Key + mTLS; short-lived JWTs (15-min expiry) | High |
| **Encryption in Transit** | Plain HTTP | TLS 1.3 everywhere; mTLS between internal services (Istio) | High |
| **Encryption at Rest** | None | AES-256 via Vault/KMS for PVCs, audit store, model weights, cache | High |
| **High Availability** | Single replicas | min 2–3 replicas for all stateless services; HA for stateful services | Medium |
| **Compliance Controls** | None enforced | GDPR, HIPAA, SOC 2, ISO 27001, PCI-DSS controls per layer | High |
| **Incident Response** | None | Alerting, runbooks, on-call rotation, automated remediation | High |
| **Change Management** | Manual | GitOps (Argo CD) with PR-based change approval | Medium |
| **Penetration Testing** | Not done | Required before production go-live | High |
| **Load Testing** | Not done | Baseline capacity planning; SLA validation | Medium |
| **Documentation** | Steering files | OpenAPI specs, runbooks, architecture decision records (ADRs) | Medium |

---

## Recommended Production Phasing

Rather than trying to close all gaps at once, we recommend a phased approach:

### Phase 1 — Security Hardening (Weeks 1–6)
Close the highest-risk gaps before any production traffic.
- Deploy Istio + mTLS across all namespaces
- Replace static API key with Keycloak OIDC
- Deploy HashiCorp Vault; migrate all secrets
- Deploy OPA + Rego policies for RBAC/ABAC
- Replace regex injection scanner with ML classifier
- Deploy LlamaGuard for content moderation
- Replace SQLite audit store with Elasticsearch + hash chaining

### Phase 2 — Resilience and Scale (Weeks 7–12)
Make the platform handle production load without manual intervention.
- Replace Ollama with vLLM on GPU nodes for primary inference
- Add Redis Sentinel, HPA for all stateless services
- Replace in-memory semantic cache with Milvus
- Deploy Argo CD for GitOps
- Deploy Alertmanager with PagerDuty/Slack integration
- Add OTel distributed tracing + Jaeger

### Phase 3 — Compliance and Governance (Weeks 13–18)
Close compliance gaps for regulated industries.
- Full audit store: S3 Object Lock archival, ILM retention, GDPR erasure
- Compliance report templates (SOC 2, HIPAA, GDPR)
- Replace JSON file model registry with MLflow
- Canary deployment via Argo Rollouts with automated rollback
- Deploy human approval workflow + review portal
- Security penetration test

### Phase 4 — Advanced Capabilities (Weeks 19–24)
Add capabilities that differentiate the platform.
- MCP server integrations (enterprise knowledge bases, databases)
- Temporal workflow engine for durable agent execution
- A/B testing engine for model experiments
- Full cost attribution and charge-back dashboards
- Kubernetes Operator for automated model lifecycle
- Load testing and capacity planning

---

## Effort Summary

| Phase | Duration | Team Size | Primary Focus |
|---|---|---|---|
| POC (Done) | 4–6 weeks | 2–3 engineers | Architecture proof, end-to-end flow |
| Phase 1 | 6 weeks | 3–4 engineers | Security, auth, compliance foundation |
| Phase 2 | 6 weeks | 3–4 engineers | Production inference, resilience, scale |
| Phase 3 | 6 weeks | 3–4 engineers | Compliance, audit, model operations |
| Phase 4 | 6 weeks | 3–4 engineers | Advanced features, optimisation |
| **Total to Production** | **~24 weeks** | **3–4 engineers** | |

> **Note:** Phases can overlap for different layers. A dedicated team per layer (as designed for parallel development) can compress this timeline to 12–16 weeks.

---

## What the POC De-Risks

It is worth stating clearly what the POC has already validated:

1. **The layered architecture works** — requests flow through all 11 layers correctly.
2. **The IMF schema is correct** — all layers read and write the same internal message format without conflicts.
3. **Ollama runs on-prem without GPU** — we can run LLMs entirely within our data center with no cloud dependency.
4. **Governance pipeline is functional** — security checks happen before inference; PII masking works on both prompt and response.
5. **Semantic caching reduces inference calls** — cache hit demonstrated on similar prompts.
6. **Agent loop with tool calling works** — multi-step reasoning with tool execution completes correctly.
7. **Kubernetes deployment is viable** — the platform deploys reliably with Helm charts on a local cluster.
8. **Audit trail covers the full request lifecycle** — every layer writes to the audit store; a single `request_id` traces the complete journey.

These are the hard architectural questions. The production work ahead is hardening, not re-architecting.

---

*This document should be reviewed alongside the steering files in `.kiro/steering/` and the platform framework document `enterprise_onprem_LLM_platform_framework.md`.*
