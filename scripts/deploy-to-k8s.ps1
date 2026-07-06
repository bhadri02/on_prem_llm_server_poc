#!/usr/bin/env pwsh
# =============================================================================
# scripts/deploy-to-k8s.ps1
# Deploys the LLM Platform POC to Kubernetes (Docker Desktop)
# Run from the repo root: powershell -ExecutionPolicy Bypass -File scripts\deploy-to-k8s.ps1
# =============================================================================

param(
    [string]$Tag       = "poc",
    [string]$Namespace = "llm-poc",
    [string]$Release   = "llm-poc",
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root       = $PSScriptRoot | Split-Path
$ChartDir   = Join-Path $Root "llm-platform"
$ValuesFile = Join-Path $ChartDir "values-poc.yaml"
$LocalValues = Join-Path $ChartDir "values-poc-local.yaml"

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
}

function Invoke-Cmd {
    param([string[]]$cmd)
    if ($DryRun) {
        Write-Host "[DRY-RUN] $($cmd -join ' ')" -ForegroundColor DarkYellow
        return
    }
    & $cmd[0] $cmd[1..($cmd.Length-1)]
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $($cmd -join ' ')"
    }
}

Write-Host ""
Write-Host "████████████████████████████████████████████████████" -ForegroundColor Cyan
Write-Host "  Enterprise On-Prem LLM Platform — K8s Deploy      " -ForegroundColor Cyan
Write-Host "████████████████████████████████████████████████████" -ForegroundColor Cyan
Write-Host "  Namespace : $Namespace"
Write-Host "  Release   : $Release"
Write-Host "  Chart Dir : $ChartDir"
Write-Host "  Values    : $ValuesFile"
if ($DryRun) { Write-Host "  [DRY-RUN MODE]" -ForegroundColor DarkYellow }
Write-Host ""

# ─── UNINSTALL PATH ───────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step "Uninstalling LLM Platform POC"

    Write-Host "Removing Helm release '$Release' from namespace '$Namespace'..."
    helm uninstall $Release --namespace $Namespace 2>&1 | Write-Host

    Write-Host "Removing observability Helm release..."
    helm uninstall observability --namespace monitoring 2>&1 | Write-Host

    Write-Host "Deleting namespace '$Namespace'..."
    kubectl delete namespace $Namespace --ignore-not-found=true

    Write-Host "Deleting namespace 'monitoring'..."
    kubectl delete namespace monitoring --ignore-not-found=true

    Write-Host ""
    Write-Host "✓ Uninstall complete." -ForegroundColor Green
    exit 0
}

# ─── STEP 1: Verify Tools ─────────────────────────────────────────────────────
Write-Step "Step 1/8: Verify Tools"

foreach ($tool in @("kubectl", "helm", "docker")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] '$tool' not found on PATH." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] $tool found"
}

Write-Host ""
Write-Host "Checking Kubernetes cluster..."
kubectl cluster-info 2>&1 | Select-Object -First 2 | Write-Host
Write-Host "[OK] Cluster reachable"

# ─── STEP 2: Install NGINX Ingress ────────────────────────────────────────────
Write-Step "Step 2/8: Install NGINX Ingress Controller"

$ingressManifest = "https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml"

Write-Host "Applying NGINX Ingress Controller manifest..."
Invoke-Cmd kubectl apply -f $ingressManifest

if (-not $DryRun) {
    Write-Host "Waiting for ingress controller pod to be ready (timeout: 120s)..."
    kubectl wait --namespace ingress-nginx `
        --for=condition=ready pod `
        --selector=app.kubernetes.io/component=controller `
        --timeout=120s
    Write-Host "[OK] NGINX Ingress Controller ready"
}

# ─── STEP 3: Create Namespace ─────────────────────────────────────────────────
Write-Step "Step 3/8: Create Namespace and Secrets"

Write-Host "Creating namespace '$Namespace' (idempotent)..."
Invoke-Cmd kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -

if (-not $DryRun) {
    kubectl label namespace $Namespace "kubernetes.io/metadata.name=$Namespace" --overwrite=true 2>&1 | Write-Host
}

# ─── STEP 4: Create Kubernetes Secrets ────────────────────────────────────────
Write-Host ""
Write-Host "Creating Kubernetes secret 'llm-poc-secrets'..."

$secretCmd = @(
    "kubectl", "create", "secret", "generic", "llm-poc-secrets",
    "--namespace", $Namespace,
    "--from-literal=GATEWAY_API_KEY=poc-secret-key",
    "--from-literal=REDIS_PASSWORD=",
    "--from-literal=AUDIT_API_KEY=poc-audit-key",
    "--dry-run=client", "-o", "yaml"
)

if ($DryRun) {
    Write-Host "[DRY-RUN] kubectl create secret generic llm-poc-secrets ..." -ForegroundColor DarkYellow
} else {
    & kubectl create secret generic llm-poc-secrets `
        --namespace $Namespace `
        --from-literal=GATEWAY_API_KEY=poc-secret-key `
        --from-literal=REDIS_PASSWORD="" `
        --from-literal=AUDIT_API_KEY=poc-audit-key `
        --dry-run=client -o yaml | kubectl apply -f -
}

Write-Host "Creating service account 'llm-platform'..."
Invoke-Cmd kubectl create serviceaccount llm-platform --namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -

# ─── STEP 5: Seed Model Registry Data ─────────────────────────────────────────
Write-Step "Step 5/8: Create Model Registry ConfigMap"

