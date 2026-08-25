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
    include=["backend.tasks.review_tasks", "backend.tasks.duplicate_tasks", "backend.tasks.document_parser", "backend.tasks.feedback_tasks", "backend.tasks.experience_tasks", "backend.tasks.billing_tasks", "backend.tasks.blind_check_tasks", "backend.tasks.bid_draft_tasks", "backend.tasks.polish_tasks"],
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
        "backend.tasks.review_tasks.generate_overall_report": {"queue": "review"},
        "backend.tasks.document_parser.parse_document": {"queue": "parser"},
        "backend.tasks.feedback_tasks.process_feedback": {"queue": "review"},
        "backend.tasks.feedback_tasks.process_batch_feedback": {"queue": "review"},
        "backend.tasks.feedback_tasks.rewrite_skill_from_feedback": {"queue": "review"},
        "backend.tasks.experience_tasks.extract_experience": {"queue": "review"},
        "backend.tasks.experience_tasks.process_skill_extraction": {"queue": "review"},
        "backend.tasks.blind_check_tasks.run_blind_check": {"queue": "review"},
        "backend.tasks.bid_draft_tasks.run_bid_draft": {"queue": "generation"},
        "backend.tasks.polish_tasks.run_polish": {"queue": "review"},
        "backend.tasks.billing_tasks.poll_pending_recharge_orders": {"queue": "review"},
        "backend.tasks.billing_tasks.expire_credit_lots": {"queue": "review"},
        "backend.tasks.billing_tasks.settle_task_billing": {"queue": "review"},
        "backend.tasks.billing_tasks.dispatch_pending_task_outbox": {"queue": "review"},
        "backend.tasks.billing_tasks.reconcile_task_billing": {"queue": "review"},
        "backend.tasks.billing_tasks.expire_pending_recharge_orders": {"queue": "review"},
    },
    # 定时任务调度。beat 进程在 prod 单例跑（bjt-proc.sh start_celery_beat），
    # 派发的任务路由到 review 队列由 3 节点 celery worker 消费。
    beat_schedule={
        "poll-pending-recharge-orders": {
            "task": "backend.tasks.billing_tasks.poll_pending_recharge_orders",
            "schedule": 60.0,  # 每 60 秒扫一次 pending 真实交行订单
        },
        "expire-credit-lots": {
            "task": "backend.tasks.billing_tasks.expire_credit_lots",
            "schedule": 3600.0,
        },
        "dispatch-pending-task-outbox": {
            "task": "backend.tasks.billing_tasks.dispatch_pending_task_outbox",
            "schedule": 10.0,
        },
        "reconcile-task-billing": {
            "task": "backend.tasks.billing_tasks.reconcile_task_billing",
            "schedule": 120.0,
        },
        # 兜底清理 poll 覆盖不到的过期 pending 订单（未取码 / 超 24h），
        # 首轮会清历史积压（2026-08-16 巡检 42 笔），之后稳态量极小。
        "expire-pending-recharge-orders": {
            "task": "backend.tasks.billing_tasks.expire_pending_recharge_orders",
            "schedule": 300.0,
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
        "backend.tasks.blind_check_tasks.run_blind_check": {
            "time_limit": 1800,
            "soft_time_limit": 1740,
        },
        # 标书生成：长任务（分析→大纲→逐节），worker 侧 asyncio 上限 6600s，
        # soft/hard 留梯度。跑在独立 generation 队列，不与 review 争抢。
        "backend.tasks.bid_draft_tasks.run_bid_draft": {
            "time_limit": 7200,
            "soft_time_limit": 6900,
        },
        # 润色：单次 LLM 调用（worker 侧 asyncio 上限 240s）。
        "backend.tasks.polish_tasks.run_polish": {
            "time_limit": 300,
            "soft_time_limit": 270,
        },
        # 充值轮询：扫一批 pending 订单 + 每条调一次交行查单（最多 ~10 条 × 5s 超时），
        # 给 90s 软超时 / 120s 硬超时兜底。
        "backend.tasks.billing_tasks.poll_pending_recharge_orders": {
            "time_limit": 120,
            "soft_time_limit": 90,
        },
        "backend.tasks.billing_tasks.expire_credit_lots": {
            "time_limit": 600,
            "soft_time_limit": 540,
        },
        "backend.tasks.billing_tasks.settle_task_billing": {
            "time_limit": 180,
            "soft_time_limit": 150,
        },
        "backend.tasks.billing_tasks.dispatch_pending_task_outbox": {
            "time_limit": 120,
            "soft_time_limit": 90,
        },
        "backend.tasks.billing_tasks.reconcile_task_billing": {
            "time_limit": 300,
            "soft_time_limit": 240,
        },
        # 首轮清积压时逐单查交行（100 条 × 5s 超时上限），放宽硬超时兜底。
        "backend.tasks.billing_tasks.expire_pending_recharge_orders": {
            "time_limit": 600,
            "soft_time_limit": 540,
        },
    },
)

# Start celery worker with:
# celery -A celery_app worker --loglevel=info
