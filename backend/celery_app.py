"""Celery application configuration."""

from celery import Celery

from backend.config import get_settings

settings = get_settings()

celery_app = Celery(
    "bid_review_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["backend.tasks.review_tasks", "backend.tasks.document_parser"],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    # Safety nets to prevent worker hangs
    task_time_limit=1800,            # 30min hard limit (SIGKILL)
    task_soft_time_limit=1500,       # 25min soft limit (raises SoftTimeLimitExceeded)
    worker_max_tasks_per_child=10,   # Recycle worker process every 10 tasks
    task_routes={
        "backend.tasks.review_tasks.run_review": {"queue": "review"},
        "backend.tasks.review_tasks.merge_review_results": {"queue": "review"},
        "backend.tasks.document_parser.parse_document": {"queue": "parser"},
    },
    task_annotations={
        "backend.tasks.review_tasks.run_review": {
            "time_limit": 1800,
            "soft_time_limit": 1500,
        },
        "backend.tasks.review_tasks.merge_review_results": {
            "time_limit": 600,
            "soft_time_limit": 480,
        },
        "backend.tasks.document_parser.parse_document": {
            "time_limit": 900,
            "soft_time_limit": 750,
        },
    },
)

# Start celery worker with:
# celery -A celery_app worker --loglevel=info
