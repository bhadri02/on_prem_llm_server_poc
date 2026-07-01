# Implementation Flow — Parallel Development Plan

**Platform:** Enterprise On-Prem LLM Platform POC
**Deployment Target:** Kubernetes (Docker Desktop local)
**Development Style:** Parallel tracks — multiple engineers can work simultaneously

---

## Why Parallel Development Is Possible

The platform is layered, but the dependencies only flow **downward** (consumer → gateway → security → router → inference). Services that don't depend on each other can be built simultaneously.

The key insight: **every service has a fixed HTTP contract (the IMF schema)**. As long as teams mock their upstream/downstream during development, they never block each other.

---

## Dependency Map

```
                        ┌─────────────────────────────┐
                        │   FOUNDATION (no deps)       │
                        │                              │
                        │  [A] Shared IMF Library      │
                        │  [B] Model Registry          │
                        │  [C] Audit Store             │
                        │  [D] Ollama Setup            │
                        └──────────────┬───────────────┘
                                       │  all ready
                    ┌──────────────────┼──────────────────┐
                    │                  │                   │
          ┌─────────▼──────┐  ┌────────▼───────┐  ┌──────▼──────────┐
          │ [E] Inference   │  │ [F] Cache       │  │ [G] Security    │
          │     Adapter     │  │     Layer       │  │     Layer       │
          └────────┬────────┘  └────────┬────────┘  └──────┬──────────┘
                   │                    │                   │
                   └──────────┬─────────┘                   │
                              │                             │
                     ┌────────▼────────┐                    │
                     │  [H] Router     │◄───────────────────┘
                     └────────┬────────┘
                              │
                     ┌────────▼────────┐
                     │  [I] API Gateway│
                     └────────┬────────┘
                              │
          ┌───────────────────┼──────────────────┐
          │                   │                  │
 ┌────────▼───────┐  ┌────────▼───────┐  ┌──────▼──────────┐
 │ [J] Agent      │  │ [K] Admin      │  │ [L] Observability│
 │     Framework  │  │     Portal     │  │     (Prometheus  │
 │                │  │                │  │      + Grafana)  │
 └────────────────┘  └────────────────┘  └─────────────────┘
                              │
                     ┌────────▼────────┐
                     │ [M] Helm Charts │
                     │ + Integration   │
                     │   Testing       │
                     └─────────────────┘
```

---

## Parallel Tracks Overview

| Track | Who Works On It | Services | Can Start |
|---|---|---|---|
| **Track 1 — Foundation** | 1 engineer | IMF lib, Model Registry, Audit Store, Ollama | Day 1 |
| **Track 2 — Inference** | 1 engineer | Inference Adapter | After Track 1 starts (mock Ollama) |
| **Track 3 — Cache** | 1 engineer | Cache Service + Redis | Day 1 (no upstream deps) |
| **Track 4 — Security** | 1 engineer | Security & Governance Layer | Day 1 (uses IMF lib only) |
| **Track 5 — Router** | 1 engineer | Intelligent Router | After Track 2, 3, 4 complete |
| **Track 6 — Gateway** | 1 engineer | API Gateway | After Track 5 completes |
| **Track 7 — Upper Layer** | 2 engineers | Agent Framework + Admin Portal | After Track 6 completes |
| **Track 8 — Platform** | 1 engineer | Observability + Helm Charts + K8s | Runs in parallel from Day 1 |

**Minimum team to hit 2-week POC:** 3 engineers (tracks can be combined)
**Ideal team for 1-week sprint:** 4–5 engineers

---

## Sprint Plan (2-Week POC Target)

### Week 1 — Build All Independent Services

```
Day 1–2
├── Track 1:  [A] Shared IMF + [B] Model Registry + [C] Audit Store
├── Track 3:  [F] Cache Layer
├── Track 4:  [G] Security Layer
└── Track 8:  [L] Helm base chart + Kubernetes namespace + Redis deploy

Day 3–4
├── Track 2:  [E] Inference Adapter  (Ollama must be running)
├── Track 1:  [D] Ollama setup + model pull (phi3:mini, llama3.2:3b)
├── Track 3:  Cache Layer testing with Redis
└── Track 4:  Security Layer unit tests

Day 4–5
└── Track 5:  [H] Router  (depends on Inference Adapter, Cache, Security done)
```

