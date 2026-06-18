# ============================================================
# bjt-agent local management script (PowerShell)
# Single-window, background services, file logs - matches bjt.sh on Linux.
#
# Usage:
#   .\bjt.ps1 start     Start all services (backend + celery + frontend)
#   .\bjt.ps1 stop      Stop all services
#   .\bjt.ps1 status    Show service status, ports and log paths
#   .\bjt.ps1 restart   Stop then start
#   .\bjt.ps1 logs <s>  Tail a service log: backend | celery | frontend | all
#
# Logs      -> scripts\logs\*.log
# PID files -> scripts\logs\*.pid
# Conda env : bjt-agent   Node: D:\nvm\v20.19.6
# ============================================================

param(
    [Parameter(Mandatory = $false, Position = 0)]
    [ValidateSet("start", "stop", "status", "restart", "logs", "")]
    [string]$Action = "",

    [Parameter(Mandatory = $false, Position = 1)]
    [string]$LogTarget = ""
)

$ErrorActionPreference = "SilentlyContinue"

# ---- Paths ----
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$LogDir    = Join-Path $ScriptDir "logs"           # scripts/logs (matches main.py + bjt.sh)
$BackendDir   = Join-Path $RootDir "backend"
$FrontendDir  = Join-Path $RootDir "frontend"
$EnvFile      = Join-Path $BackendDir ".env"

# ---- Local Redis (7.4.9, msys2 build) ----
# Bundled under scripts/redis-7. The msys2 binary mangles absolute Windows paths
# (E:\... -> /cygdrive/e/.../E:\...), so we MUST launch it with WorkingDirectory
# set to its own folder and pass the config as a relative filename.
$RedisDir    = Join-Path $ScriptDir "redis-7"
$RedisExe    = Join-Path $RedisDir "redis-server.exe"
$RedisCliExe = Join-Path $RedisDir "redis-cli.exe"
$RedisConf   = "redis-bjt.conf"   # relative - resolved against $RedisDir at launch
$RedisPort   = 6379

# ---- Toolchain ----
$CondaEnv  = "D:\miniconda3\envs\bjt-agent"
$PythonExe = Join-Path $CondaEnv "python.exe"
$CeleryExe = Join-Path $CondaEnv "Scripts\celery.exe"
$UvicornExe = Join-Path $CondaEnv "Scripts\uvicorn.exe"

# nvm node
$NodeDir   = "D:\nvm\v20.19.6"
if (-not (Test-Path (Join-Path $NodeDir "npm.cmd"))) { $NodeDir = "D:\nvm4w\nodejs" }
$NpmExe    = Join-Path $NodeDir "npm.cmd"

$BackendHost = "0.0.0.0"
$BackendPort = 8000
$FrontHost   = "0.0.0.0"
$FrontPort   = 3000

# Inject toolchain into PATH for child processes (so uvicorn/celery/npm find deps)
$env:PYTHONPATH = $RootDir
$env:PATH = "$NodeDir;$CondaEnv;$CondaEnv\Scripts;" + $env:PATH

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ---- Logging helpers ----
function Write-Log { param([string]$msg) Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] $msg" -ForegroundColor Green }
function Write-Warn2 { param([string]$msg) Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] WARNING: $msg" -ForegroundColor Yellow }
function Write-Err2 { param([string]$msg) Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] ERROR: $msg" -ForegroundColor Red }

# ---- PID file helpers ----
function Get-PidFile($name)   { return Join-Path $LogDir "$name.pid" }
function Get-SavedPid($name)  {
    $f = Get-PidFile $name
    if (Test-Path $f) {
        $v = (Get-Content $f -Raw -ErrorAction SilentlyContinue).Trim()
        if ($v -match '^\d+$') { return [int]$v }
    }
    return $null
}
function Save-Pid($name, $procId) { Set-Content -Path (Get-PidFile $name) -Value "$procId" -Encoding UTF8 }
function Remove-Pid($name) { $f = Get-PidFile $name; if (Test-Path $f) { Remove-Item $f -Force } }

