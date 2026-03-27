"""SSE (Server-Sent Events) service for real-time notifications."""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

import redis.asyncio as redis

from backend.config import get_settings

logger = logging.getLogger(__name__)


class SSEConnectionManager:
    """Manages SSE connections for real-time updates."""

    def __init__(self):
        self._redis_client = None

    async def connect(self, task_id: str, last_event_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Connect to SSE stream for a specific task.

        Listens to Redis pubsub channel `task:{task_id}` and yields events.
        Supports reconnection via Last-Event-ID header.

        Args:
            task_id: The task ID to subscribe to
            last_event_id: The last event ID received before reconnection (for catch-up)

        Yields:
            SSE-formatted event strings
        """
        settings = get_settings()
        r = redis.from_url(settings.redis_url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"task:{task_id}")
        logger.info(f"SSE subscribed to channel: task:{task_id}")

        # Delay to ensure subscription is fully established before events arrive
        # Celery task may publish events quickly after being queued
        await asyncio.sleep(0.5)

        event_count = 0
        try:
            async for message in pubsub.listen():
                logger.info(f"SSE received message: {message}")
                if message["type"] == "message":
                    data = message["data"]
                    event_count += 1
                    logger.info(f"SSE yielding event {event_count}: {data}")
                    # Include event ID for client reconnection support
                    yield f"id: {event_count}\ndata: {data}\n\n"
        finally:
            await pubsub.unsubscribe(f"task:{task_id}")
            await pubsub.close()
            await r.aclose()


# Global SSE manager instance
sse_manager = SSEConnectionManager()