### Week 2 — Integration and Upper Layers

```
Day 6–7
├── Track 6:  [I] API Gateway  (depends on Router)
└── Track 8:  Helm charts for all completed services

Day 7–8
├── Track 7a: [J] Agent Framework  (depends on API Gateway)
└── Track 7b: [K] Admin Portal     (depends on Audit Store + Model Registry)

Day 9
└── Track 8:  [M] End-to-end integration test + smoke test script

Day 10
└── ALL:      Demo rehearsal + fix blockers
```

---

## Detailed Build Order Per Track

---

### Track 1 — Foundation Services
> **Owner:** 1 engineer | **Duration:** Day 1–3
> These have zero upstream dependencies. Start immediately on Day 1.

#### [A] Shared IMF Python Library
**Build first — every other service imports this.**

```
services/shared/
├── imf.py          # IMF dataclass / Pydantic model
├── audit.py        # AuditRecord dataclass + async write helper
└── __init__.py
```

What it provides:
- `InternalMessageFormat` Pydantic model (matches master contract IMF JSON exactly)
- `AuditRecord` Pydantic model
- `async write_audit(record)` helper — HTTP POST to audit store, fire-and-forget
- `make_request_id()` — uuid4 generator
- `get_logger()` — structured JSON logger (stdout)

All other services import from this. Keeps IMF consistent everywhere.

**Done when:** `from shared.imf import InternalMessageFormat` works in all service containers.

---

#### [B] Model Registry
**No dependencies. Start Day 1.**

Simple FastAPI + JSON file store.

```
services/model-registry/
├── main.py             # FastAPI app
├── models.json         # seed data with phi3:mini, llama3.2:3b
├── requirements.txt    # fastapi, uvicorn, pydantic
└── Dockerfile
```

Key endpoints:
```
GET  /models
GET  /models/{name}
GET  /models/by-task/{task_type}   ← Router calls this
POST /models
PATCH /models/{name}/status
GET  /health
```

**Done when:** `GET /models/by-task/chat` returns phi3:mini in JSON.

---

#### [C] Audit Store
**No dependencies. Start Day 1.**

FastAPI + SQLite.

```
services/audit-store/
├── main.py             # FastAPI app + SQLite init
├── requirements.txt    # fastapi, uvicorn, aiosqlite
└── Dockerfile
```

Key endpoints:
```
POST /audit/events           ← all layers call this
POST /audit/events/batch
GET  /audit/requests/{id}    ← Admin Portal calls this
GET  /audit/events?...
GET  /health
```

**Done when:** POST an audit record, GET it back by request_id.

---

#### [D] Ollama Setup
**Run natively on Windows. Not a Kubernetes service.**

```cmd
# Install from ollama.com, then:
ollama pull phi3:mini
ollama pull llama3.2:3b

# Keep running in background — Kubernetes services call it via host.docker.internal:11434
ollama serve
```

Kubernetes pods reach it at `http://host.docker.internal:11434`.

**Done when:** `curl http://localhost:11434/api/tags` returns model list.

---

### Track 2 — Inference Adapter
> **Owner:** 1 engineer | **Start:** Day 1 (mock Ollama) → integrate Day 3
> Thin translation layer: IMF → Ollama API → IMF response.

```
services/inference-adapter/
├── main.py             # FastAPI; translates IMF to Ollama /api/chat
├── requirements.txt    # fastapi, uvicorn, httpx, pydantic
└── Dockerfile
```

**Development approach:** Use a mock Ollama response on Day 1–2, wire to real Ollama on Day 3.

Key endpoint:
```
POST /infer        ← Router calls this with IMF
GET  /health
```

