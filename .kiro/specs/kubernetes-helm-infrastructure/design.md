# Design Document

## Kubernetes & Helm Infrastructure Layer

**Feature:** kubernetes-helm-infrastructure
**Spec Type:** feature | requirements-first

---

## Overview

This document specifies the technical design for packaging and deploying the entire Enterprise On-Premises LLM Platform as a Helm-managed Kubernetes workload. The infrastructure layer is responsible for:

- Completing the four partially-built sub-charts (`audit-store`, `cache`, `inference-ollama`, `model-registry`)
- Scaffolding the six missing sub-charts (`api-gateway`, `security-layer`, `router`, `agent-framework`, `admin-portal`, `observability`)
- Creating the umbrella chart at `llm-platform/` that wires all ten sub-charts as conditional Helm dependencies
- Automating the full deployment sequence with `scripts/deploy.sh`
- Validating the running stack with `scripts/smoke-test.sh`

The POC target is a single-namespace (`llm-poc`) cluster running k3s, kind, or minikube. All production concerns — Istio, Vault, HPA, GitOps, mTLS — are explicitly out of scope and deferred to Phase 2.

---

## Architecture

### Platform Topology

All ten application services deploy into the `llm-poc` namespace. The observability sub-chart deploys into the `monitoring` namespace (kube-prometheus-stack default). NGINX Ingress Controller lives in `ingress-nginx`.

```
┌──────────────────────────────────────────────────────────────────┐
│  External / Host                                                 │
│  llm-poc.local → api-gateway:8080                                │
│  llm-portal.local → admin-portal:8084                           │
│  grafana-poc.local → observability-grafana:3000                  │
└─────────────────┬────────────────────────────────────────────────┘
                  │  NGINX Ingress  (ingress-nginx ns)
┌─────────────────▼────────────────────────────────────────────────┐
│  namespace: llm-poc                                              │
│                                                                  │
│  api-gateway:8080 → security-layer:8081 → router:8082            │
│       ↓                                       ↓                  │
│  audit-store:9200              cache:8086  inference-ollama:11434 │
│  model-registry:5000           inference-adapter:8087            │
│  agent-framework:8083          admin-portal:8084                 │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│  namespace: monitoring                                           │
│  prometheus:9090   grafana:3000   (jaeger:16686 optional)        │
└──────────────────────────────────────────────────────────────────┘
```

### Request Flow Through the Stack

```
Consumer
  └─► NGINX Ingress (llm-poc.local)
        └─► api-gateway:8080
              └─► security-layer:8081  (injection check, API key auth)
                    └─► router:8082  (model selection)
                          ├─► cache:8086  (lookup hit → return early)
                          └─► inference-adapter:8087
                                └─► inference-ollama:11434  (Ollama engine)
                          └─► agent-framework:8083  (agentic tasks)
                    └─► audit-store:9200  (append audit record)
```

### Umbrella Chart Dependency Tree

```
llm-platform/  (umbrella)
├── api-gateway          (condition: apiGateway.enabled)
├── security-layer       (condition: securityLayer.enabled)
├── router               (condition: router.enabled)
├── cache                (condition: cache.enabled)
│     └── redis 19.x     (bitnami sub-dependency, condition: redis.enabled)
├── inference-ollama     (condition: inferenceOllama.enabled)
├── agent-framework      (condition: agentFramework.enabled)
├── model-registry       (condition: modelRegistry.enabled)
├── audit-store          (condition: auditStore.enabled)
├── admin-portal         (condition: adminPortal.enabled)
└── observability        (condition: observability.enabled)
      └── kube-prometheus-stack ~58.x  (prometheus-community)
```

### Key Design Decisions

**1. Local chart references, not registry.** All ten sub-charts live under `llm-platform/charts/`. The umbrella chart's `Chart.yaml` references each at `repository: ""` (local file path), so `helm dependency update` copies `.tgz` archives from the charts directory into `llm-platform/charts/` without reaching an external registry.

**2. Condition key naming convention.** The camelCase condition key (e.g., `apiGateway.enabled`) is set at the top level of the values file, allowing the umbrella chart to gate entire sub-charts without sub-chart values prefix collisions.

