# Layer-Wise Implementation Guide (POC)

> Who builds what, files to create, what it does, how to test it, and what depends on it.
> Parallel tracks marked clearly. Read this before writing a single line of code.

---

## Before Anything — Shared Foundation (Everyone Needs This)

### Shared IMF Library
**Location:** `services/shared/`
**Who:** Any one engineer, Day 1, first thing.
**Why first:** Every single service imports this. Nothing else can start until this exists.

**Files to create:**
```
services/shared/
├── imf.py        ← IMF Pydantic model
├── audit.py      ← AuditRecord model + async write_audit() helper
├── logging.py    ← structured JSON logger
└── __init__.py
```

**What `imf.py` must contain:**
- `InternalMessageFormat` — full Pydantic model matching the master contract JSON exactly
- `make_request_id()` — returns `str(uuid.uuid4())`
- Default values for all blocks: `governance`, `routing`, `cache`, `response` initialized empty

**What `audit.py` must contain:**
- `AuditRecord` Pydantic model
- `async write_audit(record: dict, audit_url: str)` — fire-and-forget httpx POST, swallow errors

**Done when:** `from shared.imf import InternalMessageFormat` works. Takes ~2 hours.

---

## Parallel Group 1 — Start All Together on Day 1

These four have zero dependencies on each other or on upstream services.
**Assign one engineer per item, all start simultaneously.**

---

### Layer 7 — Model Registry
**Port:** 5000 | **Location:** `services/model-registry/`
**Can start:** Day 1 | **Depends on:** Nothing
**Parallel with:** Audit Store, Cache, Security, Kubernetes setup

**Files to create:**
```
services/model-registry/
├── main.py
├── models.json          ← seed data (3 models pre-loaded)
├── requirements.txt     ← fastapi, uvicorn, pydantic
└── Dockerfile
```

**What it does:**
Stores model metadata in a JSON file. The Router calls this to know which model handles which task type.

**Seed data in `models.json`** — pre-populate with:
- `llama3.2:3b` → tasks: [chat, summarization, reasoning] → status: active
- `llama3.2:3b` → tasks: [chat, summarization, reasoning] → status: active
- `llama3.2:3b` → tasks: [code] → status: active

**Key endpoints:**
```
GET  /models                       ← Router polls this every 60s
GET  /models/by-task/{task_type}   ← Router calls before routing
POST /models                       ← register new model
PATCH /models/{name}/status        ← Admin Portal activate/retire
GET  /health
```

**How to test:**
```cmd
curl http://localhost:5000/models/by-task/chat
# Must return llama3.2:3b and llama3.2:3b
```

**Done when:** Router can fetch models and get a JSON list back. ~3 hours.

---

### Layer 9 — Audit Store
**Port:** 9200 | **Location:** `services/audit-store/`
**Can start:** Day 1 | **Depends on:** Nothing
**Parallel with:** Model Registry, Cache, Security, Kubernetes setup

**Files to create:**
```
services/audit-store/
├── main.py
├── requirements.txt     ← fastapi, uvicorn, aiosqlite, pydantic
└── Dockerfile
```

**What it does:**
Every layer writes an audit event here after processing a request. SQLite stores it. The Admin Portal queries it to show the full request trail.

**SQLite table — create on startup:**
```sql
CREATE TABLE IF NOT EXISTS audit_events (
    audit_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
    timestamp_utc TEXT, user_id TEXT, department TEXT,
    layer TEXT, event_type TEXT, model_used TEXT,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0, outcome TEXT,
    error_code TEXT, pii_actions TEXT, policy_decisions TEXT
);
```

**Key endpoints:**
```
POST /audit/events              ← all layers call this (fire-and-forget)
POST /audit/events/batch        ← bulk write
GET  /audit/requests/{id}       ← Admin Portal: show full trail for one request
GET  /audit/events?from=&to=    ← Admin Portal: recent events table
GET  /audit/summary             ← Admin Portal: counts by layer/outcome
GET  /health
```

