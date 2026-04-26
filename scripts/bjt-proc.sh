#!/bin/bash
# Bid Review Agent System - Production Management Script
# Usage: ./bjt-proc.sh {start|stop|status|restart|logs}

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Production directories
mkdir -p "$SCRIPT_DIR/logs"
mkdir -p "$SCRIPT_DIR/pids"

export PYTHONPATH="$PWD"
BACKEND_DIR="$PWD/backend"
FRONTEND_DIR="$PWD/frontend"
FRONTEND_DIST="$FRONTEND_DIR/dist"
RAG_MEMORY_DIR="$PWD/rag_memory_service"

# Production ports
BACKEND_PORT=8001
FRONTEND_PORT=3000
RAG_MEMORY_PORT=3001

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
    local pid_file="$SCRIPT_DIR/pids/${name}.pid"
    if [ -f "$pid_file" ]; then
        cat "$pid_file"
    fi
}

save_pid() {
    local name=$1
    local pid=$2
    echo "$pid" > "$SCRIPT_DIR/pids/${name}.pid"
}

remove_pid() {
    local name=$1
    rm -f "$SCRIPT_DIR/pids/${name}.pid"
}

is_running() {
    local pid=$1
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Check if port is in use
is_port_in_use() {
    local port=$1
    if lsof -i :$port > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

# Build frontend for production
build_frontend_only() {
    log "Building frontend for production..."
    cd "$FRONTEND_DIR"

    if [ ! -d "node_modules" ]; then
        warn "node_modules not found, installing dependencies..."
        npm install
    fi

    npm run build
    log "Frontend build completed: $FRONTEND_DIST"
}

# Start functions
start_celery_review() {
    log "Starting Celery Worker (review queue)..."
    cd "$BACKEND_DIR"
    nohup celery -A celery_app worker --loglevel=info --concurrency=4 -Q review > "$SCRIPT_DIR/logs/celery_review.log" 2>&1 &
    save_pid "celery_review" "$!"
    log "Celery Review started (PID: $(get_pid celery_review))"
}

start_celery_parser() {
    log "Starting Celery Worker (parser queue)..."
    cd "$BACKEND_DIR"
    nohup celery -A celery_app worker --loglevel=info --concurrency=4 -Q parser --max-memory-per-child=2000000 > "$SCRIPT_DIR/logs/celery_parser.log" 2>&1 &
    save_pid "celery_parser" "$!"
    log "Celery Parser started (PID: $(get_pid celery_parser))"
}

start_celery_beat() {
    log "Starting Celery Beat (task scheduler)..."
    cd "$BACKEND_DIR"
    nohup celery -A celery_app beat --loglevel=info > "$SCRIPT_DIR/logs/celery_beat.log" 2>&1 &
    save_pid "celery_beat" "$!"
    log "Celery Beat started (PID: $(get_pid celery_beat))"
}

start_backend() {
    log "Starting Backend API Server (gunicorn)..."

    # Check if port is already in use
    if is_port_in_use $BACKEND_PORT; then
        warn "Port $BACKEND_PORT is already in use, skipping backend start"
        return
    fi

    cd "$BACKEND_DIR"

    # Clean up stale pid file
    rm -f "$SCRIPT_DIR/pids/backend.pid"

    # Use subshell with conda environment and correct PYTHONPATH
    (
        source ~/miniconda3/etc/profile.d/conda.sh
        conda activate py311
        export PYTHONPATH="$SCRIPT_DIR/..:$BACKEND_DIR"
        exec gunicorn backend.main:app \
            --bind 0.0.0.0:$BACKEND_PORT \
            --workers 4 \
            --worker-class uvicorn.workers.UvicornWorker \
            --timeout 120 \
            --access-logfile "$SCRIPT_DIR/logs/backend_access.log" \
            --error-logfile "$SCRIPT_DIR/logs/backend_error.log" \
            --pid "$SCRIPT_DIR/pids/backend.pid"
    ) > "$SCRIPT_DIR/logs/backend.log" 2>&1 &

    save_pid "backend" "$!"
    log "Backend API started (PID: $(get_pid backend))"
}

start_rag_memory() {
    log "Starting RAG Memory Service..."

    # Check if port is already in use
    if is_port_in_use $RAG_MEMORY_PORT; then
        warn "Port $RAG_MEMORY_PORT is already in use, skipping rag_memory start"
        return
    fi

    cd "$RAG_MEMORY_DIR"

    # RAG memory service in background
    nohup npm run dev > "$SCRIPT_DIR/logs/rag_memory.log" 2>&1 &
    save_pid "rag_memory" "$!"
    log "RAG Memory started (PID: $(get_pid rag_memory))"
}

# Stop functions
stop_service() {
    local name=$1
    local pid=$(get_pid "$name")
    local port=""

    case "$name" in
        backend) port=$BACKEND_PORT ;;
        frontend) port=$FRONTEND_PORT ;;
        rag_memory) port=$RAG_MEMORY_PORT ;;
    esac

    # Try PID-based stop first
    if [ -n "$pid" ] && is_running "$pid"; then
        log "Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true

        for i in {1..10}; do
            if ! is_running "$pid"; then
                break
            fi
            sleep 1
        done

        if is_running "$pid"; then
            warn "$name did not stop gracefully, force killing..."
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi

    # Kill by port for additional cleanup
    if [ -n "$port" ]; then
        local port_pids=$(lsof -ti :$port 2>/dev/null)
        if [ -n "$port_pids" ]; then
            for p in $port_pids; do
                if [ -f "/proc/$p/cmdline" ] && grep -q "vscode" "/proc/$p/cmdline" 2>/dev/null; then
                    continue
                fi
                log "Killing orphaned process on port $port (PID: $p)..."
                kill -9 "$p" 2>/dev/null || true
            done
        fi
    fi

    remove_pid "$name"
    log "$name stopped"
}