**3. Single `envFrom.secretRef` pattern.** All pods reference `llm-poc-secrets` via `envFrom`, not per-key `secretKeyRef`. This keeps templates clean and allows new secret keys to be added without re-rendering charts.

**4. Observability namespace separation.** The `observability` sub-chart installs via `helm upgrade --install --namespace monitoring --create-namespace`. The deploy script handles this namespace split explicitly. ServiceMonitor resources in `llm-poc` namespace must use `namespaceSelector` to be discovered by the Prometheus in `monitoring`.

**5. `storageClass: ""` everywhere.** Using an empty string lets the cluster's default StorageClass provision PVCs (k3s `local-path`, kind `standard`, minikube `standard`) without any distribution-specific configuration.

---

## Components and Interfaces

### Umbrella Chart (`llm-platform/`)

**`Chart.yaml`**

```yaml
apiVersion: v2
name: llm-platform-poc
description: "Enterprise On-Prem LLM Platform — POC"
type: application
version: 0.1.0
appVersion: "0.1.0"

dependencies:
  - name: api-gateway
    version: "0.1.0"
    repository: "file://charts/api-gateway"
    condition: apiGateway.enabled
  - name: security-layer
    version: "0.1.0"
    repository: "file://charts/security-layer"
    condition: securityLayer.enabled
  - name: router
    version: "0.1.0"
    repository: "file://charts/router"
    condition: router.enabled
  - name: cache
    version: "0.1.0"
    repository: "file://charts/cache"
    condition: cache.enabled
  - name: inference-ollama
    version: "0.1.0"
    repository: "file://charts/inference-ollama"
    condition: inferenceOllama.enabled
  - name: agent-framework
    version: "0.1.0"
    repository: "file://charts/agent-framework"
    condition: agentFramework.enabled
  - name: model-registry
    version: "0.1.0"
    repository: "file://charts/model-registry"
    condition: modelRegistry.enabled
  - name: audit-store
    version: "0.1.0"
    repository: "file://charts/audit-store"
    condition: auditStore.enabled
  - name: admin-portal
    version: "0.1.0"
    repository: "file://charts/admin-portal"
    condition: adminPortal.enabled
  - name: observability
    version: "0.1.0"
    repository: "file://charts/observability"
    condition: observability.enabled
```

**`values.yaml`** (umbrella-level shared defaults)

```yaml
# Umbrella default — sub-charts inherit if they don't override
replicaCount: 1
image:
  pullPolicy: IfNotPresent
autoscaling:
  enabled: false
vault:
  enabled: false
secretRef:
  name: "llm-poc-secrets"
serviceAccount:
  name: "llm-platform"

# All sub-charts disabled by default; opt-in via values-poc.yaml
apiGateway:
  enabled: false
securityLayer:
  enabled: false
router:
  enabled: false
cache:
  enabled: false
inferenceOllama:
  enabled: false
agentFramework:
  enabled: false
modelRegistry:
  enabled: false
auditStore:
  enabled: false
adminPortal:
  enabled: false
observability:
  enabled: false
```

**`values-poc.yaml`** (full POC override — single authoritative file for POC installs)

```yaml
global:
  imageRegistry: registry.local

# Enable all layers
apiGateway:
  enabled: true
  ingress:
    enabled: true
securityLayer:
  enabled: true
router:
  enabled: true
cache:
  enabled: true
inferenceOllama:
  enabled: true
agentFramework:
  enabled: true
modelRegistry:
  enabled: true
auditStore:
  enabled: true
adminPortal:
  enabled: true
  ingress:
    enabled: true
observability:
  enabled: true
  ingress:
    enabled: true

# POC-wide settings
replicaCount: 1
autoscaling:
  enabled: false
vault:
  enabled: false

# Inference model preload
inferenceOllama:
  models:
    preload:
      - "llama3.2:3b"

# Service discovery (short-form DNS, all in llm-poc namespace)
services:
  apiGateway: "http://api-gateway:8080"
  securityLayer: "http://security-layer:8081"
  router: "http://router:8082"
  cache: "http://cache:8086"
  inferenceOllama: "http://inference-ollama:11434"
  inferenceAdapter: "http://inference-ollama:8087"
  agentFramework: "http://agent-framework:8083"
  modelRegistry: "http://model-registry:5000"
  auditStore: "http://audit-store:9200"
  adminPortal: "http://admin-portal:8084"
```

