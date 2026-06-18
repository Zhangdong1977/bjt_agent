"""FastAPI application entry point."""

import asyncio
import logging
import logging.config
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette import status

from backend.config import get_settings
from backend.models import init_db, close_db
from backend.api import auth_router, projects_router, documents_router, review_router, review_sessions_router, knowledge_router, feedback_router, experience_router, admin_router
from backend.api.events import router as events_router
from backend.services.sse_service import sse_manager
from backend.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# 应用日志配置：写入滚动文件 scripts/logs/backend.log（50MB × 5 份）。
# ConcurrentRotatingFileHandler 保证多 worker 进程下轮转安全（与 Celery 一致）。
# uvicorn/gunicorn 自身的控制台输出由启动脚本重定向，不经过此 handler。
_BACKEND_LOG_DIR = Path(__file__).resolve().parent.parent / "scripts" / "logs"


def _setup_app_logging() -> None:
    """应用日志配置（dictConfig）。

    在模块导入时调用一次；并在 lifespan 启动时再调用一次。
    之所以在 lifespan 重申：uvicorn/gunicorn 在 worker 启动阶段会自行配置
    logging，可能覆盖模块级的 root handler。lifespan 启动是 ASGI 生命周期里
    最后执行的一步，此时重新应用 dictConfig 可确保滚动文件 handler 生效。
    """
    _BACKEND_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "backend_file": {
                "class": "concurrent_log_handler.ConcurrentRotatingFileHandler",
                "filename": str(_BACKEND_LOG_DIR / "backend.log"),
                "maxBytes": 50 * 1024 * 1024,
                "backupCount": 5,
                "formatter": "standard",
                "encoding": "utf-8",
            },
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {
            "handlers": ["backend_file", "console"],
            "level": "INFO",
        },
        "loggers": {
            # SSE 事件高频，降为 INFO（曾是 DEBUG，是 backend.log 膨胀主因）
            "backend.services.sse_service": {"level": "INFO"},
            "backend.api.events": {"level": "INFO"},
            # 高频 HTTP 请求日志
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
            "uvicorn": {"level": "INFO"},
            "uvicorn.access": {"level": "INFO"},
            "uvicorn.error": {"level": "INFO"},
        },
    })


# 模块级首次配置（直接运行 / 普通导入时生效）
_setup_app_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown events."""
    logger = logging.getLogger(__name__)
    # 重申日志配置：确保 uvicorn/gunicorn worker 启动后滚动文件 handler 仍在
    _setup_app_logging()
    logger.info("[backend.main] lifespan startup: logging reconfigured (rotating file handler active)")
    # Startup
    await init_db()

    # Clean up stale tasks from previous runs (tasks stuck in pending/running
    # for over 45 minutes are assumed dead from crashed workers)
    try:
        from sqlalchemy import select, and_
        from backend.models import async_session_factory, ReviewTask

        stale_threshold = datetime.utcnow() - timedelta(minutes=45)
        async with async_session_factory() as db:
            result = await db.execute(
                select(ReviewTask).where(
                    and_(
                        ReviewTask.status.in_(["pending", "running"]),
                        ReviewTask.started_at < stale_threshold,
                    )
                )
            )
            stale = result.scalars().all()
            for t in stale:
                logger.warning(f"[startup] Failing stale task {t.id}, started_at={t.started_at}")
                t.status = "failed"
                t.error_message = "Stale task cleaned up on startup"
                t.completed_at = datetime.utcnow()
            await db.commit()
            if stale:
                logger.warning(f"[startup] Cleaned up {len(stale)} stale tasks")
    except Exception as e:
        logger.warning(f"[startup] Stale task cleanup failed: {e}")

    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# Add rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(projects_router, prefix=settings.api_prefix)
app.include_router(documents_router, prefix=settings.api_prefix)
app.include_router(review_router, prefix=settings.api_prefix)
app.include_router(review_sessions_router, prefix=settings.api_prefix)
app.include_router(knowledge_router, prefix=settings.api_prefix)
app.include_router(feedback_router, prefix=settings.api_prefix)
app.include_router(experience_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
app.include_router(events_router)

# Mount workspace directory as static files for image access
workspace_path = settings.workspace_path
app.mount("/files", StaticFiles(directory=str(workspace_path)), name="workspace")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "app_name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/debug/redis")
async def debug_redis():
    """Debug endpoint to check Redis connection."""
    import redis
    from backend.config import get_settings
    settings = get_settings()

    result = {
        "redis_url": settings.redis_url,
        "redis_url_empty": not bool(settings.redis_url),
    }

    if settings.redis_url:
        try:
            r = redis.from_url(settings.redis_url, decode_responses=True)
            r.ping()
            keys = r.keys("sse:stream:*")
            result["redis_connection"] = "success"
            result["stream_count"] = len(keys)
            r.close()
        except Exception as e:
            result["redis_connection"] = f"failed: {e}"
    else:
        result["redis_connection"] = "not configured (redis_url is empty)"

    return result


@app.get("/api/events/tasks/{task_id}/stream")
async def stream_task_events(task_id: str, token: str | None = None):
    """Stream SSE events for a specific task.

    This endpoint provides real-time updates about task progress,
    including document parsing status and review agent steps.
    Events are published via Redis pubsub from Celery workers.
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"SSE connection requested for task: {task_id}")

    # Validate token if provided (optional for backwards compatibility)
    if token:
        from jose import JWTError, jwt
        from backend.config import get_settings
        settings = get_settings()
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            user_id: str = payload.get("sub")
            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

    async def event_generator():
        async for event in sse_manager.connect(task_id):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/tasks/cleanup-on-restart")
async def cleanup_tasks_on_restart():
    """Cleanup all pending/running tasks before service restart.

    This endpoint is called by the bjt.sh script before restarting services.
    It terminates all Celery tasks and marks them as failed.
    """
    from backend.celery_app import celery_app
    from backend.models import async_session_factory, ReviewTask
    from sqlalchemy import select

    cleaned_count = 0

    async with async_session_factory() as db:
        # Get all pending/running tasks
        result = await db.execute(
            select(ReviewTask).where(ReviewTask.status.in_(["pending", "running"]))
        )
        tasks = result.scalars().all()

        for task in tasks:
            if task.celery_task_id:
                try:
                    # Revoke and terminate the Celery task
                    celery_app.control.revoke(task.celery_task_id, terminate=True)
                except Exception:
                    pass  # Task may have already completed

            task.status = "failed"
            task.error_message = "Service restarting"
            task.completed_at = datetime.utcnow()
            cleaned_count += 1

        await db.commit()

    return {"cleaned_tasks": cleaned_count}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
