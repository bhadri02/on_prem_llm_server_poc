#!/usr/bin/env pwsh
# =============================================================================
# scripts/build-all-images.ps1
#
# Builds all Docker images for the LLM Platform POC.
# Build context is ALWAYS the repo root so every Dockerfile can reference
# shared/ and any other top-level directory it needs.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build-all-images.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build-all-images.ps1 -Force
#   powershell -ExecutionPolicy Bypass -File scripts\build-all-images.ps1 -Service api-gateway
# =============================================================================

param(
    [string]$Tag      = "poc",
    [string]$Registry = "registry.local",
    [string]$Service  = "",        # build only this service (empty = all)
    [switch]$Force                 # rebuild even if image already exists
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # repo root (one level up from scripts/)

# ---------------------------------------------------------------------------
# Service definitions
# Each entry:  Name, Dockerfile path relative to repo root, Image name
# Build context is always $Root for every service.
# ---------------------------------------------------------------------------
$AllServices = @(
    [pscustomobject]@{
        Name       = "api-gateway"
        Dockerfile = "api_gateway\Dockerfile"
        Image      = "$Registry/api-gateway:$Tag"
    }
    [pscustomobject]@{
        Name       = "security-layer"
        Dockerfile = "security_layer\Dockerfile"
        Image      = "$Registry/security-layer:$Tag"
    }
    [pscustomobject]@{
        Name       = "router"
        Dockerfile = "intelligent_router\Dockerfile"
        Image      = "$Registry/router:$Tag"
    }
    [pscustomobject]@{
        Name       = "cache"
        Dockerfile = "cache_service\Dockerfile"
        Image      = "$Registry/cache-service:$Tag"
    }
    [pscustomobject]@{
        Name       = "inference-adapter"
        Dockerfile = "inference_adapter\Dockerfile"
        Image      = "$Registry/inference-adapter:$Tag"
    }
    [pscustomobject]@{
        Name       = "audit-store"
        Dockerfile = "audit_store\Dockerfile"
        Image      = "$Registry/audit-store:$Tag"
    }
    [pscustomobject]@{
        Name       = "model-registry"
        Dockerfile = "model_registry\Dockerfile"
        Image      = "$Registry/model-registry:$Tag"
    }
    [pscustomobject]@{
        Name       = "admin-portal"
        Dockerfile = "admin_portal\Dockerfile"
        Image      = "$Registry/admin-portal:$Tag"
    }
    [pscustomobject]@{
        Name       = "agent-framework"
        Dockerfile = "services\agent-framework\Dockerfile"
        Image      = "$Registry/agent-framework:$Tag"
    }
    [pscustomobject]@{
        Name       = "portal-ui"
        Dockerfile = "portal_ui\Dockerfile"
        Image      = "$Registry/portal-ui:$Tag"
    }
)

# Filter to a single service if -Service was provided
$Services = if ($Service) {
    $AllServices | Where-Object { $_.Name -eq $Service }
} else {
    $AllServices
}

if ($Service -and $Services.Count -eq 0) {
    Write-Host "[ERROR] Unknown service '$Service'. Valid names:" -ForegroundColor Red
    $AllServices | ForEach-Object { Write-Host "  - $($_.Name)" }
    exit 1
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  LLM Platform POC — Docker Image Build            " -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Registry : $Registry"
Write-Host "  Tag      : $Tag"
Write-Host "  Root     : $Root"
Write-Host "  Services : $($Services.Count)"
if ($Force) { Write-Host "  Mode     : FORCE REBUILD" -ForegroundColor Yellow }
Write-Host ""

# ---------------------------------------------------------------------------
# Build loop
# ---------------------------------------------------------------------------
$Passed  = 0
$Failed  = 0
$Skipped = 0
$Results = [System.Collections.Generic.List[pscustomobject]]::new()

foreach ($svc in $Services) {

    $dockerfilePath = Join-Path $Root $svc.Dockerfile

    Write-Host "────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "[BUILD] $($svc.Name)" -ForegroundColor Yellow
    Write-Host "        Image : $($svc.Image)"
    Write-Host "        File  : $dockerfilePath"

    # --- Guard: Dockerfile must exist ---
    if (-not (Test-Path $dockerfilePath)) {
        Write-Host "[SKIP]  Dockerfile not found." -ForegroundColor DarkYellow
        $Skipped++
        $Results.Add([pscustomobject]@{Service=$svc.Name; Status="SKIPPED (no Dockerfile)"; Duration="–"})
        continue
    }

    # --- Skip if image already exists and -Force not set ---
    if (-not $Force) {
        $exists = docker image inspect $svc.Image 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[SKIP]  Image already exists. Use -Force to rebuild." -ForegroundColor DarkGray
            $Skipped++
            $Results.Add([pscustomobject]@{Service=$svc.Name; Status="SKIPPED (exists)"; Duration="–"})
            continue
        }
    }

    # --- Build ---
    $startTime = Get-Date

    docker build `
        --tag  $svc.Image `
        --file $dockerfilePath `
        $Root

    $duration = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[✓] $($svc.Name) built in ${duration}s" -ForegroundColor Green
        $Passed++
        $Results.Add([pscustomobject]@{Service=$svc.Name; Status="OK"; Duration="${duration}s"})
    } else {
        Write-Host "[✗] $($svc.Name) FAILED after ${duration}s" -ForegroundColor Red
        $Failed++
        $Results.Add([pscustomobject]@{Service=$svc.Name; Status="FAILED"; Duration="${duration}s"})
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Build Summary" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
$Results | Format-Table -AutoSize

Write-Host "  Built  : $Passed" -ForegroundColor Green
Write-Host "  Skipped: $Skipped" -ForegroundColor DarkGray

if ($Failed -gt 0) {
    Write-Host "  Failed : $Failed" -ForegroundColor Red
    Write-Host ""
    Write-Host "  ✗ Fix the errors above, then re-run with -Force to rebuild failed images." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  ✓ All images ready. Next: scripts\deploy-to-k8s.ps1" -ForegroundColor Green
