"""Review tasks - handles the async bid review process."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from backend.celery_app import celery_app
from backend.models import ReviewTask, ReviewResult, AgentStep

logger = logging.getLogger(__name__)


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
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from backend.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# Error message constants
ERROR_TASK_NOT_FOUND = "Task not found"
ERROR_PROJECT_NOT_FOUND = "Project not found"
ERROR_TENDER_NOT_FOUND = "Tender document not found"
ERROR_BID_NOT_FOUND = "Bid document not found"


def _get_stream_key(task_id: str) -> str:
    """Get Redis Stream key for a task's SSE events.

    Uses sse:stream:{task_id} pattern for Redis Streams.
    """
    return f"sse:stream:{task_id}"


def _publish_event(task_id: str, event_type: str, data: dict) -> None:
    """Publish an event to Redis Stream for SSE forwarding.

    Uses Redis Streams for reliable message delivery with persistence.
    XADD + Lua script ensures atomic stream operations.
    """
    import traceback
    try:
        import redis
        from backend.config import get_settings

        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"
        event = json.dumps({"type": event_type, "task_id": task_id, **data})
        logger.info(f"[_publish_event] Publishing to stream: {stream_key}, event_type={event_type}, data_keys={list(data.keys())}")

        r = redis.from_url(settings.redis_url)
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
        finally:
            r.close()
    except Exception as e:
        logger.error(f"[_publish_event] FAILED to publish event: {e}, stream={stream_key}, event_type={event_type}, traceback={traceback.format_exc()}")


@celery_app.task(bind=True, name="backend.tasks.review_tasks.run_review")
def run_review(self, task_id: str) -> dict:
    """Run the bid review process asynchronously.

    This task:
    1. Reads the tender document
    2. Reads the bid document
    3. Uses the BidReviewAgent to compare them
    4. Stores findings in the database
    5. Publishes SSE events via Redis
    """
    async def _run():
        # Create session factory within the event loop to avoid 'different loop' error
        session_factory = create_session_factory()
        async with session_factory() as db:
            from sqlalchemy import select

            # Get the review task
            result = await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
            task = result.scalar_one_or_none()

            if not task:
                return {"status": "error", "message": ERROR_TASK_NOT_FOUND}

            try:
                task.status = "running"
                task.started_at = datetime.utcnow()
                await db.flush()

                # Send SSE event (Redis Streams handles reliability - no sleep needed)
                _publish_event(task_id, "status", {"status": "running"})

                # Get project and documents
                from backend.models import Project, Document
                result = await db.execute(select(Project).where(Project.id == task.project_id))
                project = result.scalar_one_or_none()

                if not project:
                    task.status = "failed"
                    task.error_message = ERROR_PROJECT_NOT_FOUND
                    await db.flush()
                    await db.commit()
                    _publish_event(task_id, "error", {"message": ERROR_PROJECT_NOT_FOUND})
                    return {"status": "error", "message": ERROR_PROJECT_NOT_FOUND}

                # Get tender and bid documents
                result = await db.execute(
                    select(Document).where(Document.project_id == task.project_id)
                )
                documents = result.scalars().all()

                tender_doc = next((d for d in documents if d.doc_type == "tender"), None)
                bid_doc = next((d for d in documents if d.doc_type == "bid"), None)

                if not tender_doc:
                    task.status = "failed"
                    task.error_message = ERROR_TENDER_NOT_FOUND
                    await db.flush()
                    await db.commit()
                    _publish_event(task_id, "error", {"message": ERROR_TENDER_NOT_FOUND})
                    return {"status": "error", "message": ERROR_TENDER_NOT_FOUND}

                if not bid_doc:
                    task.status = "failed"
                    task.error_message = ERROR_BID_NOT_FOUND
                    await db.flush()
                    await db.commit()
                    _publish_event(task_id, "error", {"message": ERROR_BID_NOT_FOUND})
                    return {"status": "error", "message": ERROR_BID_NOT_FOUND}

                # Send progress event
                _publish_event(task_id, "progress", {"message": "Starting document analysis..."})

                # Run the agent review
                findings = await _run_agent_review(task_id, tender_doc, bid_doc, db)

                # Store only non-compliant findings (ReviewResult is for non-compliance)
                non_compliant_findings = [f for f in findings if not f.get("is_compliant", False)]
                logger.info(f"[_run] project_id={task.project_id}, task_id={task_id}, total_findings={len(findings)}, non_compliant={len(non_compliant_findings)}")
                for finding_data in non_compliant_findings:
                    finding = ReviewResult(
                        task_id=task_id,
                        **finding_data,
                    )
                    db.add(finding)

                task.status = "completed"
                task.completed_at = datetime.utcnow()
                await db.flush()
                await db.commit()

                # Trigger merge task after successful completion
                from backend.tasks.review_tasks import merge_review_results
                merge_review_results.delay(project_id=task.project_id, latest_task_id=task_id)

                # Send completion event
                _publish_event(task_id, "complete", {
                    "status": "completed",
                    "findings_count": len(non_compliant_findings),
                })

                return {
                    "status": "success",
                    "task_id": task_id,
                    "findings_count": len(non_compliant_findings),
                }

            except Exception as e:
                task.status = "failed"
                task.error_message = str(e)
                task.completed_at = datetime.utcnow()
                await db.flush()
                await db.commit()
                _publish_event(task_id, "error", {"message": str(e)})
                return {"status": "error", "message": str(e)}

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
    def event_cb(event_type: str, data: dict):
        _publish_event(latest_task_id, event_type, data)

    async def _run_merge():
        session_factory = create_session_factory()
        async with session_factory() as db:
            from backend.services.merge_service import MergeService
            from backend.agent.bid_review_agent import BidReviewAgent
            try:
                # Create agent for merge decisions (paths don't matter since we only use MergeDeciderTool)
                agent = BidReviewAgent(
                    project_id=project_id,
                    tender_doc_path="",
                    bid_doc_path="",
                    user_id="system",
                    event_callback=None,
                    max_steps=1,
                )

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

    return run_async(_run_merge())


def _record_agent_step(db, task_id: str, step_number: int, msg, tool_results: dict | None = None) -> int:
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
                "name": func_name,
                "arguments": tc.function.arguments,
            })

    # 收集 tool_results 数据
    tool_results_data = []
    if tool_calls_data and tool_results:
        for tc_data in tool_calls_data:
            func_name = tc_data["name"]
            if func_name in tool_results:
                tool_results_data.append({
                    "name": func_name,
                    "result": tool_results[func_name]
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
        "requirement_content": "Review process encountered an error",
        "bid_content": error_msg,
        "is_compliant": False,
        "severity": "critical",
        "location_page": None,
        "location_line": None,
        "suggestion": "Check system logs for details",
        "explanation": f"Agent execution failed: {error_msg}",
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
    tender_doc,
    bid_doc,
    db,
) -> list[dict]:
    """Run the agent review process and return findings.

    Uses BidReviewAgent with Mini-Max LLM for actual comparison.
    """
    from backend.agent.bid_review_agent import BidReviewAgent

    # Get document paths
    tender_path = tender_doc.parsed_md_path or ""
    bid_path = bid_doc.parsed_md_path or ""

    if not tender_path or not Path(tender_path).exists():
        raise FileNotFoundError("Tender document not parsed")

    if not bid_path or not Path(bid_path).exists():
        raise FileNotFoundError("Bid document not parsed")

    # Create event callback for SSE
    def event_cb(event_type: str, data: dict):
        _publish_event(task_id, event_type, data)

    # Initialize the agent
    user_id = ""
    if hasattr(tender_doc.project, 'user_id'):
        user_id = str(tender_doc.project.user_id)

    agent = BidReviewAgent(
        project_id=str(tender_doc.project_id),
        tender_doc_path=tender_path,
        bid_doc_path=bid_path,
        user_id=user_id,
        event_callback=event_cb,
        max_steps=200,
    )

    # Note: BidReviewAgent.run_review() sends its own initialization event internally
    # Run the agent
    step_number = 1
    try:
        result = await agent.run_review()

        # Record agent steps from message history
        for msg in agent.get_history():
            if msg.role == "assistant":
                step_number = _record_agent_step(db, task_id, step_number, msg, agent._tool_results)

        await db.flush()
        return _parse_findings_result(result)

    except Exception as e:
        event_cb("error", {"message": f"Agent error: {str(e)}"})
        return _create_error_finding(str(e))
