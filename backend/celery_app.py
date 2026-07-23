"""Celery application configuration."""

# Windows 控制台默认 GBK 代码页，Mini-Agent 的 emoji print（📝🔄🤖 等）会触发
# UnicodeEncodeError，导致 worker 里 agent.run() 第一句 print 就崩，整个审查失败。
# 在任何 print 之前把 stdout/stderr 重配为 UTF-8（即便启动脚本已设 PYTHONIOENCODING 也兜底）。
import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from celery import Celery

from backend.config import get_settings
import backend.logging_config  # noqa: F401  # 注册 setup_logging 信号，接管 worker 日志（滚动文件）

settings = get_settings()

celery_app = Celery(
    "bid_review_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["backend.tasks.review_tasks", "backend.tasks.duplicate_tasks", "backend.tasks.document_parser", "backend.tasks.feedback_tasks", "backend.tasks.experience_tasks", "backend.tasks.billing_tasks"],
)

# Ensure celery.current_app points to our app, so @shared_task binds correctly
# regardless of import order in the API process.
celery_app.set_default()

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    # 不把 stdout/stderr 包成 LoggingProxy —— 避免 Mini-Agent 的 print() 噪声
    # （完整 LLM thinking/响应）被写入日志。这些内容已在 sub_agent_*.log、
    # ~/.mini-agent/log、interaction JSON 中保留。worker 自身的 print 走进程
    # stdout，由启动脚本重定向到 /dev/null 丢弃。
    worker_redirect_stdouts=False,
    # Safety nets to prevent worker hangs
    task_time_limit=28800,           # 8h hard limit — large tender docs need long parse time
    worker_max_tasks_per_child=10,   # Recycle worker process every 10 tasks
    # ---- 第三方 Redis 不稳定，频繁断连(10054)。下面的参数让 broker：
    #   * broker_connection_retry_on_startup=True : 启动时连不上也持续重试
    #   * broker_connection_max_retries=None      : 无限重试，不放弃
    #   * broker_transport_options.socket_keepalive=True / health_check_interval:
    #       维持长连接心跳、定期探测，避免对端 RST 后 worker 卡死
    #   * visibility_timeout : 任务确认超时回到队列的时间（秒），断连丢失任务后
    #       也能在超时后被重新投递。设大一点避免长任务被误判重投。
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,
    broker_transport_options={
        "socket_keepalive": True,
        "health_check_interval": 30,
        "visibility_timeout": 3600,
        "retry_on_timeout": True,
    },
    task_routes={
        "backend.tasks.review_tasks.run_review": {"queue": "review"},
        "backend.tasks.duplicate_tasks.run_duplicate_check": {"queue": "review"},
        "backend.tasks.review_tasks.merge_review_results": {"queue": "review"},
        "backend.tasks.document_parser.parse_document": {"queue": "parser"},
        "backend.tasks.feedback_tasks.process_feedback": {"queue": "review"},
        "backend.tasks.feedback_tasks.process_batch_feedback": {"queue": "review"},
        "backend.tasks.feedback_tasks.rewrite_skill_from_feedback": {"queue": "review"},
        "backend.tasks.experience_tasks.extract_experience": {"queue": "review"},
        "backend.tasks.experience_tasks.process_skill_extraction": {"queue": "review"},
        "backend.tasks.billing_tasks.poll_pending_recharge_orders": {"queue": "review"},
    },
    # 定时任务调度。beat 进程在 prod 单例跑（bjt-proc.sh start_celery_beat），
    # 派发的任务路由到 review 队列由 3 节点 celery worker 消费。
    beat_schedule={
        "poll-pending-recharge-orders": {
            "task": "backend.tasks.billing_tasks.poll_pending_recharge_orders",
            "schedule": 60.0,  # 每 60 秒扫一次 pending 真实交行订单
        },
    },
    task_annotations={
        "backend.tasks.review_tasks.run_review": {
            # Coordinate with agent_total_timeout (5400s, asyncio.wait_for in
            # _run_agent_review): asyncio terminates first; soft_time_limit gives
            # Celery a worker-level graceful window; time_limit is the hard backstop.
            "time_limit": 6000,
            "soft_time_limit": 5700,
        },
        "backend.tasks.duplicate_tasks.run_duplicate_check": {
            "time_limit": 6000,
            "soft_time_limit": 5700,
        },
        "backend.tasks.review_tasks.merge_review_results": {
            "time_limit": 600,
            "soft_time_limit": 480,
        },
        "backend.tasks.document_parser.parse_document": {
            "time_limit": None,
            "soft_time_limit": None,
        },
        "backend.tasks.experience_tasks.extract_experience": {
            "time_limit": 600,
            "soft_time_limit": 480,
        },
        # 充值轮询：扫一批 pending 订单 + 每条调一次交行查单（最多 ~10 条 × 5s 超时），
        # 给 90s 软超时 / 120s 硬超时兜底。
        "backend.tasks.billing_tasks.poll_pending_recharge_orders": {
            "time_limit": 120,
            "soft_time_limit": 90,
        },
    },
)

# Start celery worker with:
# celery -A celery_app worker --loglevel=info
