#!/bin/bash
# ============================================================
# BJT Agent - 集群节点部署脚本
#
# 使用方式：
#   ./scripts/bjt-cluster.sh start --node node1   # 按 node1.env 配置启动
#   ./scripts/bjt-cluster.sh start --node node2
#   ./scripts/bjt-cluster.sh start --node node3
#   ./scripts/bjt-cluster.sh stop  --node node1
#   ./scripts/bjt-cluster.sh status --node node1
#
# 配置文件：deploy/node-configs/node{1,2,3}.env
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
DEPLOY_DIR="$PROJECT_DIR/deploy"
CONFIG_DIR="$DEPLOY_DIR/node-configs"

mkdir -p "$SCRIPT_DIR/logs"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"; }
warn()  { echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"; }
error() { echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"; }
info()  { echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')] INFO:${NC} $1"; }

# ---- 解析参数 ----
NODE_NAME=""
ACTION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node)
            NODE_NAME="$2"
            shift 2
            ;;
        start|stop|status|restart)
            ACTION="$1"
            shift
            ;;
        *)
            error "Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [ -z "$ACTION" ]; then
    echo "Usage: $0 {start|stop|status|restart} --node <node_name>"
    echo ""
    echo "Commands:"
    echo "  start   - Start services for this node"
    echo "  stop    - Stop services for this node"
    echo "  status  - Show service status"
    echo "  restart - Restart services"
    echo ""
    echo "Options:"
    echo "  --node  - Node name (e.g., node1, node2, node3)"
    echo "            Loads config from deploy/node-configs/<name>.env"
    echo ""
    echo "Examples:"
    echo "  $0 start --node node1"
    echo "  $0 stop  --node node3"
    exit 1
fi

# ---- 加载节点配置 ----
if [ "$ACTION" != "status" ] && [ -z "$NODE_NAME" ]; then
    error "--node parameter is required for $ACTION"
    exit 1
fi

