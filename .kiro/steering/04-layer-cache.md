---
inclusion: manual
---

# Layer 4 — Cache Layer (POC)

> Load this file when working on the Cache layer: `#04-layer-cache`
> **Scope:** Proof-of-Concept — demonstrate cache hit/miss flow, not production-scale caching.

---

## POC Goal

Show that identical or very similar prompts return a cached response without hitting inference. Implement exact-match caching with Redis and a simple semantic similarity check using a lightweight embedding model. Prove the cache lookup and cache write flows work in the request pipeline.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Exact Response Cache | Redis (single instance) | No Sentinel/Cluster; single Redis pod |
| Semantic Cache | Redis + `sentence-transformers` | Use `all-MiniLM-L6-v2` CPU-only; store vectors in Redis as JSON (no Milvus) |
| Cache API | FastAPI (Python) | Simple REST service; not gRPC |
| Embedding Generator | `sentence-transformers` (CPU) | `all-MiniLM-L6-v2`; no BGE-M3 |
| Cache Invalidation | TTL-based only | No model-version event subscription |

> **POC Trade-off:** Vectors stored in Redis as JSON list instead of Milvus. This means ANN search is a linear scan — fine for POC (<1000 entries), not for production.

---

## Cache Lookup Flow (POC)

```
Receive lookup request from Router (HTTP JSON)
  │
  ├─ 1. EXACT MATCH (Redis)
  │       Key = SHA256(messages_text + model + task_type)
  │       HIT  → return cached response JSON
  │       MISS → continue
  │
  ├─ 2. SEMANTIC MATCH (Redis vector scan)
  │       → Generate embedding of prompt (sentence-transformers)
  │       → Load all stored vectors from Redis key "semantic_cache:{task_type}"
  │       → Compute cosine similarity for each
  │       → If best match ≥ threshold → return cached response
  │       MISS → return miss signal
  │
  └─ Return: { "hit": true/false, "cache_key": "...", "response": <IMF response> | null }
```

---

## Cache Write Flow (POC)

```
Receive write request from Router (HTTP JSON with IMF response)
  │
  ├─ 1. Write exact cache entry (Redis SET with TTL)
  │
  ├─ 2. Generate embedding of prompt
  │
  └─ 3. Append { "embedding": [...], "response": <IMF response>, "key": "..." }
         to Redis list "semantic_cache:{task_type}"
```

---

## Cache Key (POC)

```python
import hashlib, json

def make_cache_key(messages: list, model: str, task_type: str) -> str:
    content = " ".join(m["content"] for m in messages).lower().strip()
    raw = f"{content}|{model}|{task_type}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

---

## Similarity Threshold (POC)

```yaml
semanticCache:
  similarityThreshold: 0.90    # cosine similarity; 1.0 = identical
  maxEntries: 500              # stop storing new entries after this (memory guard)

exactCache:
  ttlSeconds:
    chat: 3600
    code: 7200
    summarization: 86400
    default: 3600
```

---

## Redis Data Structure (POC)

```
# Exact cache
Key:   exact:{sha256-hash}
Value: serialized IMF response JSON
TTL:   per task_type

# Semantic cache (per task_type)
Key:   semantic_cache:chat       → Redis List of JSON objects
       semantic_cache:code
       semantic_cache:summarization
Each entry: { "key": "sha256", "embedding": [0.1, 0.2, ...], "response": {...} }
```

---

## IMF Fields This Layer Reads and Writes

**Reads:**
- `request.messages`, `routing.selected_model`, `request.task_type`
- `response.*` — on write

**Writes:**
```json
{
  "cache": {
    "lookup_hit": true,
    "cache_key": "sha256-hash",
    "cache_type": "exact | semantic",
    "similarity_score": 0.93
  }
}
```

---

## Helm Chart: `llm-platform/charts/cache/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/cache-service
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8086

env:
  LOG_LEVEL: "INFO"
  REDIS_URL: "redis://redis:6379"
  SIMILARITY_THRESHOLD: "0.90"
  EMBEDDING_MODEL: "all-MiniLM-L6-v2"
  MAX_SEMANTIC_ENTRIES: "500"

resources:
  requests:
    cpu: "200m"
    memory: "512Mi"
  limits:
    cpu: "1"
    memory: "1Gi"

autoscaling:
  enabled: false

vault:
  enabled: false

# Redis sub-chart (single instance)
redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false    # no auth for POC
  master:
    persistence:
      enabled: true
      size: 5Gi
```

---

## Observability (POC)

- Structured JSON logs to stdout.
- Log per request: `request_id`, `cache_type`, `hit`, `similarity_score` (if semantic), `latency_ms`.
- No OTel tracing for POC.

---

## Audit Events (POC)

Log to stdout:
- `cache_hit` — include `cache_type` and `similarity_score`
- `cache_miss`
- `cache_write`

---

## POC Non-Goals (Explicitly Out of Scope)

- Milvus / Qdrant vector database
- Redis Sentinel or Cluster mode
- Department-namespaced cache isolation
- Model-version event-driven invalidation
- KV cache prefix sharing with vLLM
- Embedding cache for document chunks
- gRPC transport
- Production-scale ANN search