**How to test:**
```cmd
curl -X POST http://localhost:9200/audit/events \
  -H "Content-Type: application/json" \
  -d "{\"audit_id\":\"test-1\",\"request_id\":\"req-1\",\"layer\":\"api_gateway\",\"event_type\":\"request_received\",\"outcome\":\"pass\"}"

curl http://localhost:9200/audit/requests/req-1
# Must return the event you just wrote
```

**Done when:** Write an event, read it back by request_id. ~3 hours.

---

### Layer 4 — Cache Layer
**Port:** 8086 | **Location:** `services/cache/`
**Can start:** Day 1 | **Depends on:** Redis (deploy Redis first via Helm)
**Parallel with:** Model Registry, Audit Store, Security

**Files to create:**
```
services/cache/
├── main.py
├── requirements.txt     ← fastapi, uvicorn, redis, sentence-transformers, numpy, pydantic
└── Dockerfile
```

**What it does:**
Before inference, the Router asks: "do we have a cached answer for this prompt?"
- Exact match: SHA256(messages + model + task_type) → Redis key lookup
- Semantic match: embed the prompt with `all-MiniLM-L6-v2`, cosine similarity scan against stored vectors in Redis

After inference, the Router writes the response here for future cache hits.

**Note:** `sentence-transformers` is ~500 MB download. Happens at Docker build time. Plan for this.

**Cache key formula:**
```python
content = " ".join(m["content"] for m in messages).lower().strip()
key = sha256(f"{content}|{model}|{task_type}".encode()).hexdigest()
```

**Key endpoints:**
```
POST /cache/lookup    ← Router calls BEFORE inference
                        Body: {messages, model, task_type, request_id}
                        Returns: {hit: bool, cache_type, response, similarity_score}

POST /cache/write     ← Router calls AFTER inference (on cache miss)
                        Body: {messages, model, task_type, response_imf}
                        Returns: {status: "ok"}

GET  /health
```

**Redis data layout:**
```
exact:{sha256}              → string, IMF response JSON, TTL 1hr
semantic_cache:{task_type}  → list of {key, embedding, response}
```

**How to test:**
```cmd
# Deploy Redis first: helm install redis bitnami/redis -n llm-poc --set auth.enabled=false
# Start cache service

# Write a cache entry
curl -X POST http://localhost:8086/cache/write \
  -d '{"messages":[{"role":"user","content":"what is kubernetes"}],"model":"llama3.2:3b","task_type":"chat","response_imf":{"response":{"content":"K8s is..."}}}'

# Lookup same prompt — should HIT
curl -X POST http://localhost:8086/cache/lookup \
  -d '{"messages":[{"role":"user","content":"what is kubernetes"}],"model":"llama3.2:3b","task_type":"chat"}'
# Returns: {"hit": true, "cache_type": "exact", ...}
```

**Done when:** Same prompt → hit. Different but similar prompt → semantic hit. New prompt → miss. ~5 hours.

---

### Layer 2 — Security & Governance Layer
**Port:** 8081 | **Location:** `services/security-layer/`
**Can start:** Day 1 | **Depends on:** Shared IMF lib, Audit Store (can mock)
**Parallel with:** Model Registry, Audit Store, Cache

**Files to create:**
```
services/security-layer/
├── main.py
├── injection_patterns.yaml
├── requirements.txt     ← fastapi, uvicorn, presidio-analyzer, presidio-anonymizer, spacy, pyyaml, httpx, pydantic
└── Dockerfile
```

**Important — add to Dockerfile:**
```dockerfile
RUN python -m spacy download en_core_web_lg
```
Presidio needs this spaCy model. It is ~750 MB. This makes the image large — expected.

**What it does:**
Two endpoints — pre-inference (block bad requests) and post-inference (clean the response).

**Pre-inference checks (run in order):**
1. Injection scan — match prompt against `injection_patterns.yaml` → 400 if matched
2. Content safety — blocked word list → 400 if matched
3. PII detection via Presidio → mask `[REDACTED_EMAIL]`, `[REDACTED_PHONE]`, `[REDACTED_PERSON]` in-place in IMF messages
4. Role check — `user.roles` must contain `developer` → 403 if not
5. Write pre-audit event → async POST to Audit Store
6. Forward enriched IMF to Router