function Test-PidAlive($procId) {
    if (-not $procId) { return $false }
    return $null -ne (Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

# Kill a process tree (parent + children). Stop-Process on the parent leaves
# uvicorn-reloader child and celery worker children orphaned.
function Stop-ProcessTree($procId) {
    if (-not $procId) { return }
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $procId }
    foreach ($ch in $children) { Stop-ProcessTree $ch.ProcessId }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

# ---- Pre-flight checks ----
function Assert-Environment {
    $ok = $true
    if (-not (Test-Path $EnvFile))  { Write-Err2 "Missing $EnvFile"; $ok = $false }
    if (-not (Test-Path $PythonExe)) { Write-Err2 "Missing $PythonExe (conda env bjt-agent?)"; $ok = $false }
    if (-not (Test-Path $NpmExe))    { Write-Err2 "Missing $NpmExe (nvm node?)"; $ok = $false }
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules\vite\package.json"))) {
        Write-Err2 "Frontend deps missing - run: npm install in $FrontendDir"
        $ok = $false
    }
    if (-not (Test-Path $RedisExe))  { Write-Err2 "Missing $RedisExe (local Redis?)"; $ok = $false }
    return $ok
}

# ---- Starters: launch a detached background process with OS-level log redirect ----
# Wrap each command in `cmd /c "..." > log 2>&1` so the redirect is done by cmd.exe
# at the OS level (independent of this script's lifetime, exactly like bash on Linux).
# Start-Process reliably returns a PID for cmd.exe, and cmd stays alive as long as
# the wrapped child runs.
#
# IMPORTANT: the redirect target must NOT collide with files the process writes
# itself via its internal logger, or two writers fight over the file lock and you
# get a PermissionError storm that crashes the logger.
#   - backend: main.py _setup_app_logging() writes scripts/logs/backend.log
#               (ConcurrentRotatingFileHandler). We redirect to backend.stdout.log.
#   - celery : logging_config.py writes to --logfile if given, else stderr.
#               We pass NO --logfile, so it logs to stderr -> backend.stdout.log... but
#               to keep them separate we redirect to celery.stdout.log.
#   - frontend: vite has no internal file logger, logs to stdout -> frontend.log.
function Start-One($name, $file, $argArray, $workDir) {
    # backend & celery keep .stdout.log for the OS redirect; frontend (no internal
    # file logger) uses .log directly. redis also logs to stdout only, treat like
    # backend/celery to avoid confusion with project-internal .log files.
    $ext = if ($name -in @("backend","celery","redis")) { "stdout.log" } else { "log" }
    $out = Join-Path $LogDir "$name.$ext"
    if (Test-Path $out) { Clear-Content $out }

    # Build the full command line with proper quoting
    $quotedArgs = $argArray | ForEach-Object {
        if ($_ -match '\s') { "`"$_`"" } else { "$_" }
    }
    $innerCmd = "$file " + ($quotedArgs -join ' ')
    # cmd /c with redirection; ^& escapes & inside the outer quoted string
    $cmdLine = '/c "' + $innerCmd + ' 1> "' + $out + '" 2>&1"'

    $p = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdLine -WorkingDirectory $workDir `
        -PassThru -WindowStyle Hidden
    if ($p) {
        Save-Pid $name $p.Id
        Write-Log "$name started (PID: $($p.Id))  log: $name.$ext"
    } else {
        Write-Warn2 "$name process handle not captured"
    }
}

function Start-Backend {
    Write-Log "Starting backend (uvicorn)..."
    Start-One "backend" $UvicornExe @("main:app", "--host", $BackendHost, "--port", $BackendPort, "--reload") $BackendDir
}
function Start-Celery {
    Write-Log "Starting Celery worker (review,parser,celery queues, pool=solo)..."
    # NOTE: do NOT pass --logfile here. The project's logging_config.py already
    # configures a concurrent_log_handler (ConcurrentRotatingFileHandler) writing
    # to scripts/logs/celery.log. Passing --logfile=celery.log too causes a
    # PermissionError storm (two writers on the same file) that crashes the worker.
    # We only redirect stdout/stderr via Start-One's cmd wrapper, which captures
    # the worker boot/traceback output that the rotating handler may miss.
    Start-One "celery" $CeleryExe @("-A", "celery_app", "worker", "--loglevel=info", "-Q", "review,parser,celery", "--pool=solo") $BackendDir
}
function Start-Frontend {
    Write-Log "Starting frontend (vite)..."
    # Run vite directly via node (bypasses npm.cmd wrapper which detaches its child,
    # so the captured PID would die with the cmd wrapper). This keeps the node PID.
    $viteJs = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $viteJs)) { Write-Err2 "Missing $viteJs - run npm install in frontend"; return }
    $nodeExe = Join-Path $NodeDir "node.exe"
    Start-One "frontend" $nodeExe @($viteJs, "--host", $FrontHost, "--port", $FrontPort) $FrontendDir
}

# ---- Local Redis management ----
# Redis is a hard dependency for celery broker/backend + SSE streams. The bundled
# msys2 build mangles absolute paths, so we launch it with WorkingDirectory=$RedisDir
# and pass the config as a bare filename. redis-server daemonizes itself on Windows
# when stdout is redirected to a file (the cmd /c wrapper in Start-One handles this),
# so we reuse Start-One for consistent logging.
function Test-RedisUp {
    if (-not (Test-Path $RedisCliExe)) { return $false }
    $r = & $RedisCliExe -h 127.0.0.1 -p $RedisPort PING 2>$null
    return ($r -match "PONG")
}
function Start-Redis {
    if (Test-RedisUp) {
        Write-Log "redis already running on :$RedisPort (reusing)"
        Save-Pid "redis" 0   # marker: external/pre-existing
        return $true
    }
    if (-not (Test-Path $RedisExe)) { Write-Err2 "Missing $RedisExe"; return $false }
    Write-Log "Starting local Redis 7 on :$RedisPort ..."
    # msys2 redis-server needs relative paths; Start-One already supports -WorkingDirectory.
    # We pass the bare conf filename (resolved against $RedisDir).
    Start-One "redis" $RedisExe @($RedisConf) $RedisDir
    Start-Sleep -Seconds 2
    if (Test-RedisUp) {
        Write-Log "redis PING OK"
        return $true
    } else {
        Write-Err2 "redis did not respond to PING - check $LogDir/redis.stdout.log"
        return $false
    }
}
function Stop-Redis {
    $procId = Get-SavedPid "redis"
    # Prefer graceful SHUTDOWN via CLI (lets Redis flush), then fall back to kill.
    if (Test-RedisUp) {
        if (Test-Path $RedisCliExe) {
            Write-Log "Shutting down redis (SHUTDOWN NOSAVE)..."
            & $RedisCliExe -h 127.0.0.1 -p $RedisPort SHUTDOWN NOSAVE 2>$null
            Start-Sleep -Milliseconds 800
        }
    }
    if ($procId -and $procId -gt 0 -and (Test-PidAlive $procId)) {
        Write-Log "Stopping redis (PID: $procId) + children..."
        Stop-ProcessTree $procId
    }
    Remove-Pid "redis"
}

# ---- Stoppers ----
function Stop-One($name, $pattern) {
    $procId = Get-SavedPid $name
    if (Test-PidAlive $procId) {
        Write-Log "Stopping $name (PID: $procId) + children..."
        Stop-ProcessTree $procId
        Start-Sleep -Milliseconds 500
    }
    # Fallback: kill by command-line pattern (orphaned reloaders/workers)
    if ($pattern) {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*$pattern*" } |
            ForEach-Object {
                Write-Log "Killing leftover $name process (PID: $($_.ProcessId))..."
                Stop-ProcessTree $_.ProcessId
            }
    }
    Remove-Pid $name
}

# ---- Actions ----
function Do-Start {
    Write-Log "========================================"
    Write-Log "  Starting bjt-agent (redis+backend+celery+frontend)"
    Write-Log "========================================"
    if (-not (Assert-Environment)) { Write-Err2 "Pre-flight failed. Aborting."; return }

    # Redis MUST come first - celery broker & backend SSE streams depend on it.
    if (-not (Start-Redis)) { Write-Err2 "Redis failed to start. Aborting."; return }

    Start-Backend
    Start-Sleep -Seconds 2
    Start-Celery
    Start-Sleep -Seconds 2
    Start-Frontend

    Write-Host ""
    Write-Log "========================================"
    Write-Log "  All services launched (background)"
    Write-Log "========================================"
    Write-Host ""
    Write-Host "Redis      : 127.0.0.1:$RedisPort"
    Write-Host "Backend API : http://localhost:$BackendPort"
    Write-Host "API Docs    : http://localhost:$BackendPort/docs"
    Write-Host "Frontend    : http://localhost:$FrontPort"
    Write-Host "Logs        : $LogDir"
    Write-Host ""
    Write-Host "Check health: .\bjt.ps1 status"
    Write-Host "Follow logs : .\bjt.ps1 logs all"
    Write-Host ""
}

function Do-Stop {
    Write-Log "========================================"
    Write-Log "  Stopping bjt-agent"
    Write-Log "========================================"
    Stop-One "frontend" "*vite*"
    Stop-One "backend"  "*uvicorn*"
    Stop-One "celery"   "*celery*"
    # Redis last - workers/backend may still flush on shutdown.
    Stop-Redis
    Write-Host ""
    Write-Log "All services stopped."
}

function Get-ServiceState($name, $pattern) {
    $procId = Get-SavedPid $name
    if (-not (Test-PidAlive $procId)) {
        # fallback by pattern
        $m = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like "*$pattern*" } | Select-Object -First 1
        if ($m) { $procId = $m.ProcessId; Save-Pid $name $procId }
    }
    return @{ Pid = $procId; Alive = (Test-PidAlive $procId) }
}

