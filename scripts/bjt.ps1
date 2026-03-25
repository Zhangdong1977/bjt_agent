# Bid Review Agent System - Unified Management Script (PowerShell)
# Usage: .\bjt.ps1 {start|stop|status|restart}

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("start", "stop", "status", "restart")]
    [string]$Action
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $RootDir "logs"

# Create logs directory if not exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"

# Use ssirs conda environment
$PythonExe = "D:\miniconda3\envs\ssirs\python.exe"
$CeleryExe = "D:\miniconda3\envs\ssirs\python.exe"

$env:PYTHONPATH = $RootDir

# Color codes
function Write-Log { param([string]$msg) Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] WARNING: $msg" -ForegroundColor Yellow }
function Write-Err { param([string]$msg) Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] ERROR: $msg" -ForegroundColor Red }

# PID file functions
function Get-PidFileName($name) { return Join-Path $LogDir "$name.pid" }

function Get-ServicePid($name) {
    $pidFile = Get-PidFileName $name
    if (Test-Path $pidFile) {
        $content = Get-Content $pidFile -Raw -ErrorAction SilentlyContinue
        if ($content) {
            $procId = $content.Trim()
            if ($procId -match '^\d+$') {
                return [int]$procId
            }
        }
    }
    return $null
}

function Save-ServicePid($name, $procId) {
    $pidFile = Get-PidFileName $name
    $procId | Out-File -FilePath $pidFile -Encoding UTF8
}

function Remove-ServicePid($name) {
    $pidFile = Get-PidFileName $name
    if (Test-Path $pidFile) { Remove-Item $pidFile }
}

