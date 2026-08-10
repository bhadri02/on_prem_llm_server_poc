# cache

Cache Layer (Layer 4) — exact and semantic caching for the LLM platform.

This chart deploys the `cache-service` FastAPI application alongside a single-instance Redis (via the Bitnami sub-chart). The cache layer sits between the Intelligent Router and the Inference Layer. It eliminates redundant inference calls by returning previously computed responses for identical or semantically similar prompts using two strategies:

- **Exact-match caching** — SHA-256 keyed Redis entries for byte-identical requests, with per-task-type TTLs.
- **Semantic caching** — Cosine-similarity scan over `all-MiniLM-L6-v2` sentence-transformer embeddings stored as JSON lists in Redis.

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+
- Prometheus Operator (for `ServiceMonitor` CRD)

## Dependency Setup

Fetch the Bitnami Redis sub-chart before installing:

```bash
helm dependency update llm-platform/charts/cache
```

## Installation

```bash
helm install cache llm-platform/charts/cache \
  --namespace llm-platform \
  --create-namespace \
  --set image.tag=<sha>
```

To override the Redis URL (e.g. when the release name differs from `cache`):

```bash
helm install cache llm-platform/charts/cache \
  --namespace llm-platform \
  --set image.tag=<sha> \
  --set env.REDIS_URL="redis://<release-name>-redis-master:6379"
```

## Values Reference

| Key | Default | Description |
|-----|---------|-------------|
| `replicaCount` | `1` | Number of cache-service pod replicas (POC: single replica) |
| `image.repository` | `registry.local/cache-service` | Container image repository |
| `image.tag` | `""` | Image tag; set via CI `--set image.tag=<sha>`; falls back to `latest` |
| `image.pullPolicy` | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `8086` | HTTP service port |
| `metricsPort` | `9090` | Prometheus metrics port |
| `env.LOG_LEVEL` | `"INFO"` | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `env.REDIS_URL` | `"redis://redis-master:6379"` | Redis connection URL; override if release name differs |
| `env.SIMILARITY_THRESHOLD` | `"0.90"` | Minimum cosine similarity for a semantic cache hit (0.0–1.0) |
| `env.EMBEDDING_MODEL` | `"all-MiniLM-L6-v2"` | Sentence-transformers model name for embedding generation |
| `env.HF_HUB_OFFLINE` | `"1"` | Forces the embedding model to load from the image's local HF cache only — never phones home to the Hub. The model must be pre-fetched into the image at build time. |
| `env.TRANSFORMERS_OFFLINE` | `"1"` | Same offline guarantee, for the underlying `transformers` library |
| `env.MAX_SEMANTIC_ENTRIES` | `"500"` | Maximum entries per task-type in the semantic cache list |
| `env.CACHE_TTL_SECONDS` | `"60"` | Cache TTL (seconds), uniform across every `task_type` — applies to both the exact-match and semantic caches; a hit older than this is treated as a miss |
| `redis.enabled` | `true` | Deploy the Bitnami Redis sub-chart |
| `redis.architecture` | `standalone` | Redis deployment mode (POC: single instance) |
| `redis.auth.enabled` | `false` | Redis authentication (disabled for POC) |
| `redis.master.persistence.enabled` | `true` | Enable Redis data persistence |
| `redis.master.persistence.size` | `5Gi` | Redis PVC size |
| `resources.requests.cpu` | `"200m"` | CPU request for cache-service |
| `resources.requests.memory` | `"512Mi"` | Memory request for cache-service |
| `resources.limits.cpu` | `"1"` | CPU limit for cache-service |
| `resources.limits.memory` | `"1Gi"` | Memory limit for cache-service |
| `autoscaling.enabled` | `false` | Horizontal Pod Autoscaling (disabled for POC) |
| `vault.enabled` | `false` | HashiCorp Vault integration (Phase 2) |
| `livenessProbe.httpGet.path` | `/health` | Liveness probe HTTP path |
| `livenessProbe.httpGet.port` | `8086` | Liveness probe port |
| `livenessProbe.initialDelaySeconds` | `15` | Seconds before first liveness check |
| `livenessProbe.periodSeconds` | `15` | Liveness check interval |
| `livenessProbe.timeoutSeconds` | `2` | Liveness probe timeout |
| `livenessProbe.failureThreshold` | `3` | Consecutive failures before pod is restarted |
| `readinessProbe.httpGet.path` | `/health` | Readiness probe HTTP path |
| `readinessProbe.httpGet.port` | `8086` | Readiness probe port |
| `readinessProbe.initialDelaySeconds` | `15` | Seconds before first readiness check |
| `readinessProbe.periodSeconds` | `15` | Readiness check interval |
| `readinessProbe.timeoutSeconds` | `2` | Readiness probe timeout |
| `readinessProbe.failureThreshold` | `3` | Consecutive failures before pod is removed from endpoints |

## Prometheus Metrics

The service exposes the following metrics on port `9090` at `/metrics`:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_cache_requests_total` | Counter | `status`, `cache_type`, `task_type` | Total cache lookup requests |
| `llm_cache_latency_seconds` | Histogram | `operation`, `task_type` | End-to-end handler latency |
| `llm_cache_errors_total` | Counter | `error_code`, `operation` | Redis and embedding failures |
| `llm_cache_semantic_entries` | Gauge | `task_type` | Current semantic cache list length |

## Network Policy

The deployed `NetworkPolicy` restricts traffic as follows:

- **Ingress:** only from pods with `app.kubernetes.io/name: router` in the `llm-platform` namespace on port `8086`.
- **Egress:** only to pods with `app.kubernetes.io/name: redis` in the `llm-platform` namespace on port `6379`.