function Do-Status {
    Write-Log "========================================"
    Write-Log "  bjt-agent status"
    Write-Log "========================================"
    $svcs = @(
        @{Name="redis";    Pattern="*redis-server*redis-bjt*"; Port=$RedisPort;    Url=$null;           Probe=$true},
        @{Name="backend";  Pattern="*uvicorn*main:app*";      Port=$BackendPort;  Url="http://localhost:$BackendPort/health"; Probe=$false},
        @{Name="celery";   Pattern="*celery*worker*";         Port=$null;         Url=$null;           Probe=$false},
        @{Name="frontend"; Pattern="*vite*";                  Port=$FrontPort;    Url="http://localhost:$FrontPort/"; Probe=$false}
    )
    foreach ($s in $svcs) {
        # Redis uses a PING probe instead of a saved PID (it may have been started
        # externally and reused).
        if ($s.Name -eq "redis") {
            $alive = Test-RedisUp
            $procId = Get-SavedPid "redis"
            if ($alive) {
                Write-Host ("  [{0,-9}] RUNNING  :{1} PONG" -f $s.Name, $s.Port) -ForegroundColor Green
            } else {
                Write-Host ("  [{0,-9}] STOPPED" -f $s.Name) -ForegroundColor Red
            }
            continue
        }
        $st = Get-ServiceState $s.Name $s.Pattern
        if ($st.Alive) {
            Write-Host ("  [{0,-9}] RUNNING  PID {1}" -f $s.Name, $st.Pid) -ForegroundColor Green
        } else {
            Write-Host ("  [{0,-9}] STOPPED" -f $s.Name) -ForegroundColor Red
        }
        if ($s.Port) {
            $listening = Get-NetTCPConnection -State Listen -LocalPort $s.Port -ErrorAction SilentlyContinue
            $pstate = if ($listening) { "LISTEN" } else { "FREE  " }
            Write-Host ("             :{0} {1}" -f $s.Port, $pstate) -ForegroundColor Cyan
        }
        if ($s.Url -and $st.Alive) {
            try {
                $r = Invoke-WebRequest -Uri $s.Url -UseBasicParsing -TimeoutSec 3
                Write-Host ("             HTTP {0} OK" -f $r.StatusCode) -ForegroundColor Green
            } catch {
                Write-Host "             HTTP probe failed" -ForegroundColor Yellow
            }
        }
    }
    Write-Host ""
    Write-Host "Logs: $LogDir" -ForegroundColor Cyan
    Get-ChildItem $LogDir -Filter "*.log" -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ("    {0,-18} {1,8:N0} bytes" -f $_.Name, $_.Length) }
    Write-Host ""
}