### Sub-Chart Inventory and Service Map

| Sub-Chart | Status | Service Port | Metrics Port | PVC | Notes |
|---|---|---|---|---|---|
| `api-gateway` | Create | 8080 | 9090 | — | Ingress: `llm-poc.local` |
| `security-layer` | Create | 8081 | 9090 | — | `initialDelaySeconds: 60` (spaCy load) |
| `router` | Create | 8082 | 9090 | — | — |
| `cache` | Complete | 8086 | 9090 | Redis 5Gi | bitnami/redis sub-dep |
| `inference-ollama` | Complete | 11434 + 8087 | 9090 | 20Gi model store | dual-container + init Job |
| `agent-framework` | Create | 8083 | 9090 | — | — |
| `model-registry` | Complete | 5000 | 9090 | 2Gi | add missing probe YAML defaults |
| `audit-store` | Complete | 9200 | 9090 | 5Gi | add pvc.yaml + fix secretRef pattern |
| `admin-portal` | Create | 8084 | 9090 | — | Ingress: `llm-portal.local` |
| `observability` | Create | 3000/9090/16686 | — | — | kube-prometheus-stack wrapper |

### Standard Template Set (Per Sub-Chart)

Every sub-chart must contain these files:

```
llm-platform/charts/<name>/
├── Chart.yaml
├── values.yaml
├── README.md
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── servicemonitor.yaml
    ├── networkpolicy.yaml
    └── ingress.yaml       # api-gateway, admin-portal, observability only
```

Charts with stateful data also include `pvc.yaml` (`audit-store`, `model-registry`).
The `inference-ollama` chart also includes `adapter-deployment.yaml`, `init-job.yaml`, and `pvc.yaml`.

### Canonical Template Patterns

The following YAML snippets are the authoritative reference for all new sub-charts and any compliance corrections to existing ones.

**`_helpers.tpl`** (canonical — copy and replace `<name>` with the chart name)

```yaml
{{- define "<name>.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "<name>.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "<name>.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "<name>.labels" -}}
helm.sh/chart: {{ include "<name>.chart" . }}
{{ include "<name>.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "<name>.selectorLabels" -}}
app.kubernetes.io/name: <name>
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
```

**`deployment.yaml`** (canonical pattern for lightweight services)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "<name>.fullname" . }}
  labels:
    {{- include "<name>.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "<name>.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "<name>.labels" . | nindent 8 }}
    spec:
      serviceAccountName: {{ .Values.serviceAccount.name | default "llm-platform" }}
      containers:
        - name: <name>
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default "latest" }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .Values.service.port }}
              protocol: TCP
            - name: metrics
              containerPort: {{ .Values.metricsPort }}
              protocol: TCP
          envFrom:
            - secretRef:
                name: {{ .Values.secretRef.name }}
          env:
            {{- range $k, $v := .Values.env }}
            - name: {{ $k }}
              value: {{ $v | quote }}
            {{- end }}
          livenessProbe:
            {{- toYaml .Values.livenessProbe | nindent 12 }}
          readinessProbe:
            {{- toYaml .Values.readinessProbe | nindent 12 }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
```

**`service.yaml`** (canonical)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "<name>.fullname" . }}
  labels:
    {{- include "<name>.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
      protocol: TCP
    - name: metrics
      port: {{ .Values.metricsPort }}
      targetPort: {{ .Values.metricsPort }}
      protocol: TCP
  selector:
    {{- include "<name>.selectorLabels" . | nindent 4 }}
```

**`servicemonitor.yaml`** (canonical)

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "<name>.fullname" . }}
  labels:
    {{- include "<name>.labels" . | nindent 4 }}
spec:
  selector:
    matchLabels:
      {{- include "<name>.selectorLabels" . | nindent 6 }}
  namespaceSelector:
    matchNames:
      - {{ .Release.Namespace }}
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
```

**`networkpolicy.yaml`** (POC — permit all within namespace)

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "<name>.fullname" . }}
  labels:
    {{- include "<name>.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "<name>.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Release.Namespace }}
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Release.Namespace }}
    - ports:  # allow DNS
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
```

**`ingress.yaml`** (for api-gateway, admin-portal, observability)

```yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "<name>.fullname" . }}
  labels:
    {{- include "<name>.labels" . | nindent 4 }}
spec:
  ingressClassName: nginx
  rules:
    - host: {{ .Values.ingress.host }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ include "<name>.fullname" . }}
                port:
                  number: {{ .Values.ingress.servicePort }}
{{- end }}
```

### Observability Sub-Chart Design

The `observability` sub-chart is a thin wrapper that passes values through to `kube-prometheus-stack`. Its `Chart.yaml`:

```yaml
apiVersion: v2
name: observability
description: "Observability stack wrapper — kube-prometheus-stack + optional Jaeger"
type: application
version: 0.1.0
appVersion: "0.1.0"
dependencies:
  - name: kube-prometheus-stack
    version: "~58.x"
    repository: "https://prometheus-community.github.io/helm-charts"
    condition: kubePrometheusStack.enabled
```

`values.yaml` POC overrides passed through to the dependency:

```yaml
kubePrometheusStack:
  enabled: true

kube-prometheus-stack:
  grafana:
    adminPassword: "poc-admin"
    service:
      type: ClusterIP
    sidecar:
      datasources:
        enabled: true
  alertmanager:
    enabled: false
  prometheus:
    prometheusSpec:
      retention: "7d"

jaeger:
  enabled: false   # set true to deploy Jaeger all-in-one

ingress:
  enabled: false
  host: "grafana-poc.local"
  servicePort: 3000
```

The optional Jaeger Deployment (when `jaeger.enabled: true`) uses the `jaegertracing/all-in-one:latest` image and exposes ports 16686 (UI), 6831/UDP (Jaeger compact), and 14268 (HTTP collector).

---

## Data Models

### `values.yaml` Standard Schema (Lightweight Services)

Every lightweight service sub-chart must conform to this schema. Types and default values are shown.

```yaml
replicaCount: 1                           # int

image:
  repository: "registry.local/<svc>"      # string — required
  tag: ""                                  # string — empty → "latest" fallback in template
  pullPolicy: IfNotPresent                 # string

service:
  type: ClusterIP                          # string
  port: <port>                             # int — service-specific

metricsPort: 9090                          # int

resources:
  requests:
    cpu: "100m"                            # string
    memory: "256Mi"                        # string
  limits:
    cpu: "1"                               # string
    memory: "1Gi"                          # string

autoscaling:
  enabled: false                           # bool

vault:
  enabled: false                           # bool

secretRef:
  name: "llm-poc-secrets"                  # string

serviceAccount:
  name: "llm-platform"                     # string

livenessProbe:
  httpGet:
    path: /health
    port: <service port>
  initialDelaySeconds: 15                  # int
  periodSeconds: 15                        # int
  timeoutSeconds: 5                        # int
  failureThreshold: 3                      # int
  successThreshold: 1                      # int

readinessProbe:
  httpGet:
    path: /health
    port: <service port>
  initialDelaySeconds: 15                  # int
  periodSeconds: 15                        # int
  timeoutSeconds: 5                        # int
  failureThreshold: 3                      # int
  successThreshold: 1                      # int

env: {}                                    # map[string]string — service-specific env vars

persistence:
  enabled: false                           # bool — true for stateful services
  size: ""                                 # string — e.g., "5Gi"
  storageClass: ""                         # string — empty uses cluster default
  mountPath: "/data"                       # string

ingress:
  enabled: false                           # bool
  host: ""                                 # string
  servicePort: <service port>              # int
```

### Per-Service Values Overrides from Standard Schema

The table below shows only the fields that differ from the standard schema defaults:

| Sub-Chart | `service.port` | `resources.requests` | `resources.limits` | `persistence` | Probe Notes |
|---|---|---|---|---|---|
| `api-gateway` | 8080 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | — | standard |
| `security-layer` | 8081 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | — | `initialDelaySeconds: 60` (both probes) |
| `router` | 8082 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | — | standard |
| `cache` | 8086 | cpu:200m mem:512Mi | cpu:1 mem:1Gi | — | standard |
| `inference-ollama` (adapter) | 8087 | cpu:100m mem:256Mi | cpu:1 mem:512Mi | — | standard |
| `inference-ollama` (ollama) | 11434 | cpu:1 mem:8Gi | cpu:4 mem:16Gi | 20Gi, `/root/.ollama` | `path: /api/tags`, `initialDelaySeconds: 30`, `failureThreshold: 5`, `timeoutSeconds: 30` |
| `agent-framework` | 8083 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | — | standard |
| `model-registry` | 5000 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | 2Gi, `/data` | standard |
| `audit-store` | 9200 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | 5Gi, `/data` | standard |
| `admin-portal` | 8084 | cpu:100m mem:256Mi | cpu:1 mem:1Gi | — | standard |