What it does:
1. Receives IMF from Router
2. Extracts `request.messages`, `routing.selected_model`, `request.max_tokens`, `request.temperature`
3. POSTs to `http://host.docker.internal:11434/api/chat`
4. Maps Ollama response back into IMF `response` block
5. Writes `inference_complete` audit event
6. Returns enriched IMF to Router

**Done when:** Send an IMF to `/infer`, get back a real phi3:mini response in IMF format.

---

### Track 3 — Cache Layer
> **Owner:** 1 engineer | **Start:** Day 1 (no upstream deps)
> Redis exact cache + sentence-transformers semantic cache.

```
services/cache/
├── main.py             # FastAPI cache service
├── requirements.txt    # fastapi, uvicorn, redis, sentence-transformers, numpy
└── Dockerfile
```

Redis runs as a separate Kubernetes pod (Helm sub-chart). Cache service talks to it.

Key endpoints:
```
POST /cache/lookup      ← Router calls this BEFORE inference
POST /cache/write       ← Router calls this AFTER inference
POST /cache/invalidate
GET  /health
```

**Development approach:**
- Day 1: Exact cache with Redis (just SHA256 key lookup)
- Day 2: Add sentence-transformers semantic layer
- Day 3: Test with real IMF payloads

**Done when:** Send same prompt twice → second lookup returns `"hit": true` with cached response.

---

### Track 4 — Security & Governance Layer
> **Owner:** 1 engineer | **Start:** Day 1 (uses shared IMF lib only)
> Injection scan + PII masking + basic policy check + post-gen PII scan.

```
services/security-layer/
├── main.py                  # FastAPI; pre-gen and post-gen endpoints
├── injection_patterns.yaml  # keyword/regex list
├── requirements.txt         # fastapi, uvicorn, presidio-analyzer, presidio-anonymizer, spacy, pyyaml
└── Dockerfile
```

Key endpoints:
```
POST /process/pre     ← API Gateway calls this with IMF (pre-inference)
POST /process/post    ← Router calls this with IMF (post-inference)
GET  /health
```

**Development approach:**
- Day 1: Keyword injection scanner + role check (no Presidio yet)
- Day 2: Add Presidio PII detection and masking
- Day 3: Add content safety word filter + unit tests for each check

**Note:** Presidio requires spaCy model download. Add to Dockerfile:
```dockerfile
RUN python -m spacy download en_core_web_lg
```

**Done when:** 
- Prompt with `"ignore previous instructions"` → 400 blocked
- Prompt with email address → masked in IMF before forwarding downstream

---

### Track 5 — Intelligent Router
> **Owner:** 1 engineer | **Start:** Day 4 (needs Inference Adapter + Cache + Security done)
> Task classification + model selection + cache lookup + dispatch.

```
services/router/
├── main.py              # FastAPI router service
├── model_matrix.yaml    # task → model mapping (fallback if registry down)
├── task_rules.yaml      # keyword rules for task classification
├── requirements.txt     # fastapi, uvicorn, httpx, pyyaml, pydantic
└── Dockerfile
```

Key endpoint:
```
POST /route         ← Security Layer calls this with IMF
GET  /health
```

Routing flow:
1. Classify task type (keyword rules)
2. Fetch available models from Model Registry
3. Select model for task type
4. Check health of inference adapter
5. Call Cache for lookup → return if hit
6. Call Inference Adapter → get response
7. Call Cache to write new entry
8. Return enriched IMF to Security Layer (post-gen)

**Development approach:**
- Day 4: Wire up with real services but add mock fallbacks for each
- Day 5: Full integration test through Router

**Done when:** Send a `chat` IMF to `/route`, get back a real phi3:mini response, cache and audit records created.

---

### Track 6 — API Gateway
> **Owner:** 1 engineer | **Start:** Day 6 (needs Router done)
> Entry point, auth, rate limiting, IMF creation, response serialization.

```
services/api-gateway/
├── main.py              # FastAPI; OpenAI-compatible surface
├── requirements.txt     # fastapi, uvicorn, httpx, pydantic, slowapi
└── Dockerfile
```

Key endpoints (OpenAI-compatible consumer surface):
```
POST /v1/chat/completions
GET  /v1/models
GET  /health
GET  /metrics
```