function Do-Logs($target) {
    # tail -f a single service log; 'all' shows last lines of each then prints status hint.
    if (-not $target -or $target -eq "all") {
        foreach ($t in @("backend","celery","frontend")) {
            $f = Join-Path $LogDir "$t.log"
            if (Test-Path $f) {
                Write-Host "=== $t.log (last 30 lines) ===" -ForegroundColor Cyan
                Get-Content $f -Tail 30 -ErrorAction SilentlyContinue
                Write-Host ""
            }
        }
        Write-Host "Follow a single service: .\bjt.ps1 logs backend | celery | frontend" -ForegroundColor Cyan
        return
    }
    $f = Join-Path $LogDir "$target.log"
    if (-not (Test-Path $f)) { Write-Warn2 "No log: $f"; return }
    Write-Host "Following $f (Ctrl+C to exit)" -ForegroundColor Cyan
    Get-Content $f -Wait -Tail 50
}

# ---- Dispatch ----
switch ($Action) {
    "start"   { Do-Start }
    "stop"    { Do-Stop }
    "status"  { Do-Status }
    "restart" { Do-Stop; Start-Sleep -Seconds 2; Do-Start }
    "logs"    { Do-Logs $LogTarget }
    default {
        Write-Host "bjt-agent management script"
        Write-Host ""
        Write-Host "Usage: .\bjt.ps1 {start|stop|status|restart|logs [backend|celery|frontend|all]}"
        Write-Host ""
        Write-Host "  start           Start backend + celery + frontend (background, logged)"
        Write-Host "  stop            Stop all services"
        Write-Host "  status          Show service status, ports, HTTP health, log files"
        Write-Host "  restart         Stop then start"
        Write-Host "  logs <name>     tail -f a service log (backend|celery|frontend|all)"
        Write-Host ""
        Write-Host "Logs      : $LogDir"
        Write-Host "Conda env : bjt-agent"
        Write-Host "Node      : $NodeDir"
    }
}