### PVC Schema

Used by `audit-store` (5Gi), `model-registry` (2Gi). The `inference-ollama` chart has its own PVC already defined.

```yaml
{{- if .Values.persistence.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "<name>.fullname" . }}-data
  labels:
    {{- include "<name>.labels" . | nindent 4 }}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: {{ .Values.persistence.size }}
  {{- if .Values.persistence.storageClass }}
  storageClassName: {{ .Values.persistence.storageClass }}
  {{- end }}
{{- end }}
```

### Deployment Volume Mount Pattern (persistence toggle)

```yaml
          {{- if .Values.persistence.enabled }}
          volumeMounts:
            - name: data
              mountPath: {{ .Values.persistence.mountPath }}
          {{- end }}
      {{- if .Values.persistence.enabled }}
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: {{ include "<name>.fullname" . }}-data
      {{- else }}
      volumes:
        - name: data
          emptyDir: {}
      {{- end }}
```

### Deployment and Operation Scripts

#### `scripts/deploy.sh` — Step Sequence

```
Step 1:  preflight — verify kubectl, helm ≥3, cluster reachability, node capacity (≥8 CPU, ≥16Gi RAM)
Step 2:  install NGINX Ingress Controller (upstream static manifest); wait for controller pod Ready
Step 3:  create namespace llm-poc (idempotent via kubectl apply)
Step 4:  create llm-poc-secrets Secret (idempotent via --dry-run=client | kubectl apply)
Step 5:  create ServiceAccount llm-platform in llm-poc (idempotent)
Step 6:  helm dependency update ./llm-platform
Step 7:  helm upgrade --install llm-poc ./llm-platform --namespace llm-poc --values ./llm-platform/values-poc.yaml
Step 8:  helm upgrade --install observability ./llm-platform/charts/observability --namespace monitoring --create-namespace
Step 9:  kubectl rollout status deployment --namespace llm-poc (all deployments)
Step 10: check for Pending pods; print resource-constrained pod names if any
Step 11: wait for Ollama init Job completion (model pre-pull)
```

Flags: `--dry-run` (print commands only), `--uninstall` (helm uninstall + namespace delete).

#### `scripts/smoke-test.sh` — Check Sequence