NODE_CONFIG=""
if [ -n "$NODE_NAME" ]; then
    NODE_CONFIG="$CONFIG_DIR/${NODE_NAME}.env"
    if [ ! -f "$NODE_CONFIG" ]; then
        error "Node config not found: $NODE_CONFIG"
        error "Available configs:"
        ls -1 "$CONFIG_DIR"/*.env 2>/dev/null | xargs -n1 basename || echo "  (none)"
        exit 1
    fi
fi

# ---- 加载环境 ----
load_config() {
    if [ -n "$NODE_CONFIG" ]; then
        log "Loading node config: $NODE_CONFIG"
        set -a
        source "$NODE_CONFIG"
        set +a
    fi

    # 设置项目级环境变量
    export PYTHONPATH="$PROJECT_DIR"
    export MAX_SUB_AGENT_CONCURRENCY="${MAX_SUB_AGENT_CONCURRENCY:-3}"

    cd "$PROJECT_DIR/backend"

    # Conda 环境
    local target_env="py311"
    local current_env="${CONDA_DEFAULT_ENV:-}"
    if [ "$current_env" != "$target_env" ]; then
        info "Activating conda environment '$target_env'..."
        if command -v conda &> /dev/null; then
            eval "$(conda shell.bash hook 2>/dev/null)" || true
            conda activate "$target_env" 2>/dev/null || {
                error "Failed to activate conda environment '$target_env'"
                exit 1
            }
        else
            error "Conda not found"
            exit 1
        fi
    fi
}

# ---- PID 管理 ----
save_pid() {
    local name="${NODE_NAME}_$1"
    echo "$2" > "$SCRIPT_DIR/logs/${name}.pid"
}

get_pid() {
    local name="${NODE_NAME}_$1"
    local pid_file="$SCRIPT_DIR/logs/${name}.pid"
    if [ -f "$pid_file" ]; then
        cat "$pid_file"
    fi
}

remove_pid() {
    local name="${NODE_NODE}_$1"
    rm -f "$SCRIPT_DIR/logs/${NODE_NAME}_$1.pid"
}

is_running() {
    [ -n "$1" ] && kill -0 "$1" 2>/dev/null
}

# ---- 启动函数 ----
start_backend() {
    local port="${BACKEND_PORT:-8001}"
    local workers="${BACKEND_WORKERS:-4}"

    log "Starting Backend API (port=$port, workers=$workers)..."
    cd "$PROJECT_DIR/backend"

    nohup gunicorn backend.main:app \
        --bind "0.0.0.0:$port" \
        --workers "$workers" \
        --worker-class uvicorn.workers.UvicornWorker \
        --timeout 120 \
        --access-logfile "$SCRIPT_DIR/logs/${NODE_NAME}_backend_access.log" \
        --error-logfile "$SCRIPT_DIR/logs/${NODE_NAME}_backend_error.log" \
        > "$SCRIPT_DIR/logs/${NODE_NAME}_backend.log" 2>&1 &

    save_pid "backend" "$!"
    log "Backend API started (PID: $(get_pid backend))"
}

start_review_workers() {
    local count="${REVIEW_WORKER_COUNT:-2}"
    local concurrency="${REVIEW_CONCURRENCY:-3}"
    local max_tasks="${REVIEW_MAX_TASKS_PER_CHILD:-5}"
    local max_mem="${REVIEW_MAX_MEMORY_PER_CHILD:-2500000}"

    cd "$PROJECT_DIR/backend"

    for i in $(seq 1 "$count"); do
        local worker_name="review_${i}"
        local hostname="${NODE_NAME}_review_${i}"

        log "Starting Celery Review Worker $i/$count (c=$concurrency, hostname=$hostname)..."
        nohup celery -A celery_app worker \
            --loglevel=info \
            --concurrency="$concurrency" \
            -Q review \
            --hostname="$hostname" \
            --max-tasks-per-child="$max_tasks" \
            --max-memory-per-child="$max_mem" \
            > "$SCRIPT_DIR/logs/${NODE_NAME}_${worker_name}.log" 2>&1 &

        save_pid "$worker_name" "$!"
        log "Review Worker $i started (PID: $(get_pid "$worker_name"))"
        sleep 1
    done
}

start_parser_workers() {
    local count="${PARSER_WORKER_COUNT:-1}"

    if [ "$count" -eq 0 ]; then
        info "No Parser Workers for this node (PARSER_WORKER_COUNT=0)"
        return
    fi

    local concurrency="${PARSER_CONCURRENCY:-2}"
    local max_mem="${PARSER_MAX_MEMORY_PER_CHILD:-2000000}"

    cd "$PROJECT_DIR/backend"

    for i in $(seq 1 "$count"); do
        local worker_name="parser_${i}"
        local hostname="${NODE_NAME}_parser_${i}"

        log "Starting Celery Parser Worker $i/$count (c=$concurrency, hostname=$hostname)..."
        nohup celery -A celery_app worker \
            --loglevel=info \
            --concurrency="$concurrency" \
            -Q parser \
            --hostname="$hostname" \
            --max-memory-per-child="$max_mem" \
            > "$SCRIPT_DIR/logs/${NODE_NAME}_${worker_name}.log" 2>&1 &

        save_pid "$worker_name" "$!"
        log "Parser Worker $i started (PID: $(get_pid "$worker_name"))"
        sleep 1
    done
}

start_rag_memory() {
    if [ "${START_RAG_MEMORY:-false}" != "true" ]; then
        info "RAG Memory Service not configured for this node"
        return
    fi

    log "Starting RAG Memory Service..."
    cd "$PROJECT_DIR/rag_memory_service"
    nohup npm run dev > "$SCRIPT_DIR/logs/${NODE_NAME}_rag_memory.log" 2>&1 &
    save_pid "rag_memory" "$!"
    log "RAG Memory started (PID: $(get_pid rag_memory))"
}

# ---- 停止函数 ----
stop_by_pid() {
    local name="$1"
    local pid
    pid=$(get_pid "$name")

    if [ -n "$pid" ] && is_running "$pid"; then
        log "Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        for i in {1..10}; do
            is_running "$pid" || break
            sleep 1
        done
        if is_running "$pid"; then
            warn "$name did not stop gracefully, force killing..."
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
    rm -f "$SCRIPT_DIR/logs/${NODE_NAME}_${name}.pid"
}

stop_by_pattern() {
    local pattern="$1"
    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null) || true
    if [ -n "$pids" ]; then
        for p in $pids; do
            log "Killing process matching '$pattern' (PID: $p)..."
            kill -9 "$p" 2>/dev/null || true
        done
    fi
}

# ---- Actions ----
do_start() {
    load_config

    log "========================================"
    log "  Starting BJT Node: $NODE_NAME"
    log "  Role: ${NODE_ROLE:-unknown}"
    log "========================================"
    echo ""

    # Celery workers 先启动
    start_review_workers
    start_parser_workers
    sleep 2

    # API 服务
    start_backend
    sleep 2

    # RAG Memory（可选）
    start_rag_memory

    echo ""
    log "========================================"
    log "  Node $NODE_NAME started!"
    log "========================================"
    echo ""
    echo "  Role:           ${NODE_ROLE}"
    echo "  Backend API:    http://localhost:${BACKEND_PORT:-8001}"
    echo "  Review Workers: ${REVIEW_WORKER_COUNT:-2} × c=${REVIEW_CONCURRENCY:-3}"
    echo "  Parser Workers: ${PARSER_WORKER_COUNT:-1} × c=${PARSER_CONCURRENCY:-2}"
    echo ""
    echo "  Logs: $SCRIPT_DIR/logs/${NODE_NAME}_*"
    echo ""
}

do_stop() {
    log "========================================"
    log "  Stopping BJT Node: ${NODE_NAME:-all}"
    log "========================================"
    echo ""

    if [ -n "$NODE_NAME" ]; then
        # 加载配置以知道有多少 worker
        if [ -f "$NODE_CONFIG" ]; then
            set -a; source "$NODE_CONFIG"; set +a
        fi

        # 停止 API
        stop_by_pid "backend"

        # 停止 Review Workers
        local review_count="${REVIEW_WORKER_COUNT:-2}"
        for i in $(seq 1 "$review_count"); do
            stop_by_pid "review_${i}"
        done
        # 也按 hostname 模式清理
        stop_by_pattern "celery.*--hostname=${NODE_NAME}_review"

        # 停止 Parser Workers
        local parser_count="${PARSER_WORKER_COUNT:-1}"
        for i in $(seq 1 "$parser_count"); do
            stop_by_pid "parser_${i}"
        done
        stop_by_pattern "celery.*--hostname=${NODE_NAME}_parser"

        # 停止 RAG Memory
        stop_by_pid "rag_memory"
    else
        warn "No --node specified, stopping all BJT processes on this host..."
        # 按 celery 模式清理所有 celery worker
        stop_by_pattern "celery.*-A.*celery_app"
        # 按端口清理 backend
        local port_pids
        port_pids=$(lsof -ti :8001 2>/dev/null) || true
        for p in $port_pids; do
            if [ -f "/proc/$p/cmdline" ] && grep -q "vscode" "/proc/$p/cmdline" 2>/dev/null; then
                continue
            fi
            kill -9 "$p" 2>/dev/null || true
        done
    fi

    echo ""
    log "========================================"
    log "  Node ${NODE_NAME:-all} stopped!"
    log "========================================"
    echo ""
}

do_status() {
    log "========================================"
    log "  BJT Node Status: ${NODE_NAME:-local}"
    log "========================================"
    echo ""

    # Backend API
    local backend_port="${BACKEND_PORT:-8001}"
    if lsof -i :"$backend_port" > /dev/null 2>&1; then
        local bpid
        bpid=$(lsof -ti :"$backend_port" 2>/dev/null | head -1)
        echo -e "  [backend]       Running (PID: $bpid) on :$backend_port ${GREEN}✓${NC}"
    else
        echo -e "  [backend]       Not running ${RED}✗${NC}"
    fi

    # Celery Workers
    echo ""
    echo "  Celery Workers:"
    local review_pids
    review_pids=$(pgrep -af "celery.*-Q review" 2>/dev/null) || true
    if [ -n "$review_pids" ]; then
        local rcount
        rcount=$(echo "$review_pids" | wc -l)
        echo -e "    Review Workers: ${GREEN}${rcount} running${NC}"
        echo "$review_pids" | while read -r line; do
            echo "      $line"
        done
    else
        echo -e "    Review Workers: ${RED}none${NC}"
    fi

    local parser_pids
    parser_pids=$(pgrep -af "celery.*-Q parser" 2>/dev/null) || true
    if [ -n "$parser_pids" ]; then
        local pcount
        pcount=$(echo "$parser_pids" | wc -l)
        echo -e "    Parser Workers: ${GREEN}${pcount} running${NC}"
        echo "$parser_pids" | while read -r line; do
            echo "      $line"
        done
    else
        echo -e "    Parser Workers: ${RED}none${NC}"
    fi

    # Health Check
    echo ""
    echo "  Health Checks:"
    if curl -s "http://localhost:${backend_port}/health" > /dev/null 2>&1; then
        echo -e "    Backend API:  ${GREEN}healthy${NC}"
    else
        echo -e "    Backend API:  ${RED}unhealthy${NC}"
    fi

    # Redis 队列状态
    echo ""
    echo "  Redis Queue:"
    python3 << 'PYEOF'
import redis, sys
try:
    r = redis.Redis(host='183.66.37.186', port=7005, decode_responses=True)
    review_len = r.llen('review')
    parser_len = r.llen('parser')
    print(f"    Review queue: {review_len} pending")
    print(f"    Parser queue: {parser_len} pending")
except Exception as e:
    print(f"    Error: {e}")
PYEOF

    echo ""
}

do_restart() {
    do_stop
    sleep 3
    do_start
}

# ---- Main ----
case "$ACTION" in
    start)   do_start ;;
    stop)    do_stop ;;
    status)  do_status ;;
    restart) do_restart ;;
    *)       echo "Unknown action: $ACTION"; exit 1 ;;
esac