stop_celery() {
    local name=$1
    local queue_name=$2

    # Get only PIDs (first column), not full command lines
    local celery_pids=$(pgrep -f "celery.*-A.*celery_app.*-Q ${queue_name}" 2>/dev/null)
    if [ -n "$celery_pids" ]; then
        for p in $celery_pids; do
            log "Stopping celery ${queue_name} worker (PID: $p)..."
            kill -9 "$p" 2>/dev/null || true
        done
    fi
    remove_pid "$name"
}

stop_celery_beat() {
    local name=$1

    # celery beat uses different command pattern (no -Q flag)
    local celery_pids=$(pgrep -f "celery.*-A.*celery_app.*beat" 2>/dev/null)
    if [ -n "$celery_pids" ]; then
        for p in $celery_pids; do
            log "Stopping celery beat (PID: $p)..."
            kill -9 "$p" 2>/dev/null || true
        done
    fi
    remove_pid "$name"
}

# Actions
do_build() {
    log "========================================"
    log "  Building for Production"
    log "========================================"
    echo ""

    build_frontend_only

    echo ""
    log "========================================"
    log "  Build completed!"
    log "========================================"
}

do_start() {
    log "========================================"
    log "  Starting Bid Review Agent System (PRODUCTION)"
    log "========================================"
    echo ""

    # Start celery workers first
    start_celery_review
    sleep 1
    start_celery_parser
    sleep 1
    start_celery_beat
    sleep 1

    # Start backend on port 8001
    start_backend
    sleep 2

    # Start RAG memory service
    start_rag_memory
    sleep 1

    # Build frontend
    build_frontend_only

    echo ""
    log "========================================"
    log "  All services started!"
    log "========================================"
    echo ""
    echo "Backend API:   http://localhost:$BACKEND_PORT"
    echo "Frontend:     http://localhost:$FRONTEND_PORT"
    echo "RAG Memory:   http://localhost:$RAG_MEMORY_PORT"
    echo ""
    echo "Logs: $SCRIPT_DIR/logs/"
    echo "PIDs: $SCRIPT_DIR/pids/"
    echo ""
}