```
Check 1:  Health — GET /health on all 9 app services (via kubectl exec into api-gateway pod)
Check 2:  E2E Chat — POST /v1/chat/completions → HTTP 200, choices[0].message.content non-empty
Check 3:  Audit trail — GET /audit/requests/<id> → ≥3 audit events, distinct layer values
Check 4:  Cache hit — repeat identical POST → cache.lookup_hit: true in response
Check 5:  Injection block — POST with injection prompt → HTTP 400 with security_block field
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature is the Helm chart and deployment scripting layer. The "code under test" is the Helm templates themselves: YAML-generating functions that transform values into Kubernetes manifests. Property-based testing is applicable because:

- `helm template` is a pure deterministic function: `f(values) → manifests`
- There are universal structural properties that must hold across all 10 sub-charts
- Input variation (which sub-charts are enabled, what values are set) reveals structural bugs
- Template rendering is fast (milliseconds), making 100+ iterations cost-effective

The PBT library used is **pytest-helm** (Python) with **hypothesis** for input generation, or equivalently a shell-based property harness using `helm template` + `yq` assertions. Each property is validated by running `helm template` with varied inputs and asserting structural invariants on the YAML output.

### Property 1: Condition Gating — Zero Resources When Sub-Chart Disabled

*For any* sub-chart in the umbrella chart, setting its condition value to `false` in values produces zero Kubernetes resources associated with that sub-chart in the rendered manifest output.

**Validates: Requirements 1.7**

### Property 2: Full Render Produces Expected Resource Set

*For any* combination of enabled sub-charts, running `helm template` with those sub-charts enabled exits with code `0` and produces valid YAML containing exactly the expected resource kinds (Deployment, Service, ServiceMonitor, NetworkPolicy) for each enabled sub-chart.

**Validates: Requirements 1.6, 14.2**

### Property 3: Every Sub-Chart Has Required Files

*For any* sub-chart directory under `llm-platform/charts/`, the required files (`Chart.yaml`, `values.yaml`, `README.md`, and each of `_helpers.tpl`, `deployment.yaml`, `service.yaml`, `servicemonitor.yaml`, `networkpolicy.yaml` in `templates/`) must all exist.

**Validates: Requirements 2.1, 15.7**

### Property 4: Every Sub-Chart Values Schema Is Compliant

*For any* sub-chart `values.yaml`, the required top-level keys (`replicaCount`, `image.repository`, `image.tag`, `image.pullPolicy`, `service.type`, `service.port`, `resources.requests`, `resources.limits`, `autoscaling.enabled`, `vault.enabled`, `secretRef.name`) must all be present with correct types and POC-appropriate defaults.

**Validates: Requirements 2.2, 12.1**

### Property 5: Every Rendered Deployment Has Both Probes

*For any* rendered Deployment resource from any sub-chart, both `livenessProbe` and `readinessProbe` must be defined with `httpGet` configured.

**Validates: Requirements 11.1, 11.2**

### Property 6: No Literal Secret Values in Any Values File

*For any* values file (`values.yaml` or `values-poc.yaml`) across all sub-charts and the umbrella chart, scanning the file content must return no literal secret values (e.g., `poc-secret-key`, non-empty passwords, tokens).

**Validates: Requirements 4.3**

### Property 7: Persistence Toggle Replaces PVC With emptyDir

*For any* sub-chart that declares `persistence.enabled`, rendering the chart with `persistence.enabled=false` must produce a Deployment with an `emptyDir` volume rather than a `PersistentVolumeClaim` reference, and no PVC resource must appear in the manifest.

**Validates: Requirements 6.5, 6.6**

### Property 8: No Prohibited Resources in Any Rendered Chart

*For any* rendered manifest from any sub-chart, no resources of kinds `VirtualService`, `DestinationRule`, `AuthorizationPolicy`, `PeerAuthentication`, or `HorizontalPodAutoscaler` must appear.

**Validates: Requirements 18.1, 18.2**

### Property 9: Service URL Consistency

*For any* entry in the `services` block of `values-poc.yaml`, the referenced service name must correspond to a Service resource that appears in the rendered umbrella manifest when all sub-charts are enabled.

**Validates: Requirements 7.1**

### Property 10: Image Tag Fallback Behavior

*For any* sub-chart rendered with `image.tag` set to an empty string, the resulting Deployment's container image must use the suffix `:latest`; when `image.tag` is set to a non-empty value, the Deployment must use that exact tag without modification.

**Validates: Requirements 16.1, 16.5**

---

## Error Handling

### Template Rendering Errors

Helm template rendering fails fast: any undefined value reference or syntax error causes `helm template` to exit non-zero. The design avoids this by:

- Using `| default "latest"` on `image.tag` so empty-string tags never produce a rendering error
- Using `{{- if .Values.persistence.enabled }}` guards on all PVC/volume blocks
- Using `{{- if .Values.ingress.enabled }}` guards on all Ingress resources
- Using `{{- if .Values.initJob.enabled }}` guard on the Ollama init Job

All required values have defaults in `values.yaml` so `helm install` without `--values` also renders cleanly.

### Deployment Script Error Handling

```
Every kubectl/helm command is run with `|| { echo "[FAIL] Step N: <description>"; exit 1; }`.
Steps that can legitimately be no-ops (namespace/secret already exists) use idempotent apply patterns:
  kubectl create ... --dry-run=client -o yaml | kubectl apply -f -
