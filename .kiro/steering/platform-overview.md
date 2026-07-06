---
inclusion: always
---

# Enterprise On-Prem LLM Platform — POC Overview

> **Phase:** Local-first POC → then K8s. Get everything running locally before touching Helm/K8s.
> **Model:** `llama3.2:3b` — this is the Ollama colon notation used **everywhere** (model_matrix.yaml keys, router config, inference adapter, API payloads, test fixtures). Never use `llama3.2-3b` (dash) as a model identifier.

---

## Architecture — Request Flow

```
curl / Browser
  → [1] API Gateway          :8080   (auth X-Api-Key, rate limit)
  → [2] Security Layer       :8081   (injection scan, content safety, PII mask, policy)
  → [3] Intelligent Router   :8082   (task classify, model select, health check)
  → [4] Cache Service        :8086   ←→ Redis :6379  (exact + semantic lookup)
  → [5] Inference Adapter    :8087   (thin Ollama HTTP wrapper)
  → [6] Ollama               :11434  (llama3.2:3b, CPU inference)
  ↑ response propagates back up through the same chain
  → Audit Store              :9200   (async fire-and-forget from every layer)
  → Model Registry           :5001   (local) / :5000 (container)
  → Admin Portal API         :8084   (admin, audit viewer, playground)
  → Portal UI                :5173   (React/Vite, dev only)
```

All layers communicate via **Internal Message Format (IMF)** — a shared JSON envelope. The entry point for the consumer is OpenAI-compatible: `POST /v1/chat/completions` with `X-Api-Key` header.

---

## Services at a Glance

| Service | Folder | Port | Depends On |
|---|---|---|---|
| API Gateway | `api_gateway/` | 8080 | Security Layer :8081 |
| Security Layer | `security_layer/` | 8081 | Router :8082, Audit Store :9200 |
| Intelligent Router | `intelligent_router/` | 8082 | Cache :8086, Inference Adapter :8087, Audit Store :9200 |
| Cache Service | `cache_service/` | 8086 | Redis :6379 |
| Inference Adapter | `inference_adapter/` | 8087 | Ollama :11434 |
| Agent Framework | `services/agent-framework/` | 8083 | Router :8082 |
| Audit Store | `audit_store/` | 9200 | — (leaf, SQLite) |
| Model Registry | `model_registry/` | 5001 (local) | — (leaf, JSON file) |
| Admin Portal API | `admin_portal/` | 8084 | Gateway, Audit Store, Model Registry, Prometheus |
| Portal UI | `portal_ui/` | 5173 | Admin Portal :8084 |

Infrastructure (not Python services):
- **Redis** — `docker compose -f docker-compose.local.yml up -d` → `redis:7-alpine` on :6379
- **Ollama** — `ollama serve` (separate terminal) → `ollama pull llama3.2:3b`

---

## Running Locally

### One-time setup
```powershell
# 1. Python dependencies (run from repo root, in your venv)
pip install -r requirements.txt

# 2. Download spaCy model (needed by security_layer for PII detection)
python -m spacy download en_core_web_sm

# 3. Start Redis
docker compose -f docker-compose.local.yml up -d

# 4. Start Ollama (separate terminal, keep running)
ollama serve
ollama pull llama3.2:3b
```

### Start / stop all services
```powershell
# Start all 7 Python services (each opens in its own terminal window)
.\scripts\run-local.ps1

# Start a single service
.\scripts\run-local.ps1 -Service intelligent_router

# Stop all
.\scripts\run-local.ps1 -Stop
```

Service startup order (run-local.ps1 handles this automatically):
`model_registry → audit_store → inference_adapter → cache_service → intelligent_router → agent_framework → security_layer → api_gateway → admin_portal`

### Verify the stack
```powershell
# Health check each service
curl http://localhost:8080/health         # API Gateway
curl http://localhost:8081/health         # Security Layer
curl http://localhost:8082/health         # Router
curl http://localhost:8083/health         # Agent Framework
curl http://localhost:8084/portal/health  # Admin Portal
curl http://localhost:8086/health         # Cache
curl http://localhost:8087/health         # Inference Adapter
curl http://localhost:9200/health         # Audit Store
curl http://localhost:5001/health         # Model Registry

# End-to-end test
curl -X POST http://localhost:8080/v1/chat/completions `
  -H "X-Api-Key: poc-secret-key" `
  -H "Content-Type: application/json" `
  -d "{\"messages\": [{\"role\": \"user\", \"content\": \"What is Kubernetes in 2 sentences?\"}]}"
