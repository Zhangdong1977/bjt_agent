"""Review tasks - handles the async bid review process."""

import asyncio
import json
import logging
from pathlib import Path

from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import ReviewTask, ReviewResult, AgentStep
from backend.utils.time_utils import utc_now, utc_seconds_between

logger = logging.getLogger(__name__)

import time as _time
# Progress watchdog: tracks last SSE event time per task_id
_task_last_event_times: dict[str, float] = {}

# Module-level Redis connection pool for _publish_event
_review_redis_pool = None

# Redis key prefix for task cancellation flags
_CANCEL_KEY_PREFIX = "task:cancel:"


def _get_review_redis():
    """Get Redis client from shared connection pool."""
    global _review_redis_pool
    import redis as redis_lib
    if _review_redis_pool is None:
        _settings = get_settings()
        _review_redis_pool = redis_lib.ConnectionPool.from_url(
            _settings.redis_url,
            max_connections=5,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            socket_keepalive=True,
        )
    return redis_lib.Redis(connection_pool=_review_redis_pool)


def set_task_cancelled(task_id: str) -> None:
    """Set the cancellation flag for a task in Redis.

    This flag is checked by the heartbeat monitor loop to detect
    when a user has requested cancellation via the API.

    Args:
        task_id: The ReviewTask ID
    """
    r = _get_review_redis()
    key = f"{_CANCEL_KEY_PREFIX}{task_id}"
    r.set(key, "1", ex=7200)  # Expire after 2 hours


def is_task_cancelled(task_id: str) -> bool:
    """Check if a task has been cancelled via the API.

    Args:
        task_id: The ReviewTask ID

    Returns:
        True if the task has been cancelled, False otherwise.
    """
    r = _get_review_redis()
    key = f"{_CANCEL_KEY_PREFIX}{task_id}"
    return r.exists(key) == 1


def clear_task_cancelled(task_id: str) -> None:
    """Clear the cancellation flag for a task.

    Args:
        task_id: The ReviewTask ID
    """
    r = _get_review_redis()
    key = f"{_CANCEL_KEY_PREFIX}{task_id}"
    r.delete(key)


def run_async(coro):
    """Helper to run async function in sync context.

    Uses asyncio.run() which properly sets up the event loop context for
    SQLAlchemy asyncpg driver. This avoids the 'attached to a different loop'
    error that occurs with manually created loops in Celery worker contexts.
    """
    return asyncio.run(coro)


