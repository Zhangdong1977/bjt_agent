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
RAG_MEMORY_DIR="$PWD/rag_memory_service"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Conda environment check
check_conda_env() {
    local target_env="py311"
    local current_env="${CONDA_DEFAULT_ENV:-}"

    if [ "$current_env" != "$target_env" ]; then
        warn "Not in conda environment '$target_env', attempting to activate..."

        # Initialize conda and activate the environment
        if command -v conda &> /dev/null; then
            eval "$(conda shell.bash hook 2>/dev/null)" || true
            conda activate "$target_env" 2>/dev/null || {
                error "Failed to activate conda environment '$target_env'"
                error "Please run: conda activate $target_env"
                error "Then re-run this script: ./scripts/bjt.sh $1"
                exit 1
            }
            log "Successfully activated conda environment '$target_env'"
        else
            error "Conda not found. Please install conda or activate '$target_env' environment manually."
            exit 1
        fi
    else
        log "Conda environment check passed: $target_env"
    fi
}

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
    # 生产模式: 使用 uvicorn --workers
    python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4 > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
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

start_rag_memory() {
    log "Starting RAG Memory Service..."
    cd "$RAG_MEMORY_DIR"
    npm run dev > "$SCRIPT_DIR/logs/rag_memory.log" 2>&1 &
    save_pid "rag_memory" "$!"
    log "RAG Memory started (PID: $(get_pid rag_memory))"
}

# Stop functions
stop_service() {
    local name=$1
    local pid=$(get_pid "$name")
    local port=""
    local celery_pattern=""

    # Map service name to port or celery pattern
    case "$name" in
        backend) port=8000 ;;
        frontend) port=3000 ;;
        rag_memory) port=3001 ;;
        celery_review) celery_pattern="celery.*review" ;;
        celery_parser) celery_pattern="celery.*parser" ;;
    esac

    # Try PID-based stop first
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
    fi

    # Kill by port for backend/frontend
    if [ -n "$port" ]; then
        local port_pids=$(lsof -ti :$port 2>/dev/null)
        if [ -n "$port_pids" ]; then
            for p in $port_pids; do
                # 跳过 VSCode Server 相关进程，避免中断远程连接
                if [ -f "/proc/$p/cmdline" ] && grep -q "vscode" "/proc/$p/cmdline" 2>/dev/null; then
                    continue
                fi
                log "Killing orphaned process on port $port (PID: $p)..."
                kill -9 "$p" 2>/dev/null || true
            done
        fi
    fi

    # Kill by celery pattern (精确匹配，只杀Q队列worker)
    if [ -n "$celery_pattern" ]; then
        # 使用精确模式: 匹配 "-A celery_app worker ... -Q <queuename>"
        local queue_name=""
        case "$celery_pattern" in
            "celery.*review") queue_name="review" ;;
            "celery.*parser") queue_name="parser" ;;
        esac
        if [ -n "$queue_name" ]; then
            # 只杀监听特定队列的celery worker进程
            local celery_pids=$(pgrep -af "celery.*-A.*celery_app.*-Q ${queue_name}" 2>/dev/null)
            if [ -n "$celery_pids" ]; then
                for p in $celery_pids; do
                    log "Killing celery ${queue_name} worker (PID: $p)..."
                    kill -9 "$p" 2>/dev/null || true
                done
            fi
        fi
    fi

    remove_pid "$name"
    log "$name stopped"
}

# Actions
do_start() {
    check_conda_env "start"

    log "========================================"
    log "  Starting Bid Review Agent System"
    log "========================================"
    echo ""

    start_celery_review
    sleep 2
    start_celery_parser
    sleep 2
    start_rag_memory
    sleep 2
    start_backend
    sleep 2
    start_frontend

    echo ""
    log "========================================"
    log "  All services started!"
    log "========================================"
    echo ""
    echo "RAG Memory:   http://localhost:3001"
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
    stop_service "rag_memory"
    stop_service "celery_parser"
    stop_service "celery_review"

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

    # Use Python to clean Redis (redis-cli not available)
    python3 << 'EOF'
import redis
import sys

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
    log "  Bid Review Agent System Status"
    log "========================================"
    echo ""

    # Detect backend process
    backend_pid=$(lsof -ti :8000 2>/dev/null | head -1)
    if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
        echo -e "[backend] Running (PID: $backend_pid) ${GREEN}✓${NC}"
    else
        echo -e "[backend] Not running ${RED}✗${NC}"
    fi

    # Detect frontend process
    frontend_pid=$(lsof -ti :3000 2>/dev/null | head -1)
    if [ -n "$frontend_pid" ] && kill -0 "$frontend_pid" 2>/dev/null; then
        echo -e "[frontend] Running (PID: $frontend_pid) ${GREEN}✓${NC}"
    else
        echo -e "[frontend] Not running ${RED}✗${NC}"
    fi

    # Detect rag_memory process
    rag_memory_pid=$(lsof -ti :3001 2>/dev/null | head -1)
    if [ -n "$rag_memory_pid" ] && kill -0 "$rag_memory_pid" 2>/dev/null; then
        echo -e "[rag_memory] Running (PID: $rag_memory_pid) ${GREEN}✓${NC}"
    else
        echo -e "[rag_memory] Not running ${RED}✗${NC}"
    fi

    # Detect celery workers (by process name)
    celery_review_pid=$(pgrep -f "celery.*review" 2>/dev/null | head -1)
    celery_parser_pid=$(pgrep -f "celery.*parser" 2>/dev/null | head -1)

    if [ -n "$celery_review_pid" ] && kill -0 "$celery_review_pid" 2>/dev/null; then
        echo -e "[celery_review] Running (PID: $celery_review_pid) ${GREEN}✓${NC}"
    else
        echo -e "[celery_review] Not running ${RED}✗${NC}"
    fi

    if [ -n "$celery_parser_pid" ] && kill -0 "$celery_parser_pid" 2>/dev/null; then
        echo -e "[celery_parser] Running (PID: $celery_parser_pid) ${GREEN}✓${NC}"
    else
        echo -e "[celery_parser] Not running ${RED}✗${NC}"
    fi

    echo ""
    echo "Backend API Health:"
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "Backend API is healthy ${GREEN}✓${NC}"
    else
        echo -e "Backend API is not responding ${RED}✗${NC}"
    fi

    echo ""
    echo "RAG Memory Health:"
    if curl -s http://localhost:3001/api/status > /dev/null 2>&1; then
        echo -e "RAG Memory is healthy ${GREEN}✓${NC}"
    else
        echo -e "RAG Memory is not responding ${RED}✗${NC}"
    fi

    echo ""
    echo "Ports:"
    if lsof -i :8000 > /dev/null 2>&1; then
        echo -e ":8000 (Backend API) - in use ${GREEN}✓${NC}"
    else
        echo -e ":8000 (Backend API) - not in use ${RED}✗${NC}"
    fi

    if lsof -i :3000 > /dev/null 2>&1; then
        echo -e ":3000 (Frontend) - in use ${GREEN}✓${NC}"
    else
        echo -e ":3000 (Frontend) - not in use ${RED}✗${NC}"
    fi

    if lsof -i :3001 > /dev/null 2>&1; then
        echo -e ":3001 (RAG Memory) - in use ${GREEN}✓${NC}"
    else
        echo -e ":3001 (RAG Memory) - not in use ${RED}✗${NC}"
    fi

    echo ""
}

do_restart() {
    check_conda_env "restart"

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