do_stop() {
    log "========================================"
    log "  Stopping Bid Review Agent System (PRODUCTION)"
    log "========================================"
    echo ""

    stop_service "frontend"
    stop_service "backend"
    stop_service "rag_memory"
    stop_celery_beat "celery_beat"
    stop_celery "celery_parser" "parser"
    stop_celery "celery_review" "review"

    # Cleanup Redis task data
    cleanup_redis_celery

    echo ""
    log "========================================"
    log "  All services stopped!"
    log "========================================"
    echo ""
}

cleanup_redis_celery() {
    log "Cleaning up Redis Celery task data..."

    python3 << 'EOF'
import redis

r = redis.Redis(host='183.66.37.186', port=7005, decode_responses=True)

patterns = [
    'celery*',
    'celery-task-meta*',
    'celerybeat*',
    'stream:task:*',
    'sse:stream:*',
]

total_deleted = 0
for pattern in patterns:
    keys = r.keys(pattern)
    if keys:
        for key in keys:
            print(f"Deleting Redis key: {key}", flush=True)
            r.delete(key)
            total_deleted += 1

print(f"Redis cleanup completed. Total keys deleted: {total_deleted}", flush=True)
EOF

    log "Redis Celery task data cleanup completed"
}

do_status() {
    log "========================================"
    log "  Bid Review Agent System Status (PRODUCTION)"
    log "========================================"
    echo ""

    # Backend
    if is_port_in_use $BACKEND_PORT; then
        local backend_pid=$(lsof -ti :$BACKEND_PORT 2>/dev/null | head -1)
        echo -e "[backend] Running (PID: $backend_pid) ${GREEN}✓${NC}"
    else
        echo -e "[backend] Not running ${RED}✗${NC}"
    fi

    # Frontend
    if is_port_in_use $FRONTEND_PORT; then
        local frontend_pid=$(lsof -ti :$FRONTEND_PORT 2>/dev/null | head -1)
        echo -e "[frontend] Running (PID: $frontend_pid) ${GREEN}✓${NC}"
    else
        echo -e "[frontend] Not running ${RED}✗${NC}"
    fi

    # RAG Memory
    if is_port_in_use $RAG_MEMORY_PORT; then
        local rag_pid=$(lsof -ti :$RAG_MEMORY_PORT 2>/dev/null | head -1)
        echo -e "[rag_memory] Running (PID: $rag_pid) ${GREEN}✓${NC}"
    else
        echo -e "[rag_memory] Not running ${RED}✗${NC}"
    fi

    # Celery workers
    local celery_review_pid=$(pgrep -f "celery.*-Q review" 2>/dev/null | head -1)
    local celery_parser_pid=$(pgrep -f "celery.*-Q parser" 2>/dev/null | head -1)
    local celery_beat_pid=$(pgrep -f "celery.*beat" 2>/dev/null | head -1)

    if [ -n "$celery_review_pid" ]; then
        echo -e "[celery_review] Running (PID: $celery_review_pid) ${GREEN}✓${NC}"
    else
        echo -e "[celery_review] Not running ${RED}✗${NC}"
    fi

    if [ -n "$celery_parser_pid" ]; then
        echo -e "[celery_parser] Running (PID: $celery_parser_pid) ${GREEN}✓${NC}"
    else
        echo -e "[celery_parser] Not running ${RED}✗${NC}"
    fi

    if [ -n "$celery_beat_pid" ]; then
        echo -e "[celery_beat] Running (PID: $celery_beat_pid) ${GREEN}✓${NC}"
    else
        echo -e "[celery_beat] Not running ${RED}✗${NC}"
    fi

    echo ""
    echo "Ports:"
    echo -e "  :$BACKEND_PORT (Backend)  - $(is_port_in_use $BACKEND_PORT && echo -e "${GREEN}in use${NC}" || echo -e "${RED}free${NC}")"
    echo -e "  :$FRONTEND_PORT (Frontend) - $(is_port_in_use $FRONTEND_PORT && echo -e "${GREEN}in use${NC}" || echo -e "${RED}free${NC}")"
    echo -e "  :$RAG_MEMORY_PORT (RAG)   - $(is_port_in_use $RAG_MEMORY_PORT && echo -e "${GREEN}in use${NC}" || echo -e "${RED}free${NC}")"

    echo ""
    echo "Health Checks:"
    if curl -s http://localhost:$BACKEND_PORT/health > /dev/null 2>&1; then
        echo -e "  Backend API: ${GREEN}healthy${NC}"
    else
        echo -e "  Backend API: ${RED}unhealthy${NC}"
    fi

    if curl -s http://localhost:$RAG_MEMORY_PORT/api/status > /dev/null 2>&1; then
        echo -e "  RAG Memory:  ${GREEN}healthy${NC}"
    else
        echo -e "  RAG Memory:  ${RED}unhealthy${NC}"
    fi

    echo ""
    echo "Log files:"
    [ -f "$SCRIPT_DIR/logs/backend.log" ] && echo "  Backend:    $(wc -l < $SCRIPT_DIR/logs/backend.log) lines"
    [ -f "$SCRIPT_DIR/logs/frontend.log" ] && echo "  Frontend:   $(wc -l < $SCRIPT_DIR/logs/frontend.log) lines"
    [ -f "$SCRIPT_DIR/logs/celery_review.log" ] && echo "  Celery Rev: $(wc -l < $SCRIPT_DIR/logs/celery_review.log) lines"
    [ -f "$SCRIPT_DIR/logs/celery_parser.log" ] && echo "  Celery Par: $(wc -l < $SCRIPT_DIR/logs/celery_parser.log) lines"

    echo ""
}

