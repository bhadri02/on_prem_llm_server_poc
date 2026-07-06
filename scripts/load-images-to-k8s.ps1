#!/usr/bin/env pwsh
# =============================================================================
# scripts/load-images-to-k8s.ps1
#
# Loads locally built Docker images into Docker Desktop's containerd
# (k8s.io namespace) so Kubernetes pods can use them without a registry.
#
# How it works:
#   docker save <image> | docker run --rm --privileged --pid=host -i alpine
#       nsenter -t 1 -m -u -n -i -- ctr -n k8s.io images import -
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\load-images-to-k8s.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\load-images-to-k8s.ps1 -Service api-gateway
# =============================================================================

param(
    [string]$Tag      = "poc",
    [string]$Registry = "registry.local",
    [string]$Service  = ""        # load only this service (empty = all)
)

$ErrorActionPreference = "Stop"

$AllImages = @(
    "$Registry/api-gateway:$Tag"
    "$Registry/security-layer:$Tag"
    "$Registry/router:$Tag"
    "$Registry/cache-service:$Tag"
    "$Registry/inference-adapter:$Tag"
    "$Registry/audit-store:$Tag"
    "$Registry/model-registry:$Tag"
    "$Registry/admin-portal:$Tag"
    "$Registry/agent-framework:$Tag"
    "$Registry/portal-ui:$Tag"
)

$Images = if ($Service) {
    $AllImages | Where-Object { $_ -like "*/$Service`:*" }
} else {
    $AllImages
}

if ($Service -and $Images.Count -eq 0) {
    Write-Host "[ERROR] No image found matching service '$Service'" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Loading images into Docker Desktop containerd     " -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Method: docker save | nsenter ctr import"
Write-Host "  Count : $($Images.Count) images"
Write-Host ""

$Passed  = 0
$Failed  = 0
$Results = [System.Collections.Generic.List[pscustomobject]]::new()

foreach ($image in $Images) {

    Write-Host "────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host "[LOAD] $image" -ForegroundColor Yellow

    $startTime = Get-Date

    # Verify image exists locally first
    $exists = docker image inspect $image 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[SKIP] Image not found locally — build it first." -ForegroundColor DarkYellow
        $Results.Add([pscustomobject]@{Image=$image; Status="SKIPPED (not built)"; Duration="–"})
        continue
    }

    # Save and import into containerd k8s.io namespace
    # The alpine nsenter approach accesses the host's PID 1 mount namespace
    # where Docker Desktop's containerd socket lives
    $proc = Start-Process -FilePath "docker" `
        -ArgumentList @(
            "run", "--rm", "--privileged", "--pid=host", "-i",
            "alpine:3.19",
            "nsenter", "-t", "1", "-m", "-u", "-n", "-i", "--",
            "ctr", "-n", "k8s.io", "images", "import", "--all-platforms", "-"
        ) `
        -RedirectStandardInput  "\\.\pipe\docker_output_$PID" `
        -PassThru `
        -Wait:$false `
        -NoNewWindow

    # Use pipeline approach instead
    $saveAndLoad = "docker save $image | docker run --rm --privileged --pid=host -i alpine:3.19 nsenter -t 1 -m -u -n -i -- ctr -n k8s.io images import -"
    
    Invoke-Expression $saveAndLoad
    $exitCode = $LASTEXITCODE

    $duration = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)

    if ($exitCode -eq 0) {
        Write-Host "[✓] $image loaded in ${duration}s" -ForegroundColor Green
        $Passed++
        $Results.Add([pscustomobject]@{Image=$image; Status="OK"; Duration="${duration}s"})
    } else {
        Write-Host "[✗] $image FAILED (exit $exitCode)" -ForegroundColor Red
        $Failed++
        $Results.Add([pscustomobject]@{Image=$image; Status="FAILED"; Duration="${duration}s"})
    }
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Summary" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
$Results | Format-Table -AutoSize

if ($Failed -gt 0) {
    Write-Host "  ✗ $Failed image(s) failed to load." -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ All $Passed images loaded into containerd. Ready to deploy." -ForegroundColor Green