def create_session_factory():
    """Create a new async session factory within the current event loop context.

    This must be called within an asyncio event loop to ensure the session
    factory is properly bound to that loop.

    Returns:
        Tuple of (session_factory, engine). Caller MUST call await engine.dispose()
        when done to prevent connection leaks.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from backend.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        connect_args={
            "timeout": 30,
            "command_timeout": 120,
        },
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    return session_factory, engine


# Error message constants
ERROR_TASK_NOT_FOUND = "任务不存在或已被删除"
ERROR_PROJECT_NOT_FOUND = "项目不存在或无权访问"
ERROR_TENDER_NOT_FOUND = "请先上传并解析招标文件"
ERROR_BID_NOT_FOUND = "请先上传并解析投标文件"


def _get_stream_key(task_id: str) -> str:
    """Get Redis Stream key for a task's SSE events.

    Uses sse:stream:{task_id} pattern for Redis Streams.
    """
    return f"sse:stream:{task_id}"


def _publish_event(task_id: str, event_type: str, data: dict, session_factory=None) -> None:
    """Publish an event to Redis Stream for SSE forwarding.

    Uses Redis Streams for reliable message delivery with persistence.
    XADD + Lua script ensures atomic stream operations.

    Args:
        task_id: The review task ID.
        event_type: Event type string.
        data: Event data dict.
        session_factory: Optional async session factory for DB persistence.
            When provided, step events are persisted via loop.create_task()
            instead of creating new engines/threads.
    """
    import traceback
    logger.debug(f"[_publish_event] ENTRY: task_id={task_id}, event_type={event_type}")
    _task_last_event_times[task_id] = _time.time()
    try:
        import redis
        from backend.config import get_settings

        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"
        event = json.dumps({"type": event_type, "task_id": task_id, **data})
        # Debug: log tool_results if present
        if event_type == "sub_agent_step" and "tool_results" in data:
            tr_preview = str(data.get("tool_results"))[:500]
            logger.debug(f"[_publish_event] tool_results preview: {tr_preview}")
        logger.debug(f"[_publish_event] Publishing to stream: {stream_key}, event_type={event_type}, data_keys={list(data.keys())}")

        r = _get_review_redis()
        try:
            # Use Lua script for atomic XADD with TTL
            # This ensures events are added atomically and expire after 1 hour
            lua_script = """
            local key = KEYS[1]
            local data = ARGV[1]
            local ttl = ARGV[2]
            local msg_id = redis.call('XADD', key, 'MAXLEN', '~', '1000', '*', 'data', data)
            redis.call('EXPIRE', key, ttl)
            return msg_id
            """
            msg_id = r.eval(lua_script, 1, stream_key, event, 3600)  # 1 hour TTL
            logger.info(f"[_publish_event] SUCCESS: stream={stream_key}, msg_id={msg_id}, event_type={event_type}")
        except Exception as redis_err:
            logger.error(f"[_publish_event] Redis error: {redis_err}")
            raise
    except Exception as e:
        logger.error(f"[_publish_event] FAILED to publish event: {e}, stream={stream_key}, event_type={event_type}, traceback={traceback.format_exc()}")

    # Persist step events to database for historical timeline display
    if event_type in ("step", "sub_agent_step") and data.get("step_number") is not None:
        _persist_step(task_id, event_type, data, session_factory=session_factory)


def _persist_step(task_id: str, event_type: str, data: dict, session_factory=None) -> None:
    """Persist a step event to agent_steps table for historical timeline.

    When session_factory is provided (from the main task engine), schedules the
    DB write via loop.create_task() on the current event loop — avoiding new
    engines, new threads, and new event loops.

    Falls back to a background thread + asyncio.run() only when no session_factory
    is available (backward compatibility).
    """
    try:
        if session_factory is not None:
            # Preferred path: reuse existing engine via current event loop
            loop = asyncio.get_running_loop()
            loop.create_task(_persist_step_async(session_factory, task_id, data))
        else:
            # Fallback: create a new engine (backward compatible, but wasteful)
            _persist_step_fallback(task_id, data)
    except RuntimeError:
        # No running loop — use fallback
        _persist_step_fallback(task_id, data)
    except Exception as e:
        logger.warning(f"[_persist_step] Failed to persist step: {e}", exc_info=True)


async def _persist_step_async(session_factory, task_id: str, data: dict) -> None:
    """Persist a step using the shared session_factory on the current event loop."""
    try:
        async with session_factory() as db:
            step_number = data["step_number"]
            todo_id = data.get("todo_id")

            from sqlalchemy import select as _sel
            query = _sel(AgentStep).where(
                AgentStep.task_id == task_id,
                AgentStep.step_number == step_number,
            )
            if todo_id:
                query = query.where(AgentStep.todo_id == todo_id)
            else:
                query = query.where(AgentStep.todo_id.is_(None))

            existing = (await db.execute(query)).scalar_one_or_none()
            if existing:
                if data.get("content") is not None:
                    existing.content = str(data["content"])[:500]
                if data.get("step_type"):
                    existing.step_type = data["step_type"]
                if data.get("tool_calls"):
                    existing.tool_args = {"tool_calls": data["tool_calls"]}
                if data.get("tool_results"):
                    existing.tool_result = {"tool_results": data["tool_results"]}
            else:
                step = AgentStep(
                    task_id=task_id,
                    todo_id=todo_id,
                    step_number=step_number,
                    step_type=data.get("step_type", "thought"),
                    content=str(data.get("content", ""))[:500],
                    tool_args={"tool_calls": data["tool_calls"]} if data.get("tool_calls") else None,
                    tool_result={"tool_results": data["tool_results"]} if data.get("tool_results") else None,
                )
                db.add(step)
            await db.commit()
    except Exception as e:
        logger.warning(f"[_persist_step_async] Failed: {e}", exc_info=True)


def _persist_step_fallback(task_id: str, data: dict) -> None:
    """Fallback: persist step using a new engine in a background thread."""
    import threading

    async def _save():
        sf, eng = create_session_factory()
        try:
            async with sf() as db:
                step_number = data["step_number"]
                todo_id = data.get("todo_id")
                from sqlalchemy import select as _sel
                query = _sel(AgentStep).where(
                    AgentStep.task_id == task_id,
                    AgentStep.step_number == step_number,
                )
                if todo_id:
                    query = query.where(AgentStep.todo_id == todo_id)
                else:
                    query = query.where(AgentStep.todo_id.is_(None))
                existing = (await db.execute(query)).scalar_one_or_none()
                if existing:
                    if data.get("content") is not None:
                        existing.content = str(data["content"])[:500]
                    if data.get("step_type"):
                        existing.step_type = data["step_type"]
                    if data.get("tool_calls"):
                        existing.tool_args = {"tool_calls": data["tool_calls"]}
                    if data.get("tool_results"):
                        existing.tool_result = {"tool_results": data["tool_results"]}
                else:
                    step = AgentStep(
                        task_id=task_id,
                        todo_id=todo_id,
                        step_number=step_number,
                        step_type=data.get("step_type", "thought"),
                        content=str(data.get("content", ""))[:500],
                        tool_args={"tool_calls": data["tool_calls"]} if data.get("tool_calls") else None,
                        tool_result={"tool_results": data["tool_results"]} if data.get("tool_results") else None,
                    )
                    db.add(step)
                await db.commit()
        finally:
            await eng.dispose()

    t = threading.Thread(target=lambda: asyncio.run(_save()), daemon=True)
    t.start()


async def _progress_watchdog(task_id: str, cancel_event: asyncio.Event):
    """Monitor task progress and trigger cancellation if no events for too long.

    Checks every 30s whether a new SSE event has been published for this task.
    If no event for agent_progress_timeout seconds (default 600s), the task
    is considered hung and the watchdog publishes an error event and sets
    cancel_event to stop the agent.
    """
    settings = get_settings()
    while not cancel_event.is_set():
        await asyncio.sleep(30)
        last_time = _task_last_event_times.get(task_id)
        if last_time is None:
            continue
        elapsed = _time.time() - last_time
        if elapsed > settings.agent_progress_timeout:
            logger.error(
                f"[progress_watchdog] Task {task_id} hung: "
                f"no event for {elapsed:.0f}s (limit {settings.agent_progress_timeout}s)"
            )
            _publish_event(task_id, "error", {
                "message": f"审查已超过 {elapsed:.0f} 秒没有进展，系统已自动停止任务"
            })
            cancel_event.set()
            break


@celery_app.task(bind=True, name="backend.tasks.review_tasks.run_review")
def run_review(self, task_id: str) -> dict:
    """Run the bid review process asynchronously.

    This task:
    1. Reads the tender document
    2. Reads the bid document
    3. Uses the BidReviewAgent to compare them
    4. Stores findings in the database
    5. Publishes SSE events via Redis

    Handles SIGTERM gracefully for graceful cancellation.
    """
    import threading
    import signal

    # Threading event to signal graceful shutdown from SIGTERM handler
    termination_event = threading.Event()

    def sigterm_handler(signum, frame):
        logger.warning(f"[run_review] Received SIGTERM for task_id={task_id}, initiating graceful shutdown")
        termination_event.set()
        # Also set the Redis cancel flag so heartbeat monitor picks it up
        set_task_cancelled(task_id)

    # Register SIGTERM handler for graceful cancellation
    old_sigterm_handler = signal.signal(signal.SIGTERM, sigterm_handler)

    async def _run():
        # Create session factory within the event loop to avoid 'different loop' error
        session_factory, engine = create_session_factory()
        from sqlalchemy import select

        try:
            from backend.models import Project, Document

            # Phase 1: Load data with a short-lived session.
            # The session is closed before the long-running master.run() call
            # so the connection does not sit idle for hours and get dropped
            # by network middleboxes or server-side timeout.
            async with session_factory() as db:
                result = await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                task = result.scalar_one_or_none()

                if not task:
                    return {"status": "error", "message": ERROR_TASK_NOT_FOUND}

                now = utc_now()
                task.status = "running"
                task.started_at = now
                task.last_heartbeat = now
                await db.commit()

                _publish_event(task_id, "status", {"status": "running"})

                # Get project
                result = await db.execute(select(Project).where(Project.id == task.project_id))
                project = result.scalar_one_or_none()

                if not project:
                    task.status = "failed"
                    task.error_message = ERROR_PROJECT_NOT_FOUND
                    await db.commit()
                    _publish_event(task_id, "error", {"message": ERROR_PROJECT_NOT_FOUND})
                    return {"status": "error", "message": ERROR_PROJECT_NOT_FOUND}

                # Get tender and bid documents
                result = await db.execute(
                    select(Document).where(Document.project_id == task.project_id)
                )
                documents = result.scalars().all()

                # Collect all parsed documents per type as [(filename, path), ...]
                tender_docs = [
                    (d.original_filename, d.parsed_markdown_path or d.parsed_html_path)
                    for d in documents
                    if d.doc_type == "tender"
                    and d.status == "parsed"
                    and (d.parsed_markdown_path or d.parsed_html_path)
                ]
                bid_docs = [
                    (d.original_filename, d.parsed_markdown_path or d.parsed_html_path)
                    for d in documents
                    if d.doc_type == "bid"
                    and d.status == "parsed"
                    and (d.parsed_markdown_path or d.parsed_html_path)
                ]

                if not tender_docs:
                    task.status = "failed"
                    task.error_message = ERROR_TENDER_NOT_FOUND
                    await db.commit()
                    _publish_event(task_id, "error", {"message": ERROR_TENDER_NOT_FOUND})
                    return {"status": "error", "message": ERROR_TENDER_NOT_FOUND}

                if not bid_docs:
                    task.status = "failed"
                    task.error_message = ERROR_BID_NOT_FOUND
                    await db.commit()
                    _publish_event(task_id, "error", {"message": ERROR_BID_NOT_FOUND})
                    return {"status": "error", "message": ERROR_BID_NOT_FOUND}

                # Extract needed values before session closes
                project_id = str(task.project_id)
                max_concurrency = task.max_concurrency
                user_id = ""
                if hasattr(project, 'user_id'):
                    user_id = str(project.user_id)
            # Session closed — connection returned to pool

            # Phase 2: Run review (no DB session held during the long-running agent)
            _publish_event(task_id, "progress", {"message": "正在分析文档..."})

            cancel_event = asyncio.Event()
            watchdog_task = asyncio.create_task(
                _progress_watchdog(task_id, cancel_event)
            )

            try:
                findings_count = await _run_agent_review(
                    task_id=task_id,
                    tender_docs=tender_docs,
                    bid_docs=bid_docs,
                    project_id=project_id,
                    user_id=user_id,
                    max_concurrency=max_concurrency,
                    session_factory=session_factory,
                    cancel_event=cancel_event,
                )
            finally:
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass
                _task_last_event_times.pop(task_id, None)

            # Phase 3: Success
            _publish_event(task_id, "complete", {
                "status": "completed",
                "findings_count": findings_count,
            })

            return {
                "status": "success",
                "task_id": task_id,
                "findings_count": findings_count,
            }

        except Exception as e:
            error_msg = str(e)
            # Use a fresh session to update task status, since the original
            # session was closed after Phase 1.
            try:
                async with session_factory() as status_db:
                    result = await status_db.execute(
                        select(ReviewTask).where(ReviewTask.id == task_id)
                    )
                    task = result.scalar_one_or_none()
                    if task:
                        if is_task_cancelled(task_id):
                            task.status = "cancelled"
                        else:
                            task.status = "failed"
                        task.error_message = error_msg
                        task.completed_at = utc_now()
                        if task.started_at and task.completed_at:
                            task.duration_seconds = utc_seconds_between(
                                task.started_at,
                                task.completed_at,
                            )
                        await status_db.commit()
                # 任务终态（failed/cancelled）落库后刷新用量汇总行，确保运营台拿到终态。
                from backend.services.usage_summary import refresh_task_summary
                await refresh_task_summary(task_id)
            except Exception as db_err:
                logger.error(f"[run_review] Failed to update task status for {task_id}: {db_err}")

            _publish_event(task_id, "error", {"message": error_msg})
            return {"status": "error", "message": error_msg}
        finally:
            await engine.dispose()
            # Clean up Redis cancel flag
            clear_task_cancelled(task_id)
            # Restore original SIGTERM handler
            signal.signal(signal.SIGTERM, old_sigterm_handler)
            # Check if we were terminated gracefully
            if termination_event.is_set():
                logger.warning(f"[run_review] Task {task_id} was terminated gracefully via SIGTERM")
                return {"status": "cancelled", "message": "任务已取消"}

    return run_async(_run())


@celery_app.task(bind=True, name="backend.tasks.review_tasks.merge_review_results")
def merge_review_results(self, project_id: str, latest_task_id: str) -> dict:
    """Merge historical review results for a project.

    This task:
    1. Queries all historical ReviewResult for the project
    2. Uses AI semantic similarity to deduplicate
    3. Stores merged results in project_review_results table
    4. Publishes SSE events for frontend progress
    """
    from celery.exceptions import SoftTimeLimitExceeded

    def event_cb(event_type: str, data: dict):
        _publish_event(latest_task_id, event_type, data, session_factory=None)

    async def _run_merge():
        session_factory, engine = create_session_factory()
        try:
            async with session_factory() as db:
                from backend.services.merge_service import MergeService
                from backend.agent.bid_review_agent import BidReviewAgent
                agent = None
                try:
                    agent = BidReviewAgent(
                        project_id=project_id,
                        tender_doc_path="",
                        bid_doc_path="",
                        user_id="system",
                        rule_doc_path="",
                        event_callback=None,
                        max_steps=1,
                    )
                    await agent.initialize()

                    merge_service = MergeService(db, agent)
                    merged_count, total_count = await merge_service.merge_project_results(
                        project_id=project_id,
                        latest_task_id=latest_task_id,
                        event_callback=event_cb,
                    )
                    return {"status": "success", "merged_count": merged_count, "total_count": total_count}
                except Exception as e:
                    logger.error(f"Merge failed: {e}")
                    event_cb("error", {"message": str(e)})
                    return {"status": "error", "message": str(e)}
                finally:
                    if agent is not None:
                        await agent.close()
        finally:
            await engine.dispose()

    try:
        return run_async(_run_merge())
    except SoftTimeLimitExceeded:
        logger.error(f"[merge_review_results] SoftTimeLimitExceeded for project {project_id}")
        _publish_event(latest_task_id, "error", {"message": "结果合并超时，请稍后重试"})
        return {"status": "error", "message": "结果合并超时，请稍后重试"}


def _record_agent_step(db, task_id: str, step_number: int, msg, tool_results: list | None = None) -> int:
    """Record agent steps from message history.

    每个 LLM 响应（assistant message）作为一个 step。
    Tool_calls 内嵌在 step 中，不独占 step_number。
    """
    if msg.role != "assistant":
        return step_number

    # 收集 tool_calls 数据
    tool_calls_data = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            func_name = tc.function.name
            tool_calls_data.append({
                "id": tc.id,
                "name": func_name,
                "arguments": tc.function.arguments,
            })

    # 收集 tool_results 数据 (tool_results is already the correct slice for this step)
    tool_results_data = []
    if tool_calls_data and tool_results and isinstance(tool_results, list):
        # tool_results is in the same order as tool_calls, match by index
        for i, tc_data in enumerate(tool_calls_data):
            if i < len(tool_results):
                tr = tool_results[i]
                tool_results_data.append({
                    "name": tr.get("name"),
                    "result": tr
                })

    # 确定 step_type
    if msg.content and tool_calls_data:
        step_type = "observation"
    elif msg.content:
        step_type = "thought"
    elif tool_calls_data:
        step_type = "tool_call"
    else:
        return step_number  # 跳过空消息

    # 记录一条 AgentStep
    step = AgentStep(
        task_id=task_id,
        step_number=step_number,
        step_type=step_type,
        content=str(msg.content)[:500] if msg.content else None,
        tool_name=None,
        tool_args={"tool_calls": tool_calls_data} if tool_calls_data else None,
        tool_result={"tool_results": tool_results_data} if tool_results_data else None,
    )
    db.add(step)

    # 返回下一个 step_number
    return step_number + 1


def _create_error_finding(error_msg: str) -> list[dict]:
    """Create an error finding result."""
    return [{
        "requirement_key": "review_error",
        "requirement_content": "审查过程发生异常",
        "bid_content": error_msg,
        "is_compliant": False,
        "severity": "critical",
        "location_page": None,
        "location_line": None,
        "suggestion": "请稍后重试；如果问题仍然存在，请联系管理员查看系统日志",
        "explanation": f"智能体执行失败：{error_msg}",
    }]


def _parse_findings_result(result) -> list[dict]:
    """Parse findings from agent result."""
    if isinstance(result, list):
        return result
    elif isinstance(result, dict):
        return result.get("findings", [])
    else:
        return [{
            "requirement_key": "review_completed",
            "requirement_content": "Bid review completed",
            "bid_content": "Review finished without structured findings",
            "is_compliant": True,
            "severity": "minor",
            "location_page": None,
            "location_line": None,
            "suggestion": None,
            "explanation": "Review completed but no structured findings were generated",
        }]


async def _run_agent_review(
    task_id: str,
    tender_docs: list[tuple[str, str]],
    bid_docs: list[tuple[str, str]],
    project_id: str,
    user_id: str,
    max_concurrency: int,
    session_factory,
    cancel_event: asyncio.Event,
) -> int:
    """Run the agent review process and return non-compliant findings count.

    Uses MasterAgent with SubAgentExecutor for multi-agent review.
    Saves findings incrementally after each sub-agent, then replaces with merged
    results after TaskMergeService completes.

    All database access uses short-lived sessions from session_factory to avoid
    holding connections idle during the long-running master.run() call.

    Args:
        task_id: Review task ID
        tender_docs: List of (filename, parsed_md_path) for tender documents
        bid_docs: List of (filename, parsed_md_path) for bid documents
        project_id: Project ID string
        user_id: User ID string
        max_concurrency: Max concurrent sub-agents
        session_factory: Async session factory for database operations.
        cancel_event: asyncio.Event for cancellation.
    """
    from backend.agent.master import MasterAgent
    from backend.services.todo_service import TodoService

    # Create a separate session for todo operations
    todo_db = session_factory()
    todo_service = TodoService(todo_db)

    # Validate all document paths exist
    for name, path in tender_docs:
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"招标文件尚未解析完成或解析结果不存在：{name}")

    for name, path in bid_docs:
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"投标文件尚未解析完成或解析结果不存在：{name}")

    # Rule library path from config
    rule_library_path = str(get_settings().rule_library_path)

    # Create event callback for SSE — passes session_factory so _publish_event
    # can reuse the main task engine instead of creating new ones.
    def event_cb(event_type: str, data: dict):
        _publish_event(task_id, event_type, data, session_factory=session_factory)

    # Callback for incremental saving after each sub-agent completes.
    # Uses an independent session from session_factory to avoid sharing the
    # main db session across concurrent sub-agent tasks.
    async def on_sub_agent_completed(findings: list[dict]):
        non_compliant = [f for f in findings if not f.get("is_compliant", False)]
        if not non_compliant:
            return
        async with session_factory() as cb_db:
            try:
                for finding_data in non_compliant:
                    finding = ReviewResult(
                        task_id=task_id,
                        **finding_data,
                    )
                    cb_db.add(finding)
                await cb_db.commit()
                logger.info(f"Incremental save: {len(non_compliant)} findings for task {task_id}")
            except Exception as e:
                logger.error(f"Incremental save failed: {e}")
                await cb_db.rollback()

    master = MasterAgent(
        project_id=project_id,
        rule_library_path=rule_library_path,
        tender_docs=tender_docs,
        bid_docs=bid_docs,
        user_id=user_id,
        event_callback=event_cb,
        cancel_event=cancel_event,
        on_sub_agent_result=on_sub_agent_completed,
        max_concurrency=max_concurrency,
    )

    try:
        # Pass session_factory so sub-agents can create their own sessions.
        # Wrap with an absolute total timeout — the final backstop that terminates
        # a stuck task no matter why progress stalled (stale DB pool, hung LLM/tool,
        # etc.). Normal tasks finish in 22-35 min; this is independent of the
        # progress watchdog (which keys off SSE event flow).
        total_timeout = get_settings().agent_total_timeout
        try:
            result = await asyncio.wait_for(
                master.run(todo_service, session_id=task_id, session_factory=session_factory),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[ABSOLUTE TIMEOUT] Task {task_id} exceeded total timeout "
                f"{total_timeout}s, terminating",
                exc_info=True,
            )
            cancel_event.set()  # signal sub-agents / LLM wrapper to bail at next checkpoint
            await asyncio.sleep(5)  # let in-flight sub-agents finish their finally blocks
            raise TimeoutError(
                f"任务执行超时（超过 {total_timeout // 60} 分钟），已强制终止"
            )

        if result.get("success"):
            # Count non-compliant findings and update task status
            # Use a fresh short-lived session instead of a long-held one
            # to avoid connection timeout issues.
            from sqlalchemy import select as _sel, func as _func
            async with session_factory() as update_db:
                count_result = await update_db.execute(
                    _sel(_func.count()).where(ReviewResult.task_id == task_id)
                )
                non_compliant_count = count_result.scalar() or 0
                logger.info(f"[_run_agent_review] project_id={project_id}, task_id={task_id}, non_compliant={non_compliant_count}")

                # Update task status
                task_result = await update_db.execute(
                    _sel(ReviewTask).where(ReviewTask.id == task_id)
                )
                review_task = task_result.scalar_one_or_none()
                if review_task:
                    review_task.status = "completed"
                    review_task.completed_at = utc_now()
                    if review_task.started_at and review_task.completed_at:
                        review_task.duration_seconds = utc_seconds_between(
                            review_task.started_at,
                            review_task.completed_at,
                        )
                    await update_db.commit()

                # 任务终态落库后刷新用量汇总行（确保运营台能拿到 completed 状态 + 时长）。
                # await 而非 fire-and-forget：保证终态一定写入汇总表。
                from backend.services.usage_summary import refresh_task_summary
                await refresh_task_summary(task_id)
                from backend.services.billing import settle_review_consumption
                await settle_review_consumption(task_id)

            logger.info(f"[_run_agent_review] Completed: {non_compliant_count} non-compliant findings")

            try:
                from backend.tasks.experience_tasks import extract_experience
                result = extract_experience.delay(str(task_id))
                logger.info(f"Dispatched experience extraction for task {task_id}, celery_id={result.id}")
            except Exception as e:
                logger.error(f"Failed to dispatch experience extraction for task {task_id}: {e}", exc_info=True)

            return non_compliant_count

        else:
            error_msg = result.get("error", "未知错误")
            event_cb("error", {"message": f"主智能体执行失败：{error_msg}"})
            raise Exception(error_msg)

    except Exception as e:
        logger.exception(f"MasterAgent execution failed for task {task_id}: {e}")
        event_cb("error", {"message": f"智能体执行失败：{str(e)}"})
        raise  # Re-raise so run_review._run() handles status update

    finally:
        await todo_db.close()
        # Final MCP cleanup: sub-agents skip individual cleanup to avoid
        # killing shared connections, so we clean up once here.
        from mini_agent.tools.mcp_loader import cleanup_mcp_connections
        await cleanup_mcp_connections()