do_restart() {
    log "Restarting services..."

    do_stop
    sleep 3
    do_start
}

do_logs() {
    local service=$2

    if [ -z "$service" ] || [ "$service" == "all" ]; then
        echo "=== Backend Log ===" && tail -50 "$SCRIPT_DIR/logs/backend.log" 2>/dev/null || echo "No log file"
        echo ""
        echo "=== Frontend Log ===" && tail -50 "$SCRIPT_DIR/logs/frontend.log" 2>/dev/null || echo "No log file"
        echo ""
        echo "=== Celery Review Log ===" && tail -50 "$SCRIPT_DIR/logs/celery_review.log" 2>/dev/null || echo "No log file"
        echo ""
        echo "=== Celery Parser Log ===" && tail -50 "$SCRIPT_DIR/logs/celery_parser.log" 2>/dev/null || echo "No log file"
    else
        case "$service" in
            backend) tail -f "$SCRIPT_DIR/logs/backend.log" ;;
            frontend) tail -f "$SCRIPT_DIR/logs/frontend.log" ;;
            celery_review) tail -f "$SCRIPT_DIR/logs/celery_review.log" ;;
            celery_parser) tail -f "$SCRIPT_DIR/logs/celery_parser.log" ;;
            celery_beat) tail -f "$SCRIPT_DIR/logs/celery_beat.log" ;;
            rag_memory) tail -f "$SCRIPT_DIR/logs/rag_memory.log" ;;
            *) echo "Unknown service: $service" ;;
        esac
    fi
}

# Main
case "$1" in
    build)  do_build ;;
    start)  do_start ;;
    stop)   do_stop ;;
    status) do_status ;;
    restart) do_restart ;;
    logs)   do_logs "$@" ;;
    *)
        echo "Usage: $0 {build|start|stop|status|restart|logs}"
        echo ""
        echo "Commands:"
        echo "  build   - Build frontend for production"
        echo "  start   - Start all services"
        echo "  stop    - Stop all services"
        echo "  status  - Show service status"
        echo "  restart - Restart all services"
        echo "  logs    - Show logs (optional: logs [backend|frontend|celery_review|celery_parser])"
        exit 1
        ;;
esac
