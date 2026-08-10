#!/usr/bin/env pwsh
# ============================================================
# run-local.ps1 — Start all LLM Platform services locally
#
# Pre-requisites (run once):
#   1. pip install -r requirements.txt  (in repo root venv)
#   2. docker compose -f docker-compose.local.yml up -d   (Redis)
#   3. Ollama running: ollama serve  (separate terminal)
#      ollama pull llama3.2:3b
#
# Usage:
#   .\scripts\run-local.ps1            # start all services
#   .\scripts\run-local.ps1 -Stop      # kill all services
#   .\scripts\run-local.ps1 -Service audit_store   # start one only
# ============================================================
param(
    [switch]$Stop,
    [string]$Service = ""
)

$ROOT = Split-Path $PSScriptRoot -Parent
$ENV_FILE = "$ROOT\local.env"
$ENV_LOCAL_FILE = "$ROOT\local.env.local"   # optional, untracked, machine-specific overrides
$PYTHON = "python"

# ── Load .env into current session ──────────────────────────────────────────
function Load-Env {
    if (-not (Test-Path $ENV_FILE)) {
        Write-Host "ERROR: $ENV_FILE not found" -ForegroundColor Red
        exit 1
    }
    Get-Content $ENV_FILE | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $val  = $matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($name, $val, "Process")
        }
    }
    Write-Host "Loaded env from $ENV_FILE" -ForegroundColor DarkGray

    # Untracked override file (e.g. real DATABASE_URL) — loaded after local.env
    # so its values win. Never committed; see .gitignore.
    if (Test-Path $ENV_LOCAL_FILE) {
        Get-Content $ENV_LOCAL_FILE | ForEach-Object {
            if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
                $name = $matches[1].Trim()
                $val  = $matches[2].Trim()
                [System.Environment]::SetEnvironmentVariable($name, $val, "Process")
            }
        }
        Write-Host "Loaded local overrides from $ENV_LOCAL_FILE" -ForegroundColor DarkGray
    }
}

