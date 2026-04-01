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
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=1,
    task_routes={
        "backend.tasks.review_tasks.run_review": {"queue": "review"},
        "backend.tasks.review_tasks.merge_review_results": {"queue": "review"},
        "backend.tasks.document_parser.parse_document": {"queue": "parser"},
    },
)

# Start celery worker with:
# celery -A celery_app worker --loglevel=info
