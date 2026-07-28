# Observability Stack

Observability stack sub-chart for the Enterprise On-Prem LLM Platform POC. Wraps
`kube-prometheus-stack` (Prometheus + Grafana + Alertmanager) and provides an optional
Jaeger all-in-one deployment for distributed tracing.

---

## Service Purpose

| Component  | Description                                                                 |
|------------|-----------------------------------------------------------------------------|
| Prometheus | Metrics collection — scrapes all platform services via ServiceMonitor CRDs  |
| Grafana    | Metrics dashboards — pre-wired to Prometheus as a datasource                |
| Jaeger     | Distributed tracing UI (optional — disabled by default for POC)             |

---

## Prerequisites

Before installing, add the prometheus-community Helm repo and **run `helm dep update`** to fetch dependencies:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
cd llm-platform/charts/observability
helm dependency update
```

**Note:** After changing the `kube-prometheus-stack` version in `Chart.yaml`, you **must** run `helm dep update` to download the pinned version and regenerate `Chart.lock`.

---

## Deployment

This chart is deployed into the `monitoring` namespace separately from the main platform:

```bash
helm upgrade --install observability ./llm-platform/charts/observability \
  --namespace monitoring \
  --create-namespace
```

To enable the NGINX ingress for Grafana:

```bash
helm upgrade --install observability ./llm-platform/charts/observability \
  --namespace monitoring \
  --create-namespace \
  --set ingress.enabled=true
```

---

## Access URLs

### Grafana

- **Ingress (when `ingress.enabled: true`):** `http://grafana-poc.local`
  Add `<cluster-ip>  grafana-poc.local` to `/etc/hosts` before accessing.
- **In-cluster:** `http://observability-grafana:3000`
- **Admin credentials:** username `admin`, password set via `kube-prometheus-stack.grafana.adminPassword` (default: `poc-admin`)

### Prometheus

- **In-cluster:** `http://observability-kube-prometheus-prometheus:9090`

### Jaeger (when `jaeger.enabled: true`)

- **In-cluster:** `http://observability-jaeger:16686`

---

## Grafana Admin Password

The Grafana admin password is set via the `kube-prometheus-stack` sub-chart value:

```yaml
kube-prometheus-stack:
  grafana:
    adminPassword: "poc-admin"
```

Change this value at deploy time via `--set kube-prometheus-stack.grafana.adminPassword=<password>`.
**Do not commit real passwords to version-controlled values files.**

---

## Values Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `replicaCount` | int | `1` | Number of replicas (POC: single replica) |
| `kubePrometheusStack.enabled` | bool | `true` | Enable/disable the kube-prometheus-stack dependency |
| `kube-prometheus-stack.prometheus.prometheusSpec.retention` | string | `"7d"` | Prometheus metrics retention period |
| `kube-prometheus-stack.prometheus.prometheusSpec.replicas` | int | `1` | Prometheus replicas (POC: single replica) |
| `kube-prometheus-stack.prometheus.prometheusSpec.serviceMonitorSelector.matchLabels.release` | string | `"observability"` | ServiceMonitor discovery label |
| `kube-prometheus-stack.prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage` | string | `"10Gi"` | Prometheus PVC storage size |
| `kube-prometheus-stack.grafana.enabled` | bool | `true` | Enable Grafana |
| `kube-prometheus-stack.grafana.replicas` | int | `1` | Grafana replicas (POC: single replica) |
| `kube-prometheus-stack.grafana.adminPassword` | string | `"poc-admin"` | Grafana admin user password |
| `kube-prometheus-stack.grafana.service.type` | string | `ClusterIP` | Grafana Service type |
| `kube-prometheus-stack.grafana.service.port` | int | `3000` | Grafana Service port |
| `kube-prometheus-stack.grafana.sidecar.datasources.enabled` | bool | `true` | Auto-register Prometheus as a Grafana datasource |
| `kube-prometheus-stack.grafana.sidecar.dashboards.enabled` | bool | `true` | Enable dashboard sidecar discovery |
| `kube-prometheus-stack.grafana.sidecar.dashboards.label` | string | `"grafana_dashboard"` | ConfigMap label for dashboard discovery |
| `kube-prometheus-stack.grafana.dashboardsConfigMaps` | list | See values.yaml | ConfigMaps containing dashboard JSON |
| `kube-prometheus-stack.alertmanager.enabled` | bool | `false` | Enable/disable Alertmanager (disabled for POC) |
| `opentelemetry-collector.enabled` | bool | `false` | Deploy OTel Collector (opt-in for tracing) |
| `jaeger.enabled` | bool | `false` | Deploy Jaeger all-in-one (ports 16686/TCP, 6831/UDP, 14268/TCP) |
| `ingress.enabled` | bool | `false` | Create an NGINX Ingress for Grafana |
| `ingress.host` | string | `"grafana-poc.local"` | Ingress hostname for Grafana |
| `ingress.servicePort` | int | `3000` | Backend service port for the Ingress |
| `observability.metrics.port` | int | `9090` | Metrics port exposed by platform services |

---

## POC Scope

This chart follows POC constraints:

- **Single replica** for Prometheus and Grafana (no HA)
- **7-day retention** for metrics (no long-term storage)
- **10Gi PVC** for Prometheus storage
- **Alertmanager disabled** (no PagerDuty/Slack integration)
- **Tracing disabled by default** (OTel/Jaeger opt-in)
- **ClusterIP services** (use `kubectl port-forward` for external access)
