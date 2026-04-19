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
        import traceback
        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"

        logger.info(f"[SSE.connect] Connecting to stream: {stream_key}, last_event_id={last_event_id}")

        queue: asyncio.Queue = asyncio.Queue()
        event_count = 0

        def redis_listener():
            """Blocking listener that runs in a separate thread using sync redis."""
            r = None
            try:
                r = redis.from_url(settings.redis_url, decode_responses=True)
                logger.info(f"[SSE.redis_listener] Subscribed to stream: {stream_key}")

                # Start reading from last_event_id if provided, otherwise from beginning
                # Use XREAD instead of XREADGROUP to avoid pending message issues
                start_id = last_event_id if last_event_id else "0"
                logger.info(f"[SSE.redis_listener] Starting XREAD from id: {start_id}")

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
                            logger.info(f"[SSE.redis_listener] Received {len(messages)} messages from {stream_key}")
                            for stream, entries in messages:
                                for msg_id, data in entries:
                                    queue.put_nowait((msg_id, data))
                                    # Update start_id to continue from this point
                                    start_id = msg_id
                                    logger.info(f"[SSE.redis_listener] Enqueued msg_id={msg_id}")
                        else:
                            logger.debug(f"[SSE.redis_listener] No messages, waiting... stream={stream_key}")
                    except Exception as e:
                        logger.warning(f"[SSE.redis_listener] Stream read error: {e}")
                        time.sleep(0.1)
            except Exception as e:
                logger.error(f"[SSE.redis_listener] Redis listener error: {e}, traceback={traceback.format_exc()}")
            finally:
                if r:
                    try:
                        r.close()
                    except Exception:
                        pass
                queue.put_nowait(None)  # Signal end of stream
                logger.info(f"[SSE.redis_listener] Listener ending for stream={stream_key}")

        try:
            logger.info(f"[SSE.connect] Starting listener thread for {stream_key}")
            listener_thread = asyncio.create_task(
                asyncio.to_thread(redis_listener)
            )
            await asyncio.sleep(0.3)  # Wait for subscription
            logger.info(f"[SSE.connect] Listener started, entering event loop for {stream_key}")

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    logger.info(f"[SSE.connect] Received None (end of stream) for {stream_key}")
                    break
                msg_id, data = item
                event_count += 1
                # data is a dict like {'data': '<json_string>'}, extract the JSON string
                json_data = data.get('data', '') if isinstance(data, dict) else str(data)
                logger.info(f"[SSE.connect] Yielding event {event_count}: msg_id={msg_id}, data_type={type(json_data)}, data_preview={str(json_data)[:200]}")
                # Debug: parse and check tool_results
                if isinstance(json_data, str) and 'tool_results' in json_data:
                    import json as json_lib
                    try:
                        parsed = json_lib.loads(json_data)
                        if 'tool_results' in parsed:
                            tr = parsed['tool_results']
                            logger.info(f"[SSE.connect] tool_results type={type(tr)}, len={len(tr) if isinstance(tr, list) else 'N/A'}, first_item={str(tr[0])[:200] if isinstance(tr, list) and tr else 'empty'}")
                    except Exception as e:
                        logger.error(f"[SSE.connect] JSON parse error: {e}")
                elif json_data == '':
                    logger.warning(f"[SSE.connect] json_data is EMPTY! data dict: {data}")
                yield f"id: {msg_id}\ndata: {json_data}\n\n"
        except asyncio.CancelledError:
            logger.info(f"[SSE.connect] SSE connection cancelled for {stream_key}")
            raise
        except Exception as e:
            logger.error(f"[SSE.connect] SSE connection error: {e}, stream={stream_key}, traceback={traceback.format_exc()}")
            raise


# Global SSE manager instance
sse_manager = SSEConnectionManager()
