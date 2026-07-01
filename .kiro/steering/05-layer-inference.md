---
inclusion: manual
---

# Layer 5 — Inference Layer (POC)

> Load this file when working on the Inference layer: `#05-layer-inference`
> **Scope:** Proof-of-Concept — one working inference backend, functional end-to-end.

---

## POC Goal

Run at least one LLM locally inside Kubernetes and serve responses through the platform. Use **Ollama** as the primary (and only required) POC backend — it runs on CPU, is easy to deploy, and supports multiple models via GGUF. vLLM is optional if a GPU node is available.

---

## POC Backend Choice

| Backend | Required for POC | Notes |
|---|---|---|
| **Ollama** | **Yes (primary)** | CPU-capable, easy model pull, GGUF support |
| vLLM | Optional | Only if GPU node available; use for perf comparison |
| TGI / Triton / llama.cpp | No | Out of scope for POC |

---

## Ollama Deployment (POC)

Ollama is deployed as a Kubernetes Deployment with a persistent volume for model storage.

**Models to load for POC (choose based on available RAM):**

| Model | RAM Required | Use Case |
|---|---|---|
| `llama3.2:3b` | ~3 GB | Low-resource POC (selected) |

Pull models via Ollama init container or post-deploy job.

---

## Internal Inference API (POC)

The Router calls Ollama's native HTTP API directly. An adapter is needed to translate IMF → Ollama format and back.

**Inference Adapter** (lightweight Python/FastAPI service or middleware in the Router):

```
IMF request.messages + request.* 
    → Ollama /api/chat format
    ← Ollama response
    → IMF response block
```

**Ollama API call (from the adapter):**
```http
POST http://inference-ollama:11434/api/chat
Content-Type: application/json

{
  "model": "llama3.2:3b",
  "messages": [...],
  "stream": false,
  "options": {
    "num_predict": 2048,
    "temperature": 0.7
  }
}
```

**Map Ollama response back to IMF:**
```json
{
  "response": {
    "content": "<ollama message.content>",
    "finish_reason": "stop",
    "usage": {
      "prompt_tokens": "<ollama eval_count>",
      "completion_tokens": "<ollama prompt_eval_count>",
      "total_tokens": "<sum>"
    }
  },
  "metadata": {
    "inference_backend": "ollama",
    "inference_latency_ms": "<total_duration in ns / 1e6>"
  }
}
```

---

## Health Endpoint (POC)

The Router checks Ollama health via:
```
GET http://inference-ollama:11434/api/tags
```
Returns `200` with list of loaded models when healthy.

The Router should verify the target model name appears in the response before routing to it.

---

## Streaming (POC)

- Ollama supports streaming natively (`"stream": true`).
- For POC, streaming is optional — non-streaming mode is acceptable.
- If implementing streaming: proxy the Ollama `data:` chunks as SSE back to the client.

---

## Model Storage (POC)

- Use a Kubernetes `PersistentVolumeClaim` for Ollama model storage.
- Ollama pulls models on first use (internet access needed during setup, or pre-pull in init container).
- For air-gapped POC: pre-load GGUF files via init container from local storage.

```yaml
# PVC for Ollama models
persistence:
  enabled: true
  size: 20Gi
  storageClass: ""   # use cluster default for POC
  mountPath: /root/.ollama
```

---

## Helm Chart: `llm-platform/charts/inference-ollama/`

```yaml
# values.yaml (POC)
replicaCount: 1

image:
  repository: ollama/ollama
  tag: "latest"
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 11434

models:
  preload:
    - llama3.2:3b

persistence:
  enabled: true
  size: 20Gi

env:
  OLLAMA_HOST: "0.0.0.0"
  OLLAMA_KEEP_ALIVE: "24h"   # keep models in memory

resources:
  requests:
    cpu: "1"
    memory: "8Gi"
  limits:
    cpu: "4"
    memory: "16Gi"
  # GPU (optional for POC):
  # limits:
  #   nvidia.com/gpu: 1

autoscaling:
  enabled: false

vault:
  enabled: false

# Model pre-pull init job
initJob:
  enabled: true
  image: curlimages/curl:latest
```

---

## Optional: vLLM Deployment (POC — GPU Only)

Only deploy if a GPU node with ≥16 GB VRAM is available.

```yaml
# values.yaml for inference-vllm (POC)
replicaCount: 1

image:
  repository: vllm/vllm-openai
  tag: "latest"

model:
  name: "meta-llama/Llama-3.2-3B-Instruct"
  path: "/models/llama3.2-3b"

env:
  VLLM_MODEL: "/models/llama3.2-3b"
  MAX_MODEL_LEN: "4096"
  GPU_MEMORY_UTILIZATION: "0.85"

service:
  port: 8000

resources:
  requests:
    cpu: "2"
    memory: "16Gi"
  limits:
    cpu: "4"
    memory: "24Gi"
    nvidia.com/gpu: 1

nodeSelector:
  gpu: "true"

tolerations:
  - key: gpu
    operator: Equal
    value: "true"
    effect: NoSchedule
```

vLLM exposes an OpenAI-compatible API at `/v1/chat/completions` — the Router adapter can call it directly without a translation layer.

---

## Observability (POC)

- Structured JSON logs from Ollama go to stdout (Kubernetes captures them).
- Log per inference call: `request_id`, `model`, `prompt_tokens`, `completion_tokens`, `latency_ms`.
- No OTel tracing for POC.

---

## Audit Events (POC)

Log to stdout (from the inference adapter):
- `inference_start` — model name, request_id
- `inference_complete` — token counts, latency_ms

---

## POC Non-Goals (Explicitly Out of Scope)

- TGI, Triton, llama.cpp backends
- Multi-GPU tensor parallelism
- MIG (Multi-Instance GPU) configuration
- DCGM Exporter for GPU metrics
- NFS shared model storage across pods
- KV cache prefix sharing
- Multiple inference replicas / load balancing
- Model weight version management