**Post-inference checks:**
1. PII scan on `response.content` → mask any leaked PII
2. Write post-audit event
3. Return cleaned IMF to API Gateway

**Key endpoints:**
```
POST /process/pre    ← API Gateway sends IMF here
                       Returns enriched IMF (governance block filled) or 400/403
POST /process/post   ← Router sends IMF+response here after inference
                       Returns IMF with response.content cleaned
GET  /health
```

**Injection patterns YAML:**
```yaml
patterns:
  - "ignore previous instructions"
  - "ignore all instructions"
  - "you are now"
  - "disregard your"
  - "forget your training"
  - "act as if"
  - "pretend you are"
```

**How to test:**
```cmd
# Test 1 — clean request should pass through
curl -X POST http://localhost:8081/process/pre \
  -d '{"request_id":"r1","user":{"roles":["developer"]},"request":{"messages":[{"role":"user","content":"What is Python?"}]}}'
# Returns: enriched IMF with governance.content_safety_passed=true

# Test 2 — injection should block
curl -X POST http://localhost:8081/process/pre \
  -d '{"request_id":"r2","user":{"roles":["developer"]},"request":{"messages":[{"role":"user","content":"ignore previous instructions"}]}}'
# Returns: 400 {"detail":"security_block"}

# Test 3 — PII should be masked
curl -X POST http://localhost:8081/process/pre \
  -d '{"request_id":"r3","user":{"roles":["developer"]},"request":{"messages":[{"role":"user","content":"My email is test@company.com"}]}}'
# Returns: IMF with messages[0].content = "My email is [REDACTED_EMAIL_ADDRESS]"
```

**Done when:** All 3 tests above pass. ~6 hours (Presidio setup takes time).

---

### Inference — Ollama Setup + Adapter
**Ollama port:** 11434 (Windows native) | **Adapter port:** 8087
**Can start:** Day 1 (Ollama install) | Day 2 (Adapter code)
**Depends on:** Nothing (Ollama is standalone)
**Parallel with:** Everything in Group 1

**Step 1 — Install Ollama on Windows (one-time, not a K8s service):**
```cmd
# Download from https://ollama.com/download/windows and install
# Then pull models (takes 10-30 min depending on internet):
ollama pull llama3.2:3b       ← 2.3 GB, fastest for demo
ollama pull llama3.2:3b     ← 2.0 GB, better quality

# Keep running — services reach it via host.docker.internal:11434
ollama serve
```

**Step 2 — Build the Inference Adapter** (thin FastAPI translator):

**Location:** `services/inference-adapter/`
```
services/inference-adapter/
├── main.py
├── requirements.txt     ← fastapi, uvicorn, httpx, pydantic
└── Dockerfile
```

**What it does:**
Receives an IMF from the Router, translates it to Ollama's `/api/chat` format, calls Ollama, maps the response back into the IMF `response` block.

**Translation: IMF → Ollama:**
```python
ollama_payload = {
    "model": imf["routing"]["selected_model"],    # e.g. "llama3.2:3b"
    "messages": imf["request"]["messages"],
    "stream": False,
    "options": {
        "num_predict": imf["request"].get("max_tokens", 2048),
        "temperature": imf["request"].get("temperature", 0.7)
    }
}
# POST to http://host.docker.internal:11434/api/chat
```

**Translation: Ollama → IMF response block:**
```python
imf["response"]["content"] = ollama_resp["message"]["content"]
imf["response"]["finish_reason"] = "stop"
imf["response"]["usage"]["prompt_tokens"] = ollama_resp.get("prompt_eval_count", 0)
imf["response"]["usage"]["completion_tokens"] = ollama_resp.get("eval_count", 0)
imf["metadata"]["inference_backend"] = "ollama"
imf["metadata"]["inference_latency_ms"] = ollama_resp.get("total_duration", 0) // 1_000_000
```