# ── Kill all service processes ───────────────────────────────────────────────
if ($Stop) {
    Write-Host "Stopping all local services..." -ForegroundColor Yellow
    Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "uvicorn.*(api_gateway|security_layer|intelligent_router|inference_adapter|cache_service|audit_store|model_registry|admin_portal|agent_framework)"
    } | ForEach-Object {
        Write-Host "  Killing PID $($_.Id): $($_.ProcessName)"
        $_ | Stop-Process -Force
    }
    # Also kill by port (fallback)
    @(8080,8081,8082,8083,8084,8086,8087,9200,5001) | ForEach-Object {
        $port = $_
        $proc = netstat -ano 2>$null | Select-String ":$port\s.*LISTENING" |
                ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1
        if ($proc -and $proc -match '^\d+$') {
            try { Stop-Process -Id ([int]$proc) -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    Write-Host "Done." -ForegroundColor Green
    exit 0
}

Load-Env

# ── Service definitions ──────────────────────────────────────────────────────
# Each entry: Name, WorkingDir (relative to ROOT), Module, Port
# Start order: leaf services first, then dependents, gateway last.
$services = @(
    @{ Name="model_registry";    Dir=".";                         Module="model_registry.main:app";    Port=5001; MetricsPort=$null; ExtraPythonPath=$null  }
    @{ Name="audit_store";       Dir=".";                         Module="audit_store.main:app";        Port=9200; MetricsPort=$null; ExtraPythonPath=$null  }
    @{ Name="inference_adapter"; Dir=".";                         Module="inference_adapter.main:app";  Port=8087; MetricsPort=9090;  ExtraPythonPath=$null  }
    @{ Name="cache_service";     Dir=".";                         Module="cache_service.main:app";      Port=8086; MetricsPort=9091;  ExtraPythonPath=$null  }
    @{ Name="intelligent_router";Dir=".";                         Module="intelligent_router.main:app"; Port=8082; MetricsPort=$null; ExtraPythonPath=$null  }
    @{ Name="agent_framework";   Dir="services/agent-framework";  Module="agent_framework.main:app";    Port=8083; MetricsPort=9092;  ExtraPythonPath=$ROOT  }
    @{ Name="security_layer";    Dir=".";                         Module="security_layer.main:app";     Port=8081; MetricsPort=$null; ExtraPythonPath=$null  }
    @{ Name="api_gateway";       Dir=".";                         Module="api_gateway.main:app";        Port=8080; MetricsPort=$null; ExtraPythonPath=$null  }
    @{ Name="admin_portal";      Dir=".";                         Module="admin_portal.main:app";       Port=8084; MetricsPort=$null; ExtraPythonPath=$null  }
)

# Filter to single service if requested
if ($Service) {
    $allNames = $services | ForEach-Object { $_.Name }
    $services = $services | Where-Object { $_.Name -eq $Service }
    if (-not $services) {
        Write-Host "Unknown service: $Service" -ForegroundColor Red
        Write-Host "Available: $($allNames -join ', ')"
        exit 1
    }
}

# ── Check pre-requisites ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "Checking pre-requisites..." -ForegroundColor Cyan

# Redis
try {
    $redisCheck = docker run --rm redis:7-alpine redis-cli -h host.docker.internal ping 2>$null
} catch {}
$redisDirect = (& redis-cli ping 2>$null)
if ($redisDirect -ne "PONG") {
    Write-Host "  [WARN] Redis not reachable on localhost:6379" -ForegroundColor Yellow
    Write-Host "         Run: docker compose -f docker-compose.local.yml up -d" -ForegroundColor DarkYellow
} else {
    Write-Host "  [OK]  Redis on localhost:6379" -ForegroundColor Green
}

# Ollama
try {
    $ollamaCheck = (Invoke-WebRequest "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop).StatusCode
    Write-Host "  [OK]  Ollama on localhost:11434" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Ollama not running on localhost:11434" -ForegroundColor Yellow
    Write-Host "         Run: ollama serve  (in a separate terminal)" -ForegroundColor DarkYellow
}

# Python importable?
$pyCheck = & $PYTHON -c "import fastapi, uvicorn, pydantic_settings" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Python dependencies not installed." -ForegroundColor Red
    Write-Host "          Run: pip install -r requirements.txt" -ForegroundColor Red
    exit 1
} else {
    Write-Host "  [OK]  Python dependencies" -ForegroundColor Green
}

# ── Launch services ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting services..." -ForegroundColor Cyan
Write-Host "(Each opens in a new terminal window)" -ForegroundColor DarkGray
Write-Host ""

foreach ($svc in $services) {
    $name   = $svc.Name
    $dir    = Join-Path $ROOT $svc.Dir
    $module = $svc.Module
    $port   = $svc.Port

    # Build env var string for the new window
    # Re-export all vars from local.env into the child process
    $envBlock = (Get-Content $ENV_FILE |
        Where-Object { $_ -match '^\s*[^#]' -and $_ -match '=' } |
        ForEach-Object {
            $parts = $_ -split '=', 2
            "`$env:$($parts[0].Trim()) = '$($parts[1].Trim())'"
        }) -join "; "

    # Layer in untracked local overrides (e.g. real DATABASE_URL), if present.
    if (Test-Path $ENV_LOCAL_FILE) {
        $localOverrides = (Get-Content $ENV_LOCAL_FILE |
            Where-Object { $_ -match '^\s*[^#]' -and $_ -match '=' } |
            ForEach-Object {
                $parts = $_ -split '=', 2
                "`$env:$($parts[0].Trim()) = '$($parts[1].Trim())'"
            }) -join "; "
        if ($localOverrides) { $envBlock += "; $localOverrides" }
    }

    # Override METRICS_PORT per-service to avoid port collisions
    if ($svc.MetricsPort) {
        $envBlock += "; `$env:METRICS_PORT = '$($svc.MetricsPort)'"
    }

    # Add repo root to PYTHONPATH for services that need it (e.g. agent_framework
    # runs from services/agent-framework/ but imports from shared/ at repo root)
    if ($svc.ExtraPythonPath) {
        $envBlock += "; `$env:PYTHONPATH = '$($svc.ExtraPythonPath)'"
    }

    $cmd = "$envBlock; Set-Location '$dir'; python -m uvicorn $module --host 0.0.0.0 --port $port --reload"

    Write-Host "  Starting $name on port $port..." -ForegroundColor White
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd -WindowStyle Normal

    Start-Sleep 1
}

# ── Wait for all services to come up ────────────────────────────────────────
Write-Host ""
Write-Host "Waiting for services to be ready..." -ForegroundColor Cyan
Start-Sleep 8

$checks = @(
    @{ Name="model_registry";    Url="http://localhost:5001/health" }
    @{ Name="audit_store";       Url="http://localhost:9200/health" }
    @{ Name="inference_adapter"; Url="http://localhost:8087/health" }
    @{ Name="cache_service";     Url="http://localhost:8086/health" }
    @{ Name="intelligent_router";Url="http://localhost:8082/health" }
    @{ Name="agent_framework";   Url="http://localhost:8083/health" }
    @{ Name="security_layer";    Url="http://localhost:8081/health" }
    @{ Name="api_gateway";       Url="http://localhost:8080/health" }
    @{ Name="admin_portal";      Url="http://localhost:8084/portal/health" }
)

foreach ($chk in $checks) {
    if ($Service -and $chk.Name -ne $Service) { continue }
    try {
        $resp = Invoke-WebRequest $chk.Url -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        $status = ($resp.Content | ConvertFrom-Json).status
        if ($resp.StatusCode -eq 200) {
            Write-Host "  [OK]  $($chk.Name) - $($chk.Url)" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] $($chk.Name) - HTTP $($resp.StatusCode)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [FAIL] $($chk.Name) - not ready yet (check its terminal)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Local stack is up. Demo endpoints:" -ForegroundColor Green
Write-Host "  API Gateway:      http://localhost:8080/v1/chat/completions" -ForegroundColor White
Write-Host "  Health:           http://localhost:8080/health" -ForegroundColor White
Write-Host "  Audit Store:      http://localhost:9200/audit/events" -ForegroundColor White
Write-Host "  Model Registry:   http://localhost:5001/models" -ForegroundColor White
Write-Host "  Admin Portal:     http://localhost:8084/portal/health" -ForegroundColor White
Write-Host "  Agent Framework:  http://localhost:8083/health" -ForegroundColor White
Write-Host ""
Write-Host "  To stop all:  .\scripts\run-local.ps1 -Stop" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan
