"""SSE (Server-Sent Events) service for real-time notifications."""

import asyncio
import logging
import time
from typing import AsyncGenerator, Optional

import redis

from backend.config import get_settings

logger = logging.getLogger(__name__)


class SSEConnectionManager:
    """Manages SSE connections for real-time updates using Redis Streams."""

    def __init__(self):
        self._redis_client: Optional[redis.Redis] = None

    async def connect(self, task_id: str, last_event_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Connect to SSE stream for a specific task.

        Uses Redis Streams with simple XREAD (not consumer groups) for
        reliable message delivery without pending message complexity.

        Args:
            task_id: The task ID to subscribe to
            last_event_id: The last event ID received before reconnection (for catch-up)

        Yields:
            SSE-formatted event strings
        """
        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"

        queue: asyncio.Queue = asyncio.Queue()
        event_count = 0

        def redis_listener():
            """Blocking listener that runs in a separate thread using sync redis."""
            r = None
            try:
                r = redis.from_url(settings.redis_url, decode_responses=True)
                logger.info(f"SSE subscribed to stream: {stream_key}")

                # Start reading from last_event_id if provided, otherwise from beginning
                # Use XREAD instead of XREADGROUP to avoid pending message issues
                start_id = last_event_id if last_event_id else "0"

                while True:
                    try:
                        # XREAD reads directly from stream without consumer groups
                        # Block for 1 second waiting for new messages
                        messages = r.xread(
                            {stream_key: start_id},  # Read from last_event_id or start
                            count=10,
                            block=1000  # 1 second block
                        )
                        if messages:
                            for stream, entries in messages:
                                for msg_id, data in entries:
                                    queue.put_nowait((msg_id, data))
                                    # Update start_id to continue from this point
                                    start_id = msg_id
                    except Exception as e:
                        logger.warning(f"Stream read error: {e}")
                        time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Redis listener error: {e}")
            finally:
                if r:
                    try:
                        r.close()
                    except Exception:
                        pass
                queue.put_nowait(None)  # Signal end of stream

        try:
            listener_thread = asyncio.create_task(
                asyncio.to_thread(redis_listener)
            )
            await asyncio.sleep(0.3)  # Wait for subscription

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                msg_id, data = item
                event_count += 1
                # data is a dict like {'data': '<json_string>'}, extract the JSON string
                json_data = data.get('data', '') if isinstance(data, dict) else str(data)
                logger.info(f"SSE yielding event {event_count}: {str(json_data)[:100]}")
                yield f"id: {msg_id}\ndata: {json_data}\n\n"
        except asyncio.CancelledError:
            logger.info("SSE connection cancelled")
            raise


# Global SSE manager instance
sse_manager = SSEConnectionManager()
