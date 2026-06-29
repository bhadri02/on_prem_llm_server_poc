---
inclusion: manual
---

# Layer 3 — Intelligent Router (POC)

> Load this file when working on the Intelligent Router layer: `#03-layer-intelligent-router`
> **Scope:** Proof-of-Concept — demonstrate routing decisions, not production scoring.

---

## POC Goal

Show that the platform can classify the incoming task type and route to the correct inference backend based on a simple capability matrix. Fallback on inference failure should work. No GPU probing or weighted scoring required.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Task Classifier | Keyword/heuristic classifier | No ML model; rule-based keyword matching |
| Model Capability Matrix | Static YAML config | No MLflow integration; hardcoded in config |
| Health Check | HTTP GET to inference `/health` | Simple readiness check, no Prometheus |
| Policy Filter | Static department-model allow-list | No OPA; YAML config lookup |
| Fallback Manager | Try primary → try fallback → 503 | No circuit breaker; simple try/except |
| Cache Lookup | HTTP call to Cache Layer | Call cache before inference |
| Routing Mode | `auto` and `pinned` only | No A/B testing, no experiment mode |

---

## Routing Decision Flow (POC)

```
Receive IMF from Security Layer (HTTP JSON)
  │
  ├─ 1. TASK CLASSIFICATION (keyword-based)
  │       → Scan messages for keywords → assign task_type
  │       → Set IMF request.task_type
  │
  ├─ 2. MODEL SELECTION
  │       auto mode:   look up capability matrix → pick primary model for task_type
  │       pinned mode: use IMF request.model (validate it exists)
  │
  ├─ 3. HEALTH CHECK
  │       → HTTP GET to selected model's /health endpoint
  │       → If unhealthy → try fallback model
  │
  ├─ 4. CACHE LOOKUP
  │       → HTTP POST to Cache Layer with request hash
  │       → HIT: return cached response (skip inference)
  │       → MISS: continue
  │
  ├─ 5. DISPATCH TO INFERENCE
  │       → Set IMF routing.selected_model
  │       → Forward to inference endpoint
  │
  └─ 6. FALLBACK (on error)
        → Try next model in fallback list
        → After all exhausted: return 503
```

---

## Task Classification (POC — Keyword-Based)

```yaml
# task_classifier_rules.yaml
rules:
  code:
    keywords: ["code", "function", "python", "javascript", "debug", "write a script", "implement"]
  reasoning:
    keywords: ["reason", "think step by step", "math", "calculate", "prove", "analyze"]
  summarization:
    keywords: ["summarize", "summary", "tldr", "shorten", "condense"]
  translation:
    keywords: ["translate", "in french", "in spanish", "in german", "en español"]
  embeddings:
    # identified by endpoint, not keywords — /v1/embeddings
  default: chat
```

---

## Model Capability Matrix (POC — Static Config)

```yaml
# model_matrix.yaml
models:
  llama3-8b:
    backend: ollama
    endpoint: "http://inference-ollama:11434"
    tasks: [chat, summarization, reasoning]
    health_url: "http://inference-ollama:11434/api/tags"
    fallback: mistral-7b

  mistral-7b:
    backend: ollama
    endpoint: "http://inference-ollama:11434"
    tasks: [chat, summarization, translation]
    health_url: "http://inference-ollama:11434/api/tags"
    fallback: null

  deepseek-coder:
    backend: ollama
    endpoint: "http://inference-ollama:11434"
    tasks: [code]
    health_url: "http://inference-ollama:11434/api/tags"
    fallback: llama3-8b

task_defaults:
  chat: llama3-8b
  code: deepseek-coder
  reasoning: llama3-8b
  summarization: mistral-7b
  translation: mistral-7b
  embeddings: llama3-8b
```

For POC, Ollama is the primary inference backend — it hosts multiple models on one node without GPU.

---

## IMF Fields This Layer Reads and Writes

**Reads:**
- `request.messages` — for task classification
- `request.model` — for pinned mode
- `user.department` — for policy check

**Writes:**
```json
{
  "request": {
    "task_type": "chat"
  },
  "routing": {
    "selected_model": "llama3-8b",
    "routing_mode": "auto",
    "fallback_level": 0
  },
  "cache": {
    "lookup_hit": false,
    "cache_key": "sha256-hash"
  }
}
```

---

## Helm Chart: `llm-platform/charts/router/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/router
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8082

env:
  LOG_LEVEL: "INFO"
  MODEL_MATRIX_PATH: "/config/model_matrix.yaml"
  TASK_RULES_PATH: "/config/task_classifier_rules.yaml"
  CACHE_URL: "http://cache:8086"
  INFERENCE_TIMEOUT_SECONDS: "120"

configMaps:
  - name: model-matrix
    mountPath: /config/model_matrix.yaml
  - name: task-rules
    mountPath: /config/task_classifier_rules.yaml

resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"

autoscaling:
  enabled: false

vault:
  enabled: false
```

---

## Observability (POC)

- Structured JSON logs to stdout.
- Log per request: `request_id`, `task_type`, `selected_model`, `routing_mode`, `cache_hit`, `fallback_level`, `latency_ms`.
- No OTel tracing for POC.

---

## Audit Events (POC)

Log to stdout:
- `routing_decision` — include `task_type`, `selected_model`, `routing_mode`
- `cache_hit` — when cache returns a response
- `routing_fallback` — when primary model is unhealthy

---

## POC Non-Goals (Explicitly Out of Scope)

- Prometheus GPU availability probe
- ML-based task classifier
- Scoring formula (cost/latency/GPU headroom)
- A/B testing engine
- Circuit breaker with Redis state
- OPA policy queries for routing
- MLflow integration for capability matrix
- gRPC transport / mTLS
- Multi-instance load balancing
