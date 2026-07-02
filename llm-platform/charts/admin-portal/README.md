# admin-portal

Admin Portal — web UI for platform administration.

## Overview

The Admin Portal provides a browser-based interface for managing the LLM platform: model lifecycle, user/department configuration, policy review, audit log inspection, and system health dashboards.

## Ports

| Name    | Port | Description                          |
|---------|------|--------------------------------------|
| http    | 8084 | Admin Portal HTTP service            |
| metrics | 9090 | Prometheus metrics endpoint          |

## Service Discovery

| Context              | URL                          |
|----------------------|------------------------------|
| Within namespace     | `http://admin-portal:8084`   |
| Ingress (when enabled) | `http://llm-portal.local`  |

## Docker Build

```bash
docker build -t registry.local/admin-portal:dev ./admin_portal
```

## Values Reference

| Key | Description | Default |
|-----|-------------|---------|
| `replicaCount` | Number of pod replicas | `1` |
| `image.repository` | Container image repository | `registry.local/admin-portal` |
| `image.tag` | Image tag (empty = `latest`) | `""` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | HTTP service port | `8084` |
| `metricsPort` | Prometheus metrics port | `9090` |
| `secretRef.name` | Kubernetes Secret name for envFrom | `llm-poc-secrets` |
| `serviceAccount.name` | Service account name | `llm-platform` |
| `resources.requests.cpu` | CPU request | `100m` |
| `resources.requests.memory` | Memory request | `256Mi` |
| `resources.limits.cpu` | CPU limit | `1` |
| `resources.limits.memory` | Memory limit | `1Gi` |
| `autoscaling.enabled` | Enable HPA | `false` |
| `vault.enabled` | Enable Vault agent sidecar | `false` |
| `livenessProbe` | Liveness probe config (httpGet /health on 8084) | see values.yaml |
| `readinessProbe` | Readiness probe config (httpGet /health on 8084) | see values.yaml |
| `ingress.enabled` | Enable Ingress resource | `false` |
| `ingress.host` | Ingress hostname | `llm-portal.local` |
| `ingress.servicePort` | Ingress backend service port | `8084` |
| `env` | Additional environment variables (key/value map) | `{}` |
| `persistence.enabled` | Enable persistent storage | `false` |