```

---

## Environment Variables (`local.env`)

All services load from `local.env` via `run-local.ps1`. Key values:

| Variable | Value | Used By |
|---|---|---|
| `GATEWAY_API_KEY` | `poc-secret-key` | API Gateway (required) |
| `AUDIT_API_KEY` | `poc-audit-key` | Security, Router, Audit Store |
| `DOWNSTREAM_SECURITY_URL` | `http://localhost:8081` | API Gateway |
| `DOWNSTREAM_ROUTER_URL` | `http://localhost:8082` | Security Layer |
| `AUDIT_STORE_URL` | `http://localhost:9200` | Security, Router |
| `MODEL_MATRIX_PATH` | `model_matrix.yaml` | Router (relative to cwd) |
| `TASK_RULES_PATH` | `task_classifier_rules.yaml` | Router |
| `CACHE_URL` | `http://localhost:8086` | Router |
| `INFERENCE_ADAPTER_URL` | `http://localhost:8087` | Router |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Inference Adapter |
| `DEFAULT_MODEL` | `llama3.2:3b` | Inference Adapter |
| `REDIS_URL` | `redis://localhost:6379` | Cache Service |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Cache Service |
| `STORAGE_PATH` | `./models.json` | Model Registry |
| `REGISTRY_API_KEY` | `poc-registry-key` | Model Registry |
| `ROUTER_URL` | `http://localhost:8082` | Agent Framework |
| `TOOL_CATALOG_PATH` | `services/agent-framework/tools/catalog.yaml` | Agent Framework |
| `API_GATEWAY_URL` | `http://localhost:8080` | Admin Portal |
| `MODEL_REGISTRY_URL` | `http://localhost:5001` | Admin Portal |

---

## Key Config Files (root of repo)

| File | Purpose |
|---|---|
| `local.env` | All local dev env vars |
| `docker-compose.local.yml` | Starts Redis only |
| `model_matrix.yaml` | Router's model registry — model key must be `llama3.2:3b` (colon) |
| `task_classifier_rules.yaml` | Keyword rules for task classification |
| `injection_patterns.yaml` | Prompt injection detection patterns |
| `seed/models.json` | Seed data for model registry |
| `models.json` | Live model registry data file |

---

## Code Conventions

### IMF (Internal Message Format)
Every layer reads and writes the same IMF dict. Key blocks:
- `request` — messages, model, task_type, stream, max_tokens, temperature
- `governance` — injection_score, content_safety_passed, pii_masked, pii_fields_detected, policy_decisions
- `routing` — selected_model, routing_mode, fallback_level
- `cache` — lookup_hit, cache_key
- `response` — content, finish_reason, usage

### Model name
- **Always `llama3.2:3b`** (Ollama colon notation) in all code, config, YAML keys, test fixtures, and API payloads
- The model registry uses `llama3.2:3b` as its `name` field
- The model_matrix.yaml YAML key must be quoted: `"llama3.2:3b":`

### Security pipeline (Security Layer — 4 stages, strict order)
1. Injection scan → sets `governance.injection_score`; blocks 400 if `1.0`
2. Content safety → sets `governance.content_safety_passed`; blocks 400 if false
3. PII masking → masks `request.messages`; sets `governance.pii_masked`
4. Policy check → checks `user.roles` against `{developer, analyst, admin}`; blocks 403 if denied

### Router pipeline (6 stages)
1. Governance gate — blocks if `content_safety_passed` false
2. Task classification — overwrites `request.task_type`
3. Model selection — raises 422 (invalid pinned) or 503 (no model for task)
4. Health check — falls back or exhausts chain → 503
5. Cache lookup — returns 200 on hit; treats empty content as miss
6. Inference dispatch + cache write + audit (fire-and-forget)

### Audit events
Fire-and-forget background tasks from every layer. Failure is logged as WARNING but never blocks the caller. Written to `audit_store` at `/audit/events`.

### Metrics
Every service exposes Prometheus metrics:
- API Gateway, Router, Inference Adapter: `/metrics` on main port
- Cache Service: separate metrics server on `:9091`
- Metric naming: `llm_<layer>_requests_total`, `llm_<layer>_latency_seconds`, `llm_<layer>_errors_total`

### Logging
All services use structured JSON logs (structlog) to stdout. Log level from `LOG_LEVEL` env var.

---

## POC Constraints (Phase 2 deferred)

The following are **not implemented** in POC and must not be added:
- Istio mTLS / service mesh (plain HTTP between services for now)
- HashiCorp Vault (static API keys in local.env)
- OPA / Rego policy engine (static role check only)
- Redis HA / Sentinel / Cluster (single instance)
- OIDC / LDAP / SSO (API key auth only)
- ML classifiers for injection/jailbreak (pattern matching only)
- LlamaGuard content moderation
- Milvus / Qdrant vector DB (sentence-transformers + Redis for semantic cache)
- MLflow model registry (JSON file store)
- GPU metrics / vLLM (Ollama on CPU only)
- Multiple replicas / HA

---

## K8s (after local is working)

Scripts in `scripts/`:
```powershell
.\scripts\build-all-images.ps1          # build Docker images
.\scripts\load-images-to-k8s.ps1       # load into local K8s
.\scripts\deploy-to-k8s.ps1            # helm install llm-poc
.\scripts\deploy-to-k8s.ps1 -Uninstall # tear down
```

Helm charts in `llm-platform/charts/`: `api-gateway`, `security-layer`, `router`, `cache`, `inference-ollama`, `agent-framework`, `audit-store`, `model-registry`, `admin-portal`, `observability`.

K8s model_matrix uses `"llama3.2:3b"` (quoted) as the YAML key in `llm-platform/charts/router/templates/configmap.yaml`.

---

## Old Steering Files (manual inclusion only)

The detailed per-layer steering files (`01-layer-api-gateway.md` through `11-deployment-kubernetes.md` and `00-platform-master-contract.md`) are set to `inclusion: manual`. Reference them in chat with `#` only when working on a specific layer.