$modelsJson = Join-Path $Root "seed" "models.json"
if (Test-Path $modelsJson) {
    Write-Host "Creating model-registry-seed ConfigMap from seed/models.json..."
    if (-not $DryRun) {
        kubectl create configmap model-registry-seed `
            --namespace $Namespace `
            --from-file=models.json=$modelsJson `
            --dry-run=client -o yaml | kubectl apply -f -
        Write-Host "[OK] model-registry-seed ConfigMap created"
    } else {
        Write-Host "[DRY-RUN] kubectl create configmap model-registry-seed ..." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "[WARN] seed/models.json not found. Model Registry will start empty." -ForegroundColor DarkYellow
}

# Create router config maps
$modelMatrix = Join-Path $Root "model_matrix.yaml"
$taskRules   = Join-Path $Root "task_classifier_rules.yaml"

if ((Test-Path $modelMatrix) -and (Test-Path $taskRules)) {
    Write-Host "Creating router-config ConfigMap..."
    if (-not $DryRun) {
        kubectl create configmap router-config `
            --namespace $Namespace `
            --from-file=model_matrix.yaml=$modelMatrix `
            --from-file=task_classifier_rules.yaml=$taskRules `
            --dry-run=client -o yaml | kubectl apply -f -
        Write-Host "[OK] router-config ConfigMap created"
    }
}

# Create injection patterns config map for security layer
$injectionPatterns = Join-Path $Root "injection_patterns.yaml"
if (Test-Path $injectionPatterns) {
    Write-Host "Creating security-config ConfigMap..."
    if (-not $DryRun) {
        kubectl create configmap security-config `
            --namespace $Namespace `
            --from-file=injection_patterns.yaml=$injectionPatterns `
            --dry-run=client -o yaml | kubectl apply -f -
        Write-Host "[OK] security-config ConfigMap created"
    }
}

# ─── STEP 6: Helm Dependency Update ───────────────────────────────────────────
Write-Step "Step 6/8: Helm Dependency Update"

Write-Host "Running helm dependency update..."
Push-Location $ChartDir
try {
    Invoke-Cmd helm dependency update .
} finally {
    Pop-Location
}

# ─── STEP 7: Helm Install/Upgrade ─────────────────────────────────────────────
Write-Step "Step 7/8: Helm Install/Upgrade Umbrella Chart"

$helmArgs = @(
    "upgrade", "--install", $Release, $ChartDir,
    "--namespace", $Namespace,
    "--values", $ValuesFile,
    "--values", $LocalValues,
    # Override image tags — MUST use hyphenated sub-chart dependency names from Chart.yaml,
    # not camelCase umbrella keys. camelCase keys (e.g. apiGateway) do NOT flow into sub-charts.
    "--set", "api-gateway.image.tag=$Tag",
    "--set", "security-layer.image.tag=$Tag",
    "--set", "router.image.tag=$Tag",
    "--set", "cache.image.tag=$Tag",
    "--set", "inference-ollama.adapter.image.tag=$Tag",
    "--set", "audit-store.image.tag=$Tag",
    "--set", "model-registry.image.tag=$Tag",
    "--set", "admin-portal.image.tag=$Tag",
    "--set", "agent-framework.image.tag=$Tag",
    # Use local registry (localhost:5000 — visible to both Docker daemon and containerd)
    "--set", "global.imageRegistry=localhost:5000",
    "--timeout", "15m"
)

if (-not $DryRun) {
    $helmArgs += "--wait"
}

Write-Host "Running: helm $($helmArgs -join ' ')"
Invoke-Cmd helm @helmArgs

# ─── STEP 8: Deploy Observability ─────────────────────────────────────────────
Write-Step "Step 8/8: Helm Install/Upgrade Observability"

$obsChart = Join-Path $ChartDir "charts" "observability"

$obsHelmArgs = @(
    "upgrade", "--install", "observability", $obsChart,
    "--namespace", "monitoring",
    "--create-namespace",
    "--timeout", "10m"
)

if (-not $DryRun) {
    $obsHelmArgs += "--wait"
}

Invoke-Cmd helm @obsHelmArgs

# ─── Summary ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "████████████████████████████████████████████████████" -ForegroundColor Green
Write-Host "  ✓ Deployment Complete!" -ForegroundColor Green
Write-Host "████████████████████████████████████████████████████" -ForegroundColor Green
Write-Host ""
Write-Host "  Check pod status:" -ForegroundColor White
Write-Host "    kubectl get pods -n $Namespace"
Write-Host ""
Write-Host "  Port-forward for access (run in separate terminal):" -ForegroundColor White
Write-Host "    kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 80:80"
Write-Host "    kubectl port-forward -n monitoring svc/observability-grafana 3000:80"
Write-Host ""
Write-Host "  Test the platform:" -ForegroundColor White
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\smoke-test.ps1"
Write-Host ""
Write-Host "  URLs (add 127.0.0.1 llm-poc.local to C:\Windows\System32\drivers\etc\hosts):" -ForegroundColor White
Write-Host "    http://llm-poc.local/health          — API Gateway health"
Write-Host "    http://llm-poc.local/v1/chat/completions  — Chat endpoint"
Write-Host "    http://localhost:3000               — Grafana dashboard"
Write-Host ""
Write-Host "  Uninstall with:" -ForegroundColor White
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\deploy-to-k8s.ps1 -Uninstall"
Write-Host ""