What it does:
1. Validate `X-Api-Key` header
2. Rate limit check (in-memory, `slowapi`)
3. Build IMF from incoming OpenAI-format request
4. POST to Security Layer `/process/pre`
5. Security Layer calls Router, which calls everything else
6. Receive enriched IMF back
7. Serialize IMF `response.content` back to OpenAI JSON
8. Write `response_sent` audit event

**Done when:** `curl -X POST http://localhost/v1/chat/completions ...` returns an OpenAI-format response powered by phi3:mini.

---

### Track 7a — Agent Framework
> **Owner:** 1 engineer | **Start:** Day 7 (needs API Gateway running)
> LangGraph ReAct loop with 3 tools.

```
services/agent-framework/
├── main.py              # FastAPI entry; calls LangGraph
├── tools/
│   ├── catalog.yaml
│   ├── calculator.py
│   ├── get_time.py
│   └── web_search.py    # mocked for POC
├── requirements.txt     # fastapi, uvicorn, langgraph, langchain, langchain-openai, httpx
└── Dockerfile
```

LangGraph points its LLM to `http://api-gateway:8080/v1` (platform's own API Gateway). This means agent model calls go through the full governance pipeline — same as a normal user request.

**Done when:** Send `{"messages": [...], "agentic": true}` to the gateway → agent decomposes, calls tools, returns synthesized answer. Audit trail shows multiple `inference_complete` events for the agent's sub-calls.

---

### Track 7b — Admin Portal
> **Owner:** 1 engineer | **Start:** Day 7 (needs Audit Store + Model Registry running; Gateway optional)
> Minimal React UI + thin FastAPI backend.

```
services/admin-portal/
├── backend/
│   └── main.py          # FastAPI; proxies to other APIs
├── frontend/
│   ├── index.html       # Single HTML file with vanilla JS (no React build needed for POC)
│   ├── app.js
│   └── style.css
├── requirements.txt     # fastapi, uvicorn, httpx, jinja2
└── Dockerfile
```

> **POC shortcut:** Use a single HTML page with fetch() calls instead of a full React build. Saves setup time; looks fine for a demo.

Pages to build:
- `/` — Playground (chat input → calls API Gateway)
- `/audit` — Audit event table with request_id drill-down
- `/models` — Model list with activate/retire buttons
- `/metrics` — iframe embedding Grafana

**Done when:** Can open browser, send a chat, see the response, click on the request_id and see the full audit trail across all layers.

---

### Track 8 — Observability + Helm + Kubernetes
> **Owner:** 1 engineer | **Runs in parallel throughout**
> Sets up the cluster and packages everything as the other tracks build.

#### Phase A — Day 1–2: Cluster foundation
```cmd
# Enable Kubernetes in Docker Desktop (manual step)
# Install NGINX Ingress Controller
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml

# Create namespace
kubectl create namespace llm-poc

# Deploy Redis (used by Cache Layer)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install redis bitnami/redis --namespace llm-poc --set auth.enabled=false --set architecture=standalone
```

#### Phase B — Day 3–5: Helm charts as services complete
For each completed service, create its Helm chart:
```
helm/llm-platform/charts/<service>/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml
    └── servicemonitor.yaml
```

#### Phase C — Day 5–6: Observability
```cmd
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install observability prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword=poc-admin \
  --set alertmanager.enabled=false
```

Import the POC overview dashboard JSON into Grafana.

#### Phase D — Day 8–9: Integration and smoke tests
Write `scripts/smoke-test.cmd` that runs all 7 demo scenarios automatically and asserts expected results.

---

## Parallel Work Visualised (Gantt)

```
         Day 1    Day 2    Day 3    Day 4    Day 5    Day 6    Day 7    Day 8    Day 9    Day 10
         ───────────────────────────────────────────────────────────────────────────────────────
T1: Foundation
  IMF Lib        ████
  Model Registry ████
  Audit Store    ████
  Ollama Setup   ██

T2: Inference
  Adapter        ████(mock)████(real)

T3: Cache
  Cache Layer    ████████████

T4: Security
  Security Layer ████████████████

T5: Router
  Router                          ████████

T6: Gateway
  API Gateway                              ████████

T7a: Agent
  Agent Framework                                   ████████

T7b: Portal
  Admin Portal                             ████████████

T8: Platform
  Kubernetes/Helm████████████████████████████████████
  Observability  ████████████████████
  Integration                                       ████████
  Demo Prep                                                  ████
```

---

## Interface Contracts Between Teams

To avoid teams blocking each other, each service exposes a mock version on Day 1. Use these stub responses so other tracks can code against real HTTP calls.

### Mock Stub Pattern
Each service should have a `--mock` flag that returns hardcoded responses:

```python
# In any FastAPI service, add during development:
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

@app.post("/process/pre")
async def pre_process(imf: dict):
    if MOCK_MODE:
        return {**imf, "governance": {"pii_masked": False, "content_safety_passed": True, ...}}
    # real implementation
```

Deploy mock versions to Kubernetes early so downstream teams can develop against real HTTP endpoints.

### Contract Table (What Each Service Expects)

| Service | Calls | Expected Response |
|---|---|---|
| API Gateway | `POST security-layer:8081/process/pre` | Enriched IMF with `governance` block populated |
| Security Layer | `POST router:8082/route` | Enriched IMF with `routing` + `response` blocks |
| Router | `POST cache:8086/cache/lookup` | `{"hit": bool, "response": IMF or null}` |
| Router | `POST inference-adapter:8087/infer` | IMF with `response` block populated |
| Router | `GET model-registry:5000/models/by-task/{type}` | `[{name, backend, endpoint, status}]` |
| Any layer | `POST audit-store:9200/audit/events` | `{"status": "ok"}` (fire-and-forget) |
| Admin Portal | `GET audit-store:9200/audit/requests/{id}` | Array of audit records |
| Admin Portal | `GET model-registry:5000/models` | Array of model metadata |

---

## Definition of Done — POC Complete

The POC is complete and ready to demo when all of the following pass:

```
[ ] curl /v1/chat/completions returns a real LLM response via phi3:mini
[ ] Injection attempt returns 400 with security_block reason
[ ] PII in prompt is masked before reaching inference (check audit log)
[ ] Same prompt twice → second response shows cache.lookup_hit = true
[ ] Audit trail for any request_id shows events from 5+ layers
[ ] Admin Portal loads in browser, playground works, audit viewer shows records
[ ] Model Registry shows registered models; retire/activate works
[ ] Grafana dashboard shows live request rate and latency
[ ] All pods show Running in kubectl get pods -n llm-poc
[ ] Agent Framework handles a multi-step tool-calling request
```

---

## How to Start Right Now

### If you are 1 engineer working alone:
Follow this order — each item unblocks the next:
1. `[A]` Shared IMF library
2. `[B]` Model Registry + `[C]` Audit Store (both simple, no deps — do in one session)
3. `[D]` Install Ollama, pull phi3:mini
4. `[E]` Inference Adapter
5. `[F]` Cache Layer (can do in parallel with step 4)
6. `[G]` Security Layer (can do in parallel with steps 4–5)
7. `[H]` Router
8. `[I]` API Gateway
9. `[K]` Admin Portal
10. `[J]` Agent Framework
11. `[L+M]` Helm charts + Observability + integration tests

### If you are 2–3 engineers:
- **Engineer 1:** Track 1 (Foundation) + Track 8 (Kubernetes/Helm)
- **Engineer 2:** Track 3 (Cache) + Track 4 (Security) + Track 2 (Inference Adapter)
- **Engineer 3:** Track 5 (Router) + Track 6 (Gateway) + Track 7b (Portal)

---

*Reference: steering files in `.kiro/steering/` for each layer's detailed spec.*
*Reference: `LOCAL_DEMO_SETUP.md` for machine setup prerequisites.*
*Reference: `POC_to_Production_Gap_Analysis.md` for what comes after POC.*
