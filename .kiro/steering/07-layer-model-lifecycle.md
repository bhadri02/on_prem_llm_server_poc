---
inclusion: manual
---

# Layer 7 — Model Lifecycle Management (POC)

> Load this file when working on Model Lifecycle: `#07-layer-model-lifecycle`
> **Scope:** Proof-of-Concept — a lightweight model registry that the Router can query.

---

## POC Goal

Provide a simple registry that stores model metadata (name, capabilities, backend endpoint, status) and exposes a REST API for the Router to fetch the capability matrix. Show that model information is centralised and not hardcoded in each service.

---

## Components to Build (POC Scope)

| Component | Technology | POC Simplification |
|---|---|---|
| Model Registry | FastAPI + SQLite (or JSON file) | No MLflow; simple file-backed store |
| Registry API | FastAPI | CRUD endpoints for model metadata |
| Model Status | Manual via API | No Argo Rollouts; no automated canary |
| Version Tracking | Single `version` string field | No semantic versioning enforcement |
| Weight Storage | Ollama handles weights | No MinIO/NFS for POC; Ollama pulls from internet |
| Health Monitor | **Skip for POC** | No scheduled benchmark jobs |
| Update Automation | **Skip for POC** | No Kubernetes Operator |
| A/B Testing | **Skip for POC** | Not implemented |

---

## Model Metadata Schema (POC)

```json
{
  "name": "llama3.2-3b",
  "version": "1.0.0",
  "backend": "ollama",
  "endpoint": "http://inference-ollama:11434",
  "tasks": ["chat", "summarization", "reasoning", "code"],
  "status": "active",
  "vram_required_gb": 3,
  "max_context_length": 8192,
  "fallback_model": null,
  "registered_at": "ISO-8601",
  "notes": "POC primary model — small CPU-capable model"
}
```

---

## Registry API Endpoints (POC)

```
GET    /models                      # list all models
GET    /models/{name}               # get model by name
POST   /models                      # register a new model
PATCH  /models/{name}/status        # update status: active | retired | staging
GET    /models/by-task/{task_type}  # list models capable of this task (used by Router)
GET    /health
```

### Example: Router fetches models for a task
```
GET /models/by-task/chat
→ [{ "name": "llama3.2-3b", "backend": "ollama", "endpoint": "...", "status": "active" }]
```

---

## Storage (POC)

Use a JSON file (`models.json`) on a PersistentVolume, or SQLite. FastAPI reads/writes this file. No MLflow dependency.

```json
// models.json
[
  {
    "name": "llama3.2-3b",
    "version": "1.0.0",
    "backend": "ollama",
    "endpoint": "http://inference-ollama:11434",
    "tasks": ["chat", "summarization", "reasoning", "code"],
    "status": "active",
    "fallback_model": null
  }
]
```

---

## Router Integration (POC)

The Router polls the Model Registry at startup and every 60 seconds:

```
GET http://model-registry:5000/models
```

Uses the response to build its in-memory capability matrix instead of reading from a static YAML file. This demonstrates the Router–Registry contract.

If the Model Registry is unreachable, the Router falls back to its local static YAML config.

---

## Helm Chart: `llm-platform/charts/model-registry/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: registry.local/model-registry
  tag: ""
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 5000

env:
  LOG_LEVEL: "INFO"
  STORAGE_PATH: "/data/models.json"

persistence:
  enabled: true
  size: 1Gi

resources:
  requests:
    cpu: "100m"
    memory: "128Mi"
  limits:
    cpu: "300m"
    memory: "256Mi"

autoscaling:
  enabled: false

vault:
  enabled: false
```

---

## Observability (POC)

- JSON logs to stdout.
- Log each API call: `method`, `path`, `status_code`, `latency_ms`.

---

## POC Non-Goals (Explicitly Out of Scope)

- MLflow (tracking server, artifact store, experiment tracking)
- MinIO / NFS model weight storage
- Semantic versioning enforcement
- Canary deployment (Argo Rollouts)
- A/B testing configuration
- Automated health benchmark jobs
- Kubernetes Operator for automated redeployment
- Cache invalidation event publishing
