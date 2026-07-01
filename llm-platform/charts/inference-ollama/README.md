# inference-ollama

Helm chart for **Inference Layer (Layer 5)** of the Enterprise On-Premises LLM Platform.

Packages two tightly coupled components into a single deployable unit:

- **Ollama** — the inference engine (port 11434). Loads GGUF model weights from a PersistentVolumeClaim and serves the native `/api/chat` HTTP API.
- **Inference Adapter** — a lightweight FastAPI service (port 8087) that translates incoming IMF requests into Ollama's wire format, dispatches the call, maps the response back to IMF, and returns it to the Router. Prometheus metrics are exposed on port 9090.

---

## Architecture Overview

```
Router
  │
  │  POST /infer  (IMF document)
  ▼
inference-adapter  :8087
  │  translate IMF → Ollama /api/chat
  │  POST http://inference-ollama:11434/api/chat
  │  map Ollama response → IMF response block
  ▼
inference-ollama   :11434
  │
  └── PVC /root/.ollama  (model weights, 20 Gi)

Prometheus scrapes inference-adapter :9090/metrics  every 30s
```

Two separate Kubernetes Deployments share the same release. A Helm init Job pre-pulls the configured model list into the PVC before Ollama becomes Ready, so the first inference request is not delayed by a model download.

---

## Prerequisites

- Kubernetes 1.26+
- Helm 3.10+
- A default StorageClass that supports `ReadWriteOnce` (or set `persistence.storageClass`)
- Prometheus Operator installed (for `ServiceMonitor` CRD)
- The Inference Adapter image published to `adapter.image.repository` (or override the tag to `latest` for local testing)

---

## Deploy

```bash
helm upgrade --install inference-ollama ./llm-platform/charts/inference-ollama \
  --namespace llm-platform \
  --create-namespace \
  --set adapter.image.tag=<sha>
```

To override models or storage class:

```bash
helm upgrade --install inference-ollama ./llm-platform/charts/inference-ollama \
  --namespace llm-platform \
  --create-namespace \
  --set adapter.image.tag=<sha> \
  --set persistence.storageClass=fast-ssd \
  --set models.preload[0]=llama3.2:3b
```

---

## Model Preloading

The chart includes a post-install/post-upgrade `Job` (`initJob.enabled: true`) that calls `POST http://inference-ollama:11434/api/pull` for each model in `models.preload`.

- Models are pulled **sequentially** with a per-model timeout of `initJob.pullTimeoutSeconds` (default 600 s).
- If a model is already present in the PVC, Ollama skips the download and responds immediately.
- On any pull failure the Job exits non-zero, emits a structured JSON log event (`model_pull_failed`), and Kubernetes retries up to `backoffLimit: 3` times.
- Set `initJob.enabled: false` to skip pre-pull (useful when the PVC is pre-populated or in air-gapped environments).

---

## Persistence

Model weights are stored in a `PersistentVolumeClaim` named `ollama-data` mounted at `/root/.ollama` inside the Ollama container. The PVC survives pod restarts — models do not need to be re-pulled unless the PVC is deleted.

| Setting | Default | Notes |
|---|---|---|
| `persistence.enabled` | `true` | Set `false` to use `emptyDir` (weights lost on restart) |
| `persistence.size` | `20Gi` | Increase if loading multiple large models |
| `persistence.storageClass` | `""` | Empty = cluster default StorageClass |
| `persistence.mountPath` | `/root/.ollama` | Ollama model store path |

---

## Values Reference

| Key | Default | Description |
|---|---|---|
| `replicaCount` | `1` | Number of replicas for both Deployments (POC: always 1) |
| **Ollama** | | |
| `ollama.image.repository` | `ollama/ollama` | Ollama container image repository |
| `ollama.image.tag` | `latest` | Ollama image tag |
| `ollama.image.pullPolicy` | `IfNotPresent` | Image pull policy |
| `ollama.service.port` | `11434` | Ollama ClusterIP service port |
| `ollama.resources.requests.cpu` | `"1"` | Ollama CPU request |
| `ollama.resources.requests.memory` | `8Gi` | Ollama memory request |
| `ollama.resources.limits.cpu` | `"4"` | Ollama CPU limit |
| `ollama.resources.limits.memory` | `16Gi` | Ollama memory limit |
| `ollama.env.OLLAMA_HOST` | `0.0.0.0` | Bind address — must be `0.0.0.0` for in-cluster access |
| `ollama.env.OLLAMA_KEEP_ALIVE` | `24h` | Duration loaded models remain resident in memory |
| **Inference Adapter** | | |
| `adapter.image.repository` | `registry.internal/inference-adapter` | Adapter image repository |
| `adapter.image.tag` | `""` | Adapter image tag; falls back to `latest` when empty |
| `adapter.image.pullPolicy` | `IfNotPresent` | Image pull policy |
| `adapter.service.port` | `8087` | Adapter HTTP ClusterIP service port |
| `adapter.metricsPort` | `9090` | Prometheus metrics port |
| `adapter.env.DEFAULT_MODEL` | `llama3.2:3b` | Default model when IMF omits `routing.selected_model` |
| `adapter.env.LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `adapter.env.OLLAMA_BASE_URL` | `http://inference-ollama:11434` | Base URL for Ollama API calls |
| `adapter.env.OLLAMA_TIMEOUT_SECONDS` | `120` | Per-request timeout for Ollama calls (1–600 s) |
| `adapter.env.DEFAULT_MAX_TOKENS` | `2048` | Default `num_predict` when IMF omits `max_tokens` |
| `adapter.env.MAX_TOKENS_LIMIT` | `4096` | Hard ceiling for `num_predict`; requests above this are clamped |
| `adapter.env.DEFAULT_TEMPERATURE` | `0.7` | Default `temperature` when IMF omits `request.temperature` |
| **Models** | | |
| `models.preload` | `["llama3.2:3b"]` | Models pulled by the init Job before Ollama becomes Ready |
| **Persistence** | | |
| `persistence.enabled` | `true` | Create and mount PVC for Ollama model storage |
| `persistence.size` | `20Gi` | PVC size |
| `persistence.storageClass` | `""` | StorageClass name; empty = cluster default |
| `persistence.mountPath` | `/root/.ollama` | Mount path inside the Ollama container |
| **Init Job** | | |
| `initJob.enabled` | `true` | Run model pre-pull Job on install/upgrade |
| `initJob.image` | `curlimages/curl:latest` | Image used by the init Job shell script |
| `initJob.pullTimeoutSeconds` | `600` | Per-model curl timeout in seconds |
| **Other** | | |
| `autoscaling.enabled` | `false` | HPA disabled for POC |
| `vault.enabled` | `false` | HashiCorp Vault injection disabled (Phase 2) |

---

## Networking

A `NetworkPolicy` restricts traffic to the Inference Adapter:

- **Ingress** — only pods labelled `app.kubernetes.io/name: router` may reach port 8087.
- **Egress** — only pods labelled `app.kubernetes.io/name: inference-ollama` are reachable on port 11434.

Prometheus scraping (port 9090) is handled separately via the `ServiceMonitor`.

---

## Observability

A `ServiceMonitor` (requires Prometheus Operator) configures Prometheus to scrape `/metrics` on the Adapter's `metrics` port (9090) every 30 s.

Metrics exposed:

| Metric | Type | Labels |
|---|---|---|
| `llm_inference_requests_total` | Counter | `status`, `model`, `task_type`, `department` |
| `llm_inference_latency_seconds` | Histogram | `model`, `task_type`, `department` |
| `llm_inference_errors_total` | Counter | `error_code`, `model`, `department` |
