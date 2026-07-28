# build-and-push-all.ps1
# Full POC deployment script: builds images, pushes to local registry,
# creates namespace/secrets/serviceaccount, and does helm install.
#
# Run from repo root: .\scripts\build-and-push-all.ps1
#
# Prerequisites:
#   - Docker Desktop running with Kubernetes enabled
#   - local-registry container running on port 5000
#     (if not: docker run -d -p 5000:5000 --restart=always --name local-registry registry:2)
#   - helm installed
#   - kubectl context pointing to docker-desktop

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$namespace = "llm-poc"

# ─── Step 1: Ensure local registry is running ────────────────────────────────
Write-Host "`n=== Step 1: Ensuring local registry is running ===" -ForegroundColor Cyan
$reg = docker ps --filter name=local-registry --format "{{.Names}}" 2>&1
if ($reg -notmatch "local-registry") {
    Write-Host "Starting local registry..." -ForegroundColor Yellow
    docker run -d -p 5000:5000 --restart=always --name local-registry registry:2
} else {
    Write-Host "local-registry already running." -ForegroundColor Green
}

# ─── Step 2: Pull and push Redis (from Docker Hub) ───────────────────────────
Write-Host "`n=== Step 2: Pulling and pushing redis:7-alpine ===" -ForegroundColor Cyan
docker pull redis:7-alpine
docker tag redis:7-alpine localhost:5000/redis:7-alpine
docker push localhost:5000/redis:7-alpine

# ─── Step 3: Build and push all service images ───────────────────────────────
Write-Host "`n=== Step 3: Building and pushing service images ===" -ForegroundColor Cyan

$images = @(
    @{ tag = "localhost:5000/api-gateway:poc";         dockerfile = "api_gateway/Dockerfile" },
    @{ tag = "localhost:5000/security-layer:poc";      dockerfile = "security_layer/Dockerfile" },
    @{ tag = "localhost:5000/router:poc";              dockerfile = "intelligent_router/Dockerfile" },
    @{ tag = "localhost:5000/inference-adapter:poc";   dockerfile = "inference_adapter/Dockerfile" },
    @{ tag = "localhost:5000/audit-store:poc";         dockerfile = "audit_store/Dockerfile" },
    @{ tag = "localhost:5000/model-registry:poc";      dockerfile = "model_registry/Dockerfile" },
    @{ tag = "localhost:5000/admin-portal:poc";        dockerfile = "admin_portal/Dockerfile" },
    @{ tag = "localhost:5000/agent-framework:poc-v2";  dockerfile = "services/agent-framework/Dockerfile" },
    @{ tag = "localhost:5000/cache-service:poc-v2";    dockerfile = "cache_service/Dockerfile" }
)

foreach ($img in $images) {
    Write-Host "--- Building $($img.tag) ---" -ForegroundColor Cyan
    docker build -f $img.dockerfile -t $img.tag $root
    if ($LASTEXITCODE -ne 0) { Write-Error "BUILD FAILED: $($img.tag)"; exit 1 }

    Write-Host "--- Pushing $($img.tag) ---" -ForegroundColor Yellow
    docker push $img.tag
    if ($LASTEXITCODE -ne 0) { Write-Error "PUSH FAILED: $($img.tag)"; exit 1 }

    Write-Host "DONE: $($img.tag)" -ForegroundColor Green
}

# ─── Step 4: Create namespace ────────────────────────────────────────────────
Write-Host "`n=== Step 4: Creating namespace $namespace ===" -ForegroundColor Cyan
$nsExists = kubectl get namespace $namespace 2>&1
if ($nsExists -match "NotFound" -or $LASTEXITCODE -ne 0) {
    kubectl create namespace $namespace
} else {
    Write-Host "Namespace $namespace already exists." -ForegroundColor Green
}

# ─── Step 5: Create service account ─────────────────────────────────────────
Write-Host "`n=== Step 5: Creating service account llm-platform ===" -ForegroundColor Cyan
$saExists = kubectl get serviceaccount llm-platform -n $namespace 2>&1
if ($saExists -match "NotFound" -or $LASTEXITCODE -ne 0) {
    kubectl create serviceaccount llm-platform -n $namespace
} else {
    Write-Host "ServiceAccount llm-platform already exists." -ForegroundColor Green
}

# ─── Step 6: Create secrets ───────────────────────────────────────────────────
Write-Host "`n=== Step 6: Creating llm-poc-secrets ===" -ForegroundColor Cyan
$secretExists = kubectl get secret llm-poc-secrets -n $namespace 2>&1
if ($secretExists -match "NotFound" -or $LASTEXITCODE -ne 0) {
    kubectl create secret generic llm-poc-secrets -n $namespace `
        --from-literal=GATEWAY_API_KEY=poc-secret-key `
        --from-literal=AUDIT_API_KEY=poc-audit-key
} else {
    Write-Host "Secret llm-poc-secrets already exists." -ForegroundColor Green
}

# ─── Step 7: Helm install/upgrade ────────────────────────────────────────────
# NOTE: initJob is disabled here — Ollama needs to be Running before the Job
# can pull llama3.2:3b. After deploy, run:
#   kubectl exec -n llm-poc deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b
Write-Host "`n=== Step 7: Helm install/upgrade ===" -ForegroundColor Cyan
$helmStatus = helm list -n $namespace --short 2>&1
if ($helmStatus -match "llm-poc") {
    helm upgrade llm-poc "$root\llm-platform" `
        --namespace $namespace `
        --values "$root\llm-platform\values-poc.yaml" `
        --values "$root\llm-platform\values-poc-local.yaml"
} else {
    helm install llm-poc "$root\llm-platform" `
        --namespace $namespace `
        --values "$root\llm-platform\values-poc.yaml" `
        --values "$root\llm-platform\values-poc-local.yaml"
}
if ($LASTEXITCODE -ne 0) { Write-Error "HELM DEPLOY FAILED"; exit 1 }

# ─── Step 8: Wait for Ollama, then pull model ────────────────────────────────
Write-Host "`n=== Step 8: Waiting for Ollama to be ready ===" -ForegroundColor Cyan
Write-Host "This may take 1-2 minutes..." -ForegroundColor Yellow
kubectl rollout status deployment/llm-poc-inference-ollama -n $namespace --timeout=180s
if ($LASTEXITCODE -eq 0) {
    Write-Host "Pulling llama3.2:3b model into Ollama..." -ForegroundColor Yellow
    kubectl exec -n $namespace deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Model pull failed. Run manually:" -ForegroundColor Red
        Write-Host "  kubectl exec -n $namespace deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b"
    } else {
        Write-Host "Model llama3.2:3b pulled successfully." -ForegroundColor Green
    }
} else {
    Write-Host "WARNING: Ollama not ready in time. Pull model manually:" -ForegroundColor Red
    Write-Host "  kubectl exec -n $namespace deploy/llm-poc-inference-ollama -- ollama pull llama3.2:3b"
}

Write-Host "`n=== Deployment complete ===" -ForegroundColor Green
Write-Host "Check pod status: kubectl get pods -n $namespace"
Write-Host "Security-layer may take up to 6 minutes to reach Ready (Presidio/spaCy loading)"
