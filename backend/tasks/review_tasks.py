"""Review tasks - handles the async bid review process."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from backend.celery_app import celery_app
from backend.models import async_session_factory, ReviewTask, ReviewResult, AgentStep

logger = logging.getLogger(__name__)


def run_async(coro):
    """Helper to run async function in sync context.

    Uses an existing event loop if available, otherwise creates a new one.
    This pattern is safer for Celery workers than asyncio.run() which creates
    and closes a new loop each time.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


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
    try:
        import redis
        from backend.config import get_settings

        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"
        event = json.dumps({"type": event_type, "task_id": task_id, **data})
        logger.info(f"Publishing event to stream: {stream_key} -> {event}")

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
            logger.info(f"Published event to stream: {stream_key}, msg_id={msg_id}")
        finally:
            r.close()
    except Exception as e:
        logger.warning(f"Failed to publish event to Redis Stream: {e}")


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
        async with async_session_factory() as db:
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
                logger.info(f"Agent returned {len(findings)} findings, {len(non_compliant_findings)} non-compliant")
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


def _record_agent_step(db, task_id: str, step_number: int, msg) -> int:
    """Record agent steps from message history to database.

    Records both observation (assistant content) and tool_call steps.
    When tool_calls exist, observation and first tool_call share the same step_number
    to match the SSE event pattern.

    Note: SSE events are already sent by BidReviewAgent.run_review() during execution.
    This function only persists steps to the database after the fact.

    Returns the next step number.
    """
    if msg.tool_calls:
        # Record observation step with the step_number (same as SSE pattern)
        if msg.content:
            observation = AgentStep(
                task_id=task_id,
                step_number=step_number,
                step_type="observation",
                content=str(msg.content)[:500],
                tool_name=None,
            )
            db.add(observation)

        # Record each tool_call with sequential step_number AFTER the observation
        first_tool_step_number = step_number
        for tc in msg.tool_calls:
            step = AgentStep(
                task_id=task_id,
                step_number=first_tool_step_number,
                step_type="tool_call",
                content=f"Called {tc.function.name}",
                tool_name=tc.function.name,
                tool_args=tc.function.arguments,
            )
            db.add(step)
            first_tool_step_number += 1

        # Return the next available step_number (after all tool_calls)
        return first_tool_step_number
    elif msg.content:
        # No tool_calls, record as thought step
        step = AgentStep(
            task_id=task_id,
            step_number=step_number,
            step_type="thought",
            content=str(msg.content)[:500],
            tool_name=None,
        )
        db.add(step)
        step_number += 1
        return step_number

    return step_number


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
        max_steps=100,
    )

    # Note: BidReviewAgent.run_review() sends its own initialization event internally
    # Run the agent
    step_number = 1
    try:
        result = await agent.run_review()

        # Record agent steps from message history
        for msg in agent.get_history():
            if msg.role == "assistant":
                step_number = _record_agent_step(db, task_id, step_number, msg)

        await db.flush()
        return _parse_findings_result(result)

    except Exception as e:
        event_cb("error", {"message": f"Agent error: {str(e)}"})
        return _create_error_finding(str(e))