function Test-ServiceRunning($procId) {
    if ($null -eq $procId) { return $false }
    return $null -ne (Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

function Get-ServiceByPattern($pattern) {
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$pattern*" }
}

# Start functions with logging
function Start-CeleryReview {
    Write-Log "Starting Celery Worker (review queue)..."
    Set-Location $BackendDir
    $logFile = Join-Path $LogDir "celery_review.log"
    $errFile = Join-Path $LogDir "celery_review.err"
    $process = Start-Process -FilePath $CeleryExe -ArgumentList "-m celery -A celery_app worker --loglevel=info --concurrency=2 -Q review" -PassThru -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile
    Set-Location $RootDir
    if ($process) {
        Save-ServicePid "celery_review" $process.Id
        Write-Log "Celery Review started (PID: $($process.Id))"
    } else {
        Write-Warn "Celery Review may have started but process handle not captured"
    }
}

function Start-CeleryParser {
    Write-Log "Starting Celery Worker (parser queue)..."
    Set-Location $BackendDir
    $logFile = Join-Path $LogDir "celery_parser.log"
    $errFile = Join-Path $LogDir "celery_parser.err"
    $process = Start-Process -FilePath $CeleryExe -ArgumentList "-m celery -A celery_app worker --loglevel=info --concurrency=2 -Q parser" -PassThru -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile
    Set-Location $RootDir
    if ($process) {
        Save-ServicePid "celery_parser" $process.Id
        Write-Log "Celery Parser started (PID: $($process.Id))"
    } else {
        Write-Warn "Celery Parser may have started but process handle not captured"
    }
}

function Start-Backend {
    Write-Log "Starting Backend API Server..."
    Set-Location $BackendDir
    $logFile = Join-Path $LogDir "backend.log"
    $errFile = Join-Path $LogDir "backend.err"
    $process = Start-Process -FilePath $PythonExe -ArgumentList "-m uvicorn main:app --host 0.0.0.0 --port 8000" -PassThru -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile
    Set-Location $RootDir
    if ($process) {
        Save-ServicePid "backend" $process.Id
        Write-Log "Backend API started (PID: $($process.Id))"
    } else {
        Write-Warn "Backend API may have started but process handle not captured"
    }
}

function Start-Frontend {
    Write-Log "Starting Frontend Dev Server..."
    Set-Location $FrontendDir
    $logFile = Join-Path $LogDir "frontend.log"
    $errFile = Join-Path $LogDir "frontend.err"
    $process = Start-Process -FilePath "npm" -ArgumentList "run dev" -PassThru -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile
    Set-Location $RootDir
    if ($process) {
        Save-ServicePid "frontend" $process.Id
        Write-Log "Frontend started (PID: $($process.Id))"
    } else {
        Write-Warn "Frontend may have started but process handle not captured"
    }
}

# Stop functions
function Stop-ServiceByName {
    param([string]$name, [string]$pattern)

    $procId = Get-ServicePid $name

    if ($procId -and (Test-ServiceRunning $procId)) {
        Write-Log "Stopping $name (PID: $procId)..."
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500

        if (Test-ServiceRunning $procId) {
            Write-Warn "$name did not stop gracefully, force killing..."
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Remove-ServicePid $name
        Write-Log "$name stopped"
    }

    # Fallback: kill by pattern
    if ($pattern) {
        Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$pattern*" } | ForEach-Object {
            Write-Log "Killing remaining $name process (PID: $($_.ProcessId))..."
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

# Main actions
function Clear-Logs {
    Write-Log "Cleaning up old logs..."
    Get-ChildItem -Path $LogDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @('.log', '.err', '.pid') } | ForEach-Object {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Log "Logs cleaned"
}

function Do-Start {
    Write-Log "========================================"
    Write-Log "  Starting Bid Review Agent System"
    Write-Log "========================================"
    Write-Host ""

    Clear-Logs
    Write-Host ""

    Start-CeleryReview
    Start-Sleep -Seconds 2
    Start-CeleryParser
    Start-Sleep -Seconds 2
    Start-Backend
    Start-Sleep -Seconds 2
    Start-Frontend

    Write-Host ""
    Write-Log "========================================"
    Write-Log "  All services started!"
    Write-Log "========================================"
    Write-Host ""
    Write-Host "Backend API:  http://localhost:8000"
    Write-Host "API Docs:     http://localhost:8000/docs"
    Write-Host "Frontend:     http://localhost:3000"
    Write-Host ""
    Write-Host "Logs: $LogDir"
    Write-Host ""
}

function Do-Stop {
    Write-Log "========================================"
    Write-Log "  Stopping Bid Review Agent System"
    Write-Log "========================================"
    Write-Host ""

    Stop-ServiceByName "frontend" "vite"
    Stop-ServiceByName "backend" "uvicorn"
    Stop-ServiceByName "celery_parser" "celery.*parser"
    Stop-ServiceByName "celery_review" "celery.*review"

    Write-Host ""
    Write-Log "========================================"
    Write-Log "  All services stopped!"
    Write-Log "========================================"
    Write-Host ""
}

function Do-Status {
    Write-Log "========================================"
    Write-Log "  Bid Review Agent System Status"
    Write-Log "========================================"
    Write-Host ""

    $services = @(
        @{Name="celery_review"; Pattern="celery.*review"},
        @{Name="celery_parser"; Pattern="celery.*parser"},
        @{Name="backend"; Pattern="uvicorn"},
        @{Name="frontend"; Pattern="vite"}
    )

    foreach ($svc in $services) {
        $procId = Get-ServicePid $svc.Name
        $running = Test-ServiceRunning $procId
        if (-not $running) {
            # Fallback: check by pattern
            $matchingProcs = Get-ServiceByPattern $svc.Pattern
            if ($matchingProcs) {
                $procId = $matchingProcs[0].ProcessId
                $running = $true
                Save-ServicePid $svc.Name $procId  # Update PID file
            }
        }
        if ($running) {
            Write-Host "[$($svc.Name)] Running (PID: $procId)" -ForegroundColor Green
        } else {
            Write-Host "[$($svc.Name)] Not running" -ForegroundColor Red
        }
    }

    Write-Host ""
    Write-Host "Backend API Health:"

    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            Write-Host "Backend API is healthy" -ForegroundColor Green
        } else {
            Write-Host "Backend API returned status $($response.StatusCode)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Backend API is not responding" -ForegroundColor Red
    }

    Write-Host ""
    Write-Host "Ports:"

    $backendPort = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($backendPort) {
        Write-Host ":8000 (Backend API) - in use" -ForegroundColor Green
    } else {
        Write-Host ":8000 (Backend API) - not in use" -ForegroundColor Red
    }

    $frontendPort = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($frontendPort) {
        Write-Host ":3000 (Frontend) - in use" -ForegroundColor Green
    } else {
        Write-Host ":3000 (Frontend) - not in use" -ForegroundColor Red
    }

    Write-Host ""
}

function Do-Restart {
    Write-Log "========================================"
    Write-Log "  Restarting Bid Review Agent System"
    Write-Log "========================================"
    Write-Host ""

    Do-Stop
    Start-Sleep -Seconds 2
    Do-Start
}

# Command handler
switch ($Action) {
    "start"   { Do-Start }
    "stop"    { Do-Stop }
    "status"  { Do-Status }
    "restart" { Do-Restart }
    default {
        Write-Host "Usage: .\bjt.ps1 {start|stop|status|restart}"
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  start   - Start all services"
        Write-Host "  stop    - Stop all services"
        Write-Host "  status  - Show service status"
        Write-Host "  restart - Restart all services"
        exit 1
    }
}