**Key endpoints:**
```
POST /infer     ← Router calls this with full IMF
                  Returns IMF with response block filled
GET  /health    ← calls Ollama /api/tags and returns status
```

**How to test:**
```cmd
curl -X POST http://localhost:8087/infer \
  -H "Content-Type: application/json" \
  -d "{\"request_id\":\"r1\",\"routing\":{\"selected_model\":\"llama3.2:3b\"},\"request\":{\"messages\":[{\"role\":\"user\",\"content\":\"What is 2+2?\"}],\"max_tokens\":100,\"temperature\":0.7}}"
# Returns: IMF with response.content = "4" (or similar)
```

**Done when:** Real response from llama3.2:3b comes back in IMF format. ~3 hours.

---

### Kubernetes & Helm Infrastructure Setup
**Who:** 1 engineer running in parallel from Day 1
**This track has no code — pure infrastructure setup**

**Day 1 tasks:**
```cmd
# 1. Enable Kubernetes in Docker Desktop
#    Settings → Kubernetes → Enable Kubernetes → Apply & Restart

# 2. Verify
kubectl get nodes
# Expected: docker-desktop   Ready

# 3. Install Helm
winget install Helm.Helm
helm version  # verify

# 4. Install NGINX Ingress Controller
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=120s

# 5. Create platform namespace
kubectl create namespace llm-poc

# 6. Deploy Redis (Cache Layer needs this)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install redis bitnami/redis --namespace llm-poc \
  --set auth.enabled=false \
  --set architecture=standalone \
  --set master.persistence.size=5Gi
kubectl rollout status statefulset/redis-master -n llm-poc
```

**Day 2–4 tasks (as services get built):**
For each completed service, create its Helm chart:
```
helm/llm-platform/charts/{service}/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    └── configmap.yaml      (for YAML configs like model_matrix.yaml)
```

**Day 5–6: Deploy Observability stack:**
```cmd
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install observability prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword=poc-admin \
  --set grafana.service.type=NodePort \
  --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.retention=7d
```

**Add to hosts file** (`C:\Windows\System32\drivers\etc\hosts`):
```
127.0.0.1  llm-poc.local
127.0.0.1  llm-portal.local
```

---

---

## Parallel Group 2 — Start When Group 1 is Done (Day 3–4)

These need at least the Inference Adapter, Cache, and Security Layer working.
**Router is the critical dependency for everything above it.**

---

### Layer 3 — Intelligent Router
**Port:** 8082 | **Location:** `services/router/`
**Can start:** When Inference Adapter + Cache + Security Layer are done
**Depends on:** Inference Adapter (8087), Cache (8086), Security Layer (8081), Model Registry (5000)

**Files to create:**
```
services/router/
├── main.py
├── model_matrix.yaml        ← fallback if model-registry is down
├── task_classifier_rules.yaml
├── requirements.txt         ← fastapi, uvicorn, httpx, pyyaml, pydantic
└── Dockerfile
```

**What it does — full routing flow:**
```
Receive IMF from Security Layer
  ↓
1. Classify task type (keyword scan over messages)
   "code"/"function"/"python" → code
   "summarize"/"tldr"         → summarization
   "translate"                → translation
   default                    → chat

2. Fetch models from Model Registry (cached, refresh every 60s)
   GET http://model-registry:5000/models/by-task/{task_type}
   Fallback to model_matrix.yaml if registry unreachable

3. Health check → GET {model_endpoint}/api/tags
   If unhealthy → try fallback model from registry

4. Cache lookup → POST http://cache:8086/cache/lookup
   HIT  → return cached IMF immediately (no inference)
   MISS → continue

5. Call Inference Adapter → POST http://inference-adapter:8087/infer
   Pass full IMF with routing.selected_model set
   On error → try fallback model

6. Cache write → POST http://cache:8086/cache/write (async)

7. Write routing_decision audit event

8. Return IMF with response block to Security Layer (for post-gen)
```

**Config files to create:**

`task_classifier_rules.yaml`:
```yaml
rules:
  code: ["code", "function", "python", "javascript", "debug", "implement", "script"]
  reasoning: ["reason", "step by step", "math", "calculate", "prove", "analyze"]
  summarization: ["summarize", "summary", "tldr", "shorten", "condense"]
  translation: ["translate", "in french", "in spanish", "in german"]
default: chat
```

`model_matrix.yaml` (fallback only):
```yaml
task_defaults:
  chat: llama3.2:3b
  code: llama3.2:3b
  reasoning: llama3.2:3b
  summarization: llama3.2:3b
  translation: llama3.2:3b
```

**Key endpoint:**
```
POST /route     ← Security Layer sends IMF here
                  Returns IMF with routing + response blocks filled
GET  /health
```

**Also expose OpenAI-compatible endpoint (needed by Agent Framework LangChain client):**
```
POST /v1/chat/completions   ← Agent Framework's LangChain calls here
                              Wraps IMF creation + route internally
```

**How to test:**
```cmd
curl -X POST http://localhost:8082/route \
  -H "Content-Type: application/json" \
  -d "{\"request_id\":\"r1\",\"user\":{\"user_id\":\"poc-user\",\"department\":\"poc\",\"roles\":[\"developer\"],\"auth_method\":\"api_key\"},\"request\":{\"messages\":[{\"role\":\"user\",\"content\":\"What is Kubernetes?\"}]},\"governance\":{\"content_safety_passed\":true}}"
# Returns: IMF with response.content filled from llama3.2:3b
```

**Done when:** End-to-end flow works: Security → Router → Cache miss → Inference → response back. ~6 hours.

---

---

## Parallel Group 3 — Start When Router is Done (Day 5–6)

---

### Layer 1 — API Gateway
**Port:** 8080 | **Location:** `services/api-gateway/`
**Can start:** When Router is done
**Depends on:** Security Layer (8081)
**This is the public entry point — it's what users and your demo curl hits**

**Files to create:**
```
services/api-gateway/
├── main.py
├── requirements.txt     ← fastapi, uvicorn, httpx, pydantic, slowapi
└── Dockerfile
```

**What it does:**
Accepts OpenAI-format requests, validates the API key, builds an IMF from scratch, sends it to Security Layer, receives the final IMF back, converts it to OpenAI-format response.

**Request flow inside this service:**
```
POST /v1/chat/completions
  ↓
1. Validate X-Api-Key header (compare to env GATEWAY_API_KEY)
   → 401 if wrong

2. Rate limit check (in-memory, 60 req/min per key using slowapi)
   → 429 if over limit

3. Build IMF from OpenAI request body:
   request_id = uuid4()
   user = {user_id: "poc-user", department: "poc", roles: ["developer"], auth_method: "api_key"}
   request = {model, messages, stream, max_tokens, temperature}
   governance/routing/cache/response = default empty values

4. Write request_received + auth_pass audit events (async)

5. POST imf to http://security-layer:8081/process/pre
   → Security Layer runs checks, calls Router, gets response back
   → On 400/403 from security: return that error to client

6. Receive enriched IMF with response.content filled

7. Write response_sent audit event

8. Serialize IMF response → OpenAI format:
   {"id": request_id, "choices": [{"message": {"role": "assistant", "content": response.content}}],
    "usage": {...}, "model": routing.selected_model}

9. Return to client
```

**Key endpoints:**
```
POST /v1/chat/completions    ← main endpoint (OpenAI-compatible)
GET  /v1/models              ← return static list from Model Registry
GET  /health
GET  /metrics                ← Prometheus counters
```

**Env vars:**
```
GATEWAY_API_KEY=poc-secret-key
DOWNSTREAM_SECURITY_URL=http://security-layer:8081
MODEL_REGISTRY_URL=http://model-registry:5000
AUDIT_STORE_URL=http://audit-store:9200
```

**How to test (this is the big end-to-end test):**
```cmd
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "X-Api-Key: poc-secret-key" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3.2:3b\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 2+2?\"}]}"
# Returns OpenAI-format JSON with answer from llama3.2:3b

# Test auth failure
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "X-Api-Key: wrong-key" \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}"
# Returns: {"error": {"code": "401", "message": "Unauthorized"}}
```

**Done when:** Full end-to-end curl works. Response comes from llama3.2:3b through all layers. ~5 hours.

---

---

## Parallel Group 4 — Start When API Gateway is Done (Day 7–8)

These two can be built in parallel — they don't depend on each other.

---

### Layer 6 — Agent Framework
**Port:** 8083 | **Location:** `services/agent-framework/`
**Can start:** When API Gateway is done
**Depends on:** API Gateway (8080) — LangGraph calls it as the LLM endpoint
**Parallel with:** Admin Portal

**Files to create:**
```
services/agent-framework/
├── main.py
├── tools/
│   ├── catalog.yaml
│   ├── calculator.py      ← safe math eval
│   ├── get_time.py        ← datetime.utcnow()
│   └── web_search.py      ← returns mock/hardcoded results
├── requirements.txt       ← fastapi, uvicorn, langgraph, langchain, langchain-openai, httpx, pydantic
└── Dockerfile
```

**What it does:**
When a request has `"agentic": true`, the Router sends the IMF here. A LangGraph ReAct agent decomposes the task, calls tools, loops back through the platform for each LLM call, and returns a final synthesized answer.

**Key setup — LangGraph LLM client points to YOUR API Gateway:**
```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://api-gateway:8080/v1",
    api_key="poc-secret-key",
    model="llama3.2:3b",
    temperature=0.7
)
```
This means every agent sub-call goes through the full security + audit pipeline. That's intentional.

**Tools to implement:**

`calculator.py`:
```python
import ast, operator

def calculator(expression: str) -> str:
    # safe eval: only allow math operators, no builtins
    allowed = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod}
    # parse and evaluate; return result as string
```

`get_time.py`:
```python
from datetime import datetime, timezone
def get_current_time() -> str:
    return datetime.now(timezone.utc).isoformat()
```

`web_search.py` (mocked for POC):
```python
def web_search(query: str) -> str:
    return f"[POC Mock] Search results for '{query}': This is a simulated result. In production this would query an enterprise search system."
```

**Key endpoint:**
```
POST /agent/run    ← Router sends IMF here when request.agentic=true
                     Returns final IMF with response.content = agent's synthesized answer
GET  /health
```

**How to test:**
```cmd
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "X-Api-Key: poc-secret-key" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"llama3.2:3b\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 15% of 340? Also tell me the current time.\"}],\"agentic\":true}"
# Agent should call calculator tool AND get_current_time tool, then synthesize answer
```

**Done when:** Agent uses at least 2 tools in one response and returns a coherent answer. ~6 hours.

---

### Layer 10 — Admin Portal
**Port:** 8084 | **Location:** `services/admin-portal/`
**Can start:** When Audit Store + Model Registry are done (Day 3 onwards for backend; Day 7 for full UI)
**Depends on:** Audit Store (9200), Model Registry (5000), API Gateway (8080 for playground)
**Parallel with:** Agent Framework

**Files to create:**
```
services/admin-portal/
├── backend/
│   └── main.py          ← FastAPI, proxies to other services
├── frontend/
│   ├── index.html       ← single HTML file, no build step
│   ├── app.js           ← vanilla JS fetch() calls
│   └── style.css        ← minimal CSS
├── requirements.txt     ← fastapi, uvicorn, httpx, jinja2
└── Dockerfile
```

**Backend — proxy endpoints:**
```
GET   /portal/health
POST  /portal/playground/chat      → http://api-gateway:8080/v1/chat/completions
GET   /portal/audit/requests/{id}  → http://audit-store:9200/audit/requests/{id}
GET   /portal/audit/events         → http://audit-store:9200/audit/events
GET   /portal/models               → http://model-registry:5000/models
PATCH /portal/models/{name}/status → http://model-registry:5000/models/{name}/status
```

**Frontend pages (all in one `index.html` with JS tabs):**

