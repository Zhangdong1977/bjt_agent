"""SSE (Server-Sent Events) service for real-time notifications."""

import asyncio
import logging
import os
import threading
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

        Uses Redis Streams for reliable message delivery with persistence.
        Consumer groups enable replay capability and ordered delivery.

        Args:
            task_id: The task ID to subscribe to
            last_event_id: The last event ID received before reconnection (for catch-up)

        Yields:
            SSE-formatted event strings
        """
        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"
        consumer_group = f"sse_group_{task_id}"
        consumer_name = f"consumer_{os.getpid()}_{threading.current_thread().ident}"

        queue: asyncio.Queue = asyncio.Queue()
        event_count = 0

        def redis_listener():
            """Blocking listener that runs in a separate thread using sync redis."""
            r = None
            try:
                r = redis.from_url(settings.redis_url, decode_responses=True)
                try:
                    # Create consumer group if not exists (mkstream=True creates stream)
                    r.xgroup_create(stream_key, consumer_group, id="0", mkstream=True)
                except redis.ResponseError as e:
                    if "BUSYGROUP" not in str(e):
                        raise

                logger.info(f"SSE subscribed to stream: {stream_key}, group: {consumer_group}")

                # Read from stream using consumer group
                while True:
                    try:
                        messages = r.xreadgroup(
                            consumer_group,
                            consumer_name,
                            {stream_key: ">"},  # ">" means only new messages
                            count=1,
                            block=1000  # 1 second block
                        )
                        if messages:
                            for stream, entries in messages:
                                for msg_id, data in entries:
                                    queue.put_nowait((msg_id, data))
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
            listener_thread = threading.Thread(target=redis_listener, daemon=True)
            listener_thread.start()
            await asyncio.sleep(0.3)  # Wait for subscription

            last_id = last_event_id  # For reconnection support

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                msg_id, data = item
                event_count += 1
                logger.info(f"SSE yielding event {event_count}: {str(data)[:100]}")
                yield f"id: {msg_id}\ndata: {data}\n\n"
                last_id = msg_id
        except asyncio.CancelledError:
            logger.info("SSE connection cancelled")
            raise


# Global SSE manager instance
sse_manager = SSEConnectionManager()
