#!/bin/bash
# OCR Service - Standalone Management Script
# Usage: ./ocr_service.sh {start|stop|status|restart}
#
# 独立于 bjt.sh 运行，可部署在 OCR 专用服务器上。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
OCR_SERVICE_DIR="$PROJECT_DIR/ocr_service"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$LOG_DIR/ocr_service.pid"
PORT=8900

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p "$LOG_DIR"

log()   { echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"; }
warn()  { echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"; }
error() { echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"; }

# Conda environment check
check_conda_env() {
    local target_env="py311"
    local current_env="${CONDA_DEFAULT_ENV:-}"

    if [ "$current_env" != "$target_env" ]; then
        warn "Not in conda environment '$target_env', attempting to activate..."
        if command -v conda &> /dev/null; then
            eval "$(conda shell.bash hook 2>/dev/null)" || true
            conda activate "$target_env" 2>/dev/null || {
                error "Failed to activate conda environment '$target_env'"
                error "Please run: conda activate $target_env"
                exit 1
            }
            log "Activated conda environment '$target_env'"
        else
            error "Conda not found. Please activate '$target_env' manually."
            exit 1
        fi
    fi
}

get_pid() {
    if [ -f "$PID_FILE" ]; then cat "$PID_FILE"; fi
}

save_pid() {
    echo "$1" > "$PID_FILE"
}

is_running() {
    local pid=$1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

do_start() {
    check_conda_env

    local pid=$(get_pid)
    if is_running "$pid"; then
        warn "OCR Service already running (PID: $pid)"
        return 0
    fi

    # Also check by port
    local port_pid=$(lsof -ti :$PORT 2>/dev/null | head -1)
    if [ -n "$port_pid" ]; then
        warn "Port $PORT already in use (PID: $port_pid)"
        return 1
    fi

    log "Starting OCR Service..."
    cd "$OCR_SERVICE_DIR"
    python -m uvicorn main:app --host 0.0.0.0 --port $PORT > "$LOG_DIR/ocr_service.log" 2>&1 &
    save_pid "$!"
    sleep 2

    if is_running "$(get_pid)"; then
        log "OCR Service started (PID: $(get_pid), port: $PORT)"
    else
        error "OCR Service failed to start. Check $LOG_DIR/ocr_service.log"
        return 1
    fi
}

do_stop() {
    local pid=$(get_pid)

    # PID-based stop
    if is_running "$pid"; then
        log "Stopping OCR Service (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        for i in {1..5}; do
            is_running "$pid" || break
            sleep 1
        done
        if is_running "$pid"; then
            warn "Force killing OCR Service..."
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi

    # Port-based cleanup
    local port_pids=$(lsof -ti :$PORT 2>/dev/null)
    if [ -n "$port_pids" ]; then
        for p in $port_pids; do
            log "Killing orphaned process on port $PORT (PID: $p)..."
            kill -9 "$p" 2>/dev/null || true
        done
    fi

    rm -f "$PID_FILE"
    log "OCR Service stopped"
}

do_status() {
    local pid=$(get_pid)
    echo ""
    echo "OCR Service Status:"
    echo "  Port: $PORT"
    echo "  PID file: $PID_FILE"

    if is_running "$pid"; then
        echo -e "  Process: Running (PID: $pid) ${GREEN}OK${NC}"
    else
        echo -e "  Process: Not running ${RED}STOPPED${NC}"
    fi

    echo ""
    echo "Health Check:"
    if curl -s "http://localhost:$PORT/health" > /dev/null 2>&1; then
        echo -e "  http://localhost:$PORT/health ${GREEN}OK${NC}"
    else
        echo -e "  http://localhost:$PORT/health ${RED}NOT RESPONDING${NC}"
    fi
    echo ""
}

do_restart() {
    do_stop
    sleep 2
    do_start
}

case "$1" in
    start)   do_start ;;
    stop)    do_stop ;;
    status)  do_status ;;
    restart) do_restart ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