Tab 1 — Playground:
```
[Model dropdown] [Send button]
[Chat message input]
[Response display area]
[Request ID: xxxxxxxx] [View Audit Trail button]
```

Tab 2 — Audit Trail:
```
[From datetime] [To datetime] [Filter button]
Table: timestamp | request_id (clickable) | layer | event_type | outcome | latency_ms
Click request_id → show all events for that request in order
```

Tab 3 — Models:
```
Table: name | version | tasks | status | [Activate] [Retire] buttons
```

Tab 4 — Metrics (iframe):
```html
<iframe src="http://localhost:3000/d/..." height="600px"></iframe>
```

**How to test:**
Open `http://localhost:8084` in browser. Send a message in playground. Click the request ID. See 5+ audit events from different layers appearing in the audit trail.

**Done when:** Full demo flow works in the browser. ~8 hours (frontend takes time).

---

---

## Layer 8 — Observability (Runs Throughout)

**Not a custom service — deploy via Helm.**
**Who:** Infrastructure engineer, start Day 5 once Redis and a few services are running.

```cmd
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install observability prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword=poc-admin \
  --set grafana.service.type=NodePort \
  --set alertmanager.enabled=false
```

**Each custom service must expose `/metrics`** using `prometheus_client`:
```python
from prometheus_client import Counter, Histogram, make_asgi_app
import prometheus_client

requests_total = Counter("llm_api_gateway_requests_total", "Requests", ["status"])
latency = Histogram("llm_api_gateway_latency_seconds", "Latency")

# Mount metrics endpoint in FastAPI
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

**Grafana dashboard to create** — import this JSON or build manually with these panels:
1. Request rate: `rate(llm_api_gateway_requests_total[1m])`
2. Error rate: `rate(llm_*_requests_total{status="error"}[1m])`
3. Cache hit rate: `llm_cache_requests_total{outcome="hit"}` / total
4. Security blocks: `rate(llm_security_requests_total{outcome="block"}[1m])`
5. Inference latency P95: `histogram_quantile(0.95, llm_inference_latency_seconds_bucket)`

Access Grafana: `kubectl port-forward svc/observability-grafana 3000:80 -n monitoring`
Login: admin / poc-admin

---

## Python Dependencies Summary

Each service needs these in `requirements.txt`. Copy the relevant ones:

| Package | Used By |
|---|---|
| `fastapi` | All services |
| `uvicorn[standard]` | All services |
| `httpx` | All services that call other services |
| `pydantic>=2` | All services |
| `prometheus-client` | All services (metrics) |
| `structlog` | All services (JSON logging) |
| `pyyaml` | Router, Security Layer |
| `redis` | Cache service |
| `sentence-transformers` | Cache service |
| `numpy` | Cache service (cosine similarity) |
| `aiosqlite` | Audit Store |
| `presidio-analyzer` | Security Layer |
| `presidio-anonymizer` | Security Layer |
| `spacy` | Security Layer |
| `slowapi` | API Gateway (rate limiting) |
| `langgraph` | Agent Framework |
| `langchain-openai` | Agent Framework |
| `jinja2` | Admin Portal backend |

---

## Dockerfile Template (All Services)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy shared lib first
COPY ../shared ./shared

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE {PORT}
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{PORT}"]
```

For Security Layer, add after `pip install`:
```dockerfile
RUN python -m spacy download en_core_web_lg
```

---

## Build All Images (Run from project root)

```cmd
REM Run this script after all services are coded
docker build -t model-registry:poc ./services/model-registry
docker build -t audit-store:poc ./services/audit-store
docker build -t cache-service:poc ./services/cache
docker build -t security-layer:poc ./services/security-layer
docker build -t inference-adapter:poc ./services/inference-adapter
docker build -t router:poc ./services/router
docker build -t api-gateway:poc ./services/api-gateway
docker build -t agent-framework:poc ./services/agent-framework
docker build -t admin-portal:poc ./services/admin-portal
```

Security Layer image will be ~1.5 GB (spaCy model). All others < 500 MB.

