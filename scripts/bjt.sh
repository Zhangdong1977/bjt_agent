#!/bin/bash
# Bid Review Agent System - Unified Management Script
# Usage: ./bjt.sh {start|stop|status|restart}

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Create logs directory
mkdir -p "$SCRIPT_DIR/logs"

export PYTHONPATH="$PWD"
BACKEND_DIR="$PWD/backend"
FRONTEND_DIR="$PWD/frontend"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

# PID file functions
get_pid() {
    local name=$1
    local pid_file="$SCRIPT_DIR/logs/${name}.pid"
    if [ -f "$pid_file" ]; then
        cat "$pid_file"
    fi
}

save_pid() {
    local name=$1
    local pid=$2
    echo "$pid" > "$SCRIPT_DIR/logs/${name}.pid"
}

remove_pid() {
    local name=$1
    rm -f "$SCRIPT_DIR/logs/${name}.pid"
}

is_running() {
    local pid=$1
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Start functions
start_celery_review() {
    log "Starting Celery Worker (review queue)..."
    cd "$BACKEND_DIR"
    celery -A celery_app worker --loglevel=info --concurrency=2 -Q review > "$SCRIPT_DIR/logs/celery_review.log" 2>&1 &
    save_pid "celery_review" "$!"
    log "Celery Review started (PID: $(get_pid celery_review))"
}

start_celery_parser() {
    log "Starting Celery Worker (parser queue)..."
    cd "$BACKEND_DIR"
    celery -A celery_app worker --loglevel=info --concurrency=2 -Q parser > "$SCRIPT_DIR/logs/celery_parser.log" 2>&1 &
    save_pid "celery_parser" "$!"
    log "Celery Parser started (PID: $(get_pid celery_parser))"
}

start_backend() {
    log "Starting Backend API Server..."
    cd "$BACKEND_DIR"
    python -m uvicorn main:app --host 0.0.0.0 --port 8000 > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
    save_pid "backend" "$!"
    log "Backend API started (PID: $(get_pid backend))"
}

start_frontend() {
    log "Starting Frontend Dev Server..."
    cd "$FRONTEND_DIR"
    npm run dev > "$SCRIPT_DIR/logs/frontend.log" 2>&1 &
    save_pid "frontend" "$!"
    log "Frontend started (PID: $(get_pid frontend))"
}

# Stop functions
stop_service() {
    local name=$1
    local pid=$(get_pid "$name")

    if [ -n "$pid" ] && is_running "$pid"; then
        log "Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true

        for i in {1..5}; do
            if ! is_running "$pid"; then
                break
            fi
            sleep 1
        done

        if is_running "$pid"; then
            warn "$name did not stop gracefully, force killing..."
            kill -9 "$pid" 2>/dev/null || true
        fi

        remove_pid "$name"
        log "$name stopped"
    fi
}

# Actions
do_start() {
    log "========================================"
    log "  Starting Bid Review Agent System"
    log "========================================"
    echo ""

    start_celery_review
    sleep 2
    start_celery_parser
    sleep 2
    start_backend
    sleep 2
    start_frontend

    echo ""
    log "========================================"
    log "  All services started!"
    log "========================================"
    echo ""
    echo "Backend API:  http://localhost:8000"
    echo "API Docs:     http://localhost:8000/docs"
    echo "Frontend:     http://localhost:3000"
    echo ""
    echo "Logs: $SCRIPT_DIR/logs/"
    echo ""
}

do_stop() {
    log "========================================"
    log "  Stopping Bid Review Agent System"
    log "========================================"
    echo ""

    stop_service "frontend"
    stop_service "backend"
    stop_service "celery_parser"
    stop_service "celery_review"

    echo ""
    log "========================================"
    log "  All services stopped!"
    log "========================================"
    echo ""
}

do_status() {
    log "========================================"
    log "  Bid Review Agent System Status"
    log "========================================"
    echo ""

    for name in celery_review celery_parser backend frontend; do
        pid=$(get_pid "$name")
        if is_running "$pid"; then
            echo -e "[$name] Running (PID: $pid)" "${GREEN}✓${NC}"
        else
            echo -e "[$name] Not running" "${RED}✗${NC}"
        fi
    done

    echo ""
    echo "Backend API Health:"
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "Backend API is healthy" "${GREEN}✓${NC}"
    else
        echo -e "Backend API is not responding" "${RED}✗${NC}"
    fi

    echo ""
    echo "Ports:"
    if lsof -i :8000 > /dev/null 2>&1; then
        echo -e ":8000 (Backend API) - in use" "${GREEN}✓${NC}"
    else
        echo -e ":8000 (Backend API) - not in use" "${RED}✗${NC}"
    fi

    if lsof -i :3000 > /dev/null 2>&1; then
        echo -e ":3000 (Frontend) - in use" "${GREEN}✓${NC}"
    else
        echo -e ":3000 (Frontend) - not in use" "${RED}✗${NC}"
    fi

    echo ""
}

do_restart() {
    log "Restarting services..."
    do_stop
    sleep 2
    do_start
}

# Main
case "$1" in
    start)   do_start ;;
    stop)    do_stop ;;
    status)  do_status ;;
    restart) do_restart ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        echo ""
        echo "Commands:"
        echo "  start   - Start all services"
        echo "  stop    - Stop all services"
        echo "  status  - Show service status"
        echo "  restart - Restart all services"
        exit 1
        ;;
esac