This exits 0 whether the resource already exists or was just created.
```

The script checks for Pending pods after rollout and emits a warning (not a failure) with resource-constrained pod names, so the operator can decide whether to scale the cluster.

### Secret Missing at Pod Schedule Time

When `llm-poc-secrets` does not exist and a pod references it via `envFrom.secretRef`, Kubernetes holds the pod in `Pending` with event reason `CreateContainerConfigError`. This is the correct behavior — the platform must not start in a degraded state. The deploy script creates the secret before `helm install` precisely to prevent this.

### Observability Namespace Split

ServiceMonitor resources live in `llm-poc`. Prometheus lives in `monitoring`. For Prometheus to discover these ServiceMonitors, the kube-prometheus-stack must be configured with `prometheus.prometheusSpec.serviceMonitorNamespaceSelector: {}` (match all namespaces). This is set in the observability sub-chart values.

### PVC Provisioning Failure

If the cluster lacks a default StorageClass (rare on k3s/kind/minikube), PVCs will remain in `Pending`. The deploy script checks for Pending PVCs after rollout and emits a named warning. For k3s, the `local-path` provisioner is pre-installed; for kind and minikube, the `standard` StorageClass is pre-installed.

---

## Testing Strategy

### Dual-Layer Approach

This feature uses a combination of:

1. **Property-based tests** — verify universal structural invariants of the Helm templates using `hypothesis` + shell-invoked `helm template` + `PyYAML` parsing
2. **Example-based unit tests** — verify specific chart configurations (exact PVC sizes, specific probe values, dependency declarations)
3. **Integration tests** — validate the running platform via `scripts/smoke-test.sh`

Property-based tests are the right tool here because `helm template` is a fast, pure, deterministic function with a large and well-defined input space (values combinations), and many bugs in Helm charts arise from edge cases in values combinations (missing keys, empty strings, boolean flags) that random generation will explore.

### Property-Based Test Configuration

**Library:** `hypothesis` (Python) with custom `helm_template` strategy  
**Iterations:** Minimum 100 per property  
**Test tag format:** `# Feature: kubernetes-helm-infrastructure, Property N: <property text>`

Each property test follows this pattern:

```python
from hypothesis import given, settings
from hypothesis import strategies as st
import subprocess, yaml

CHARTS = ["api-gateway", "security-layer", "router", "cache",
          "inference-ollama", "agent-framework", "model-registry",
          "audit-store", "admin-portal"]

# Feature: kubernetes-helm-infrastructure, Property 1: condition gating
@given(chart=st.sampled_from(CHARTS))
@settings(max_examples=100)
def test_condition_gating(chart):
    """Disabling a sub-chart condition produces zero resources for that chart."""
    camel = to_camel(chart)
    result = helm_template(f"--set {camel}.enabled=false")
    manifests = yaml.safe_load_all(result.stdout)
    for doc in manifests:
        assert chart not in (doc.get("metadata", {}).get("name", "") or ""), \
            f"Found resource for disabled chart {chart}"
```

### Unit / Example Tests

| Test | What It Verifies | Tool |
|---|---|---|
| `test_umbrella_chart_yaml` | 10 deps, correct condition keys, `name: llm-platform-poc` | pytest + PyYAML |
| `test_values_poc_all_enabled` | All 10 condition flags are `true` | pytest + PyYAML |
| `test_pvc_sizes` | audit-store=5Gi, model-registry=2Gi, ollama=20Gi | helm template + yq |
| `test_init_job_empty_preload` | Job script contains empty-list guard | pytest (template parse) |
| `test_observability_alertmanager_disabled` | `alertmanager.enabled: false` in rendered values | helm template |
| `test_security_layer_probe_delay` | `initialDelaySeconds: 60` for security-layer | helm template + yq |
| `test_ollama_probe_path` | `path: /api/tags` for Ollama container | helm template + yq |
| `test_helm_lint_all` | All 10 charts pass `helm lint` with zero errors | subprocess |

### Integration Tests (Smoke Test Suite)

Run via `scripts/smoke-test.sh` after a successful cluster deployment. See the Smoke Test step sequence under Components and Interfaces. These tests require a running cluster and are not part of CI unit test runs — they validate the deployed state only.

### Test Execution

```bash
# Unit + property tests (no cluster required)
cd llm-platform
helm dependency update .
pytest tests/helm/ -v --tb=short

# Lint all charts
for chart in charts/*/; do helm lint "$chart"; done

# Template validation
helm template llm-poc . --values values-poc.yaml | kubectl apply --dry-run=client -f -

# Integration tests (requires running cluster)
scripts/smoke-test.sh --namespace llm-poc --host llm-poc.local
```