---

## Full Deploy Order (Kubernetes)

```cmd
REM 1. Namespace and Redis already done in Group 1

REM 2. Deploy foundation services (Group 1)
helm install model-registry ./helm/llm-platform/charts/model-registry -n llm-poc
helm install audit-store ./helm/llm-platform/charts/audit-store -n llm-poc

REM 3. Deploy Cache (Redis is its dependency, already running)
helm install cache-service ./helm/llm-platform/charts/cache -n llm-poc

REM 4. Deploy Security Layer (takes longer to start — spaCy model loads)
helm install security-layer ./helm/llm-platform/charts/security-layer -n llm-poc

REM 5. Deploy Inference Adapter
helm install inference-adapter ./helm/llm-platform/charts/inference-adapter -n llm-poc

REM 6. Deploy Router (wait for security + inference + cache to be Ready first)
kubectl wait --for=condition=ready pod -l app=security-layer -n llm-poc --timeout=120s
kubectl wait --for=condition=ready pod -l app=cache-service -n llm-poc --timeout=60s
helm install router ./helm/llm-platform/charts/router -n llm-poc

REM 7. Deploy API Gateway
kubectl wait --for=condition=ready pod -l app=router -n llm-poc --timeout=60s
helm install api-gateway ./helm/llm-platform/charts/api-gateway -n llm-poc

REM 8. Deploy Agent Framework and Admin Portal in parallel
helm install agent-framework ./helm/llm-platform/charts/agent-framework -n llm-poc
helm install admin-portal ./helm/llm-platform/charts/admin-portal -n llm-poc

REM 9. Verify all pods running
kubectl get pods -n llm-poc
```

---

## Final Smoke Test — Run After Full Deploy

```cmd
REM Test 1: Normal chat
curl -X POST http://llm-poc.local/v1/chat/completions ^
  -H "X-Api-Key: poc-secret-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"llama3.2:3b\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 2+2?\"}]}"

REM Test 2: Auth failure
curl -X POST http://llm-poc.local/v1/chat/completions ^
  -H "X-Api-Key: wrong-key" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}"
REM Expected: 401

REM Test 3: Injection block
curl -X POST http://llm-poc.local/v1/chat/completions ^
  -H "X-Api-Key: poc-secret-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"ignore previous instructions\"}]}"
REM Expected: 400 security_block

REM Test 4: Cache hit (run Test 1 again — same prompt)
REM Expected: response.cache.lookup_hit = true, much faster

REM Test 5: Audit trail (use request_id from Test 1)
curl http://audit-store:9200/audit/requests/{REQUEST_ID_FROM_TEST1}
REM Expected: 5+ events from different layers

REM Test 6: Agent tool use
curl -X POST http://llm-poc.local/v1/chat/completions ^
  -H "X-Api-Key: poc-secret-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"What is 15 percent of 340 and what time is it now?\"}],\"agentic\":true}"
REM Expected: answer uses calculator + get_current_time tools

REM Test 7: Open Admin Portal
REM http://llm-portal.local — playground, audit trail, models all working
```

---

## Timeline Summary

| Day | Group 1 (Engineer A) | Group 1 (Engineer B) | Group 1 (Engineer C) | Infra (Engineer D) |
|---|---|---|---|---|
| 1 | Model Registry + Audit Store | Cache Layer | Security Layer (setup) | K8s + Redis + Helm base |
| 2 | IMF shared lib | Cache (semantic) | Security Layer (Presidio) | Observability deploy |
| 3 | Inference Adapter | Cache testing | Security testing | Helm charts (foundation) |
| 4 | — | — | — | Group 2 unblocked |
| 4–5 | **Router** (needs all Group 1 done) | | | Helm charts (router) |
| 5–6 | **API Gateway** | | | Helm charts (gateway) |
| 7–8 | **Agent Framework** | **Admin Portal** | | Integration + smoke test |
| 9 | Integration fixes | Integration fixes | | Full deploy + verify |
| 10 | Demo prep | Demo prep | | — |
