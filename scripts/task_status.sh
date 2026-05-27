#!/bin/bash
# 任务队列状态查看脚本
# 显示 Celery 队列中各队列的待处理任务数、活跃任务、已注册任务等信息

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 加载环境变量
if [ -f "$PROJECT_DIR/backend/.env" ]; then
    set -a
    source "$PROJECT_DIR/backend/.env"
    set +a
fi

REDIS_URL="${CELERY_BROKER_URL:-redis://183.66.37.186:7005/0}"
# 解析 Redis 连接信息
REDIS_HOST=$(echo "$REDIS_URL" | sed -E 's|redis://([^:/]+).*|\1|')
REDIS_PORT=$(echo "$REDIS_URL" | sed -E 's|redis://[^:/]+:([0-9]+).*|\1|')
REDIS_DB=$(echo "$REDIS_URL" | sed -E 's|redis://[^/]+/([0-9]+)|\1|')

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "\n${BOLD}========================================${NC}"
echo -e "${BOLD}    标书审查系统 - 任务队列状态${NC}"
echo -e "${BOLD}========================================${NC}"
echo -e "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo -e "Redis: ${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB}\n"

# 检查 redis-cli 是否可用
if ! command -v redis-cli &>/dev/null; then
    echo -e "${RED}错误: redis-cli 未安装${NC}"
    exit 1
fi

REDIS_CMD="redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB"

# 测试 Redis 连接
if ! $REDIS_CMD ping &>/dev/null; then
    echo -e "${RED}错误: 无法连接 Redis ${REDIS_HOST}:${REDIS_PORT}${NC}"
    exit 1
fi

# ---- 1. 队列待处理任务 ----
echo -e "${BOLD}${CYAN}【队列待处理任务】${NC}"

QUEUES=("review" "parser")
for queue in "${QUEUES[@]}"; do
    count=$($REDIS_CMD llen "$queue" 2>/dev/null || echo 0)
    if [ "$count" -gt 0 ]; then
        echo -e "  ${YELLOW}$queue${NC}: ${RED}${count}${NC} 个待处理"
    else
        echo -e "  ${GREEN}$queue${NC}: ${GREEN}0${NC} (空闲)"
    fi
done

# ---- 2. Celery Worker 状态 ----
echo -e "\n${BOLD}${CYAN}【Celery Worker 进程】${NC}"

WORKER_PIDS=$(pgrep -af "celery.*-A.*celery_app" 2>/dev/null || true)
if [ -z "$WORKER_PIDS" ]; then
    echo -e "  ${RED}无 Worker 进程运行${NC}"
else
    echo "$WORKER_PIDS" | while read -r pid_line; do
        pid=$(echo "$pid_line" | awk '{print $1}')
        queue=$(echo "$pid_line" | grep -oP '\-Q\s+\K\S+' || echo "unknown")
        concurrency=$(echo "$pid_line" | grep -oP '\-\-concurrency=\K\S+' || echo "default")

        # 计算该 worker 的子进程数
        child_count=$(pgrep -P "$pid" 2>/dev/null | wc -l)

        echo -e "  PID ${GREEN}$pid${NC} | 队列: ${BOLD}$queue${NC} | 并发: $concurrency | 子进程: $child_count"
    done
fi

# ---- 3. Beat 调度器 ----
BEAT_PID=$(pgrep -af "celery.*beat" 2>/dev/null | grep -v grep || true)
echo -e "\n${BOLD}${CYAN}【Beat 调度器】${NC}"
if [ -z "$BEAT_PID" ]; then
    echo -e "  ${YELLOW}未运行${NC}"
else
    pid=$(echo "$BEAT_PID" | awk '{print $1}')
    echo -e "  ${GREEN}运行中${NC} (PID: $pid)"
fi

# ---- 4. 最近任务结果 ----
echo -e "\n${BOLD}${CYAN}【最近任务结果 (最近20条)】${NC}"

# 获取 celery-task-meta-* 键
TASK_KEYS=$($REDIS_CMD keys "celery-task-meta-*" 2>/dev/null || true)
if [ -z "$TASK_KEYS" ]; then
    echo -e "  ${YELLOW}无任务记录${NC}"
else
    # 按时间排序，取最近20条
    echo "$TASK_KEYS" | while read -r key; do
        $REDIS_CMD get "$key" 2>/dev/null
    done | python3 -c "
import sys, json
results = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        task_id = data.get('task_id', 'unknown')[:8]
        status = data.get('status', 'UNKNOWN')
        name = data.get('name', data.get('result', {}).get('task_name', 'unknown'))
        # 取任务名最后一段
        if '.' in name:
            name = name.split('.')[-1]
        # 结果摘要
        result = data.get('result', '')
        if isinstance(result, dict):
            result = result.get('message', str(result)[:60])
        elif isinstance(result, str):
            result = result[:60]

        date_done = data.get('date_done', '')
        results.append((date_done, task_id, name, status, str(result)[:50]))
    except:
        continue

# 按时间倒序
results.sort(reverse=True)
results = results[:20]

for date_done, task_id, name, status, result in results:
    color = {'SUCCESS': '\033[0;32m', 'FAILURE': '\033[0;31m', 'REVOKED': '\033[1;33m',
             'STARTED': '\033[0;36m', 'PENDING': '\033[0;37m'}.get(status, '')
    nc = '\033[0m'
    print(f'  {date_done[:19]} | {task_id}... | {name:<25} | {color}{status:<10}{nc} | {result}')
" 2>/dev/null || echo -e "  ${YELLOW}解析失败${NC}"
fi

# ---- 5. SSE 活跃流 ----
echo -e "\n${BOLD}${CYAN}【SSE 活跃流】${NC}"

SSE_KEYS=$($REDIS_CMD keys "stream:task:*" 2>/dev/null || true)
if [ -z "$SSE_KEYS" ]; then
    echo -e "  ${GREEN}无活跃 SSE 流${NC}"
else
    count=$(echo "$SSE_KEYS" | wc -l)
    echo -e "  ${YELLOW}${count}${NC} 个活跃流:"
    echo "$SSE_KEYS" | head -10 | while read -r key; do
        stream_len=$($REDIS_CMD xlen "$key" 2>/dev/null || echo 0)
        echo -e "    $key (${stream_len} 条消息)"
    done
    if [ "$count" -gt 10 ]; then
        echo -e "    ... 还有 $((count - 10)) 个流"
    fi
fi

# ---- 6. Redis 内存使用 ----
echo -e "\n${BOLD}${CYAN}【Redis 资源使用】${NC}"
MEM_INFO=$($REDIS_CMD info memory 2>/dev/null || true)
USED_MEM=$(echo "$MEM_INFO" | grep "used_memory_human" | head -1 | awk -F: '{print $2}' | tr -d '\r')
PEAK_MEM=$(echo "$MEM_INFO" | grep "used_memory_peak_human" | head -1 | awk -F: '{print $2}' | tr -d '\r')
DB_SIZE=$($REDIS_CMD dbsize 2>/dev/null || echo "N/A")
echo -e "  内存使用: ${BOLD}$USED_MEM${NC} (峰值: $PEAK_MEM)"
echo -e "  键总数: $DB_SIZE"

echo -e "\n${BOLD}========================================${NC}"
echo -e "完成。\n"
