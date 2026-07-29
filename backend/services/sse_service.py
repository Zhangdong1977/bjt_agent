"""SSE (Server-Sent Events) service for real-time notifications."""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

import redis.asyncio as redis

from backend.config import get_settings

logger = logging.getLogger(__name__)


class SSEConnectionManager:
    """Read task events from Redis Streams without occupying worker threads."""

    _READ_BLOCK_MS = 10_000

    async def connect(
        self,
        task_id: str,
        last_event_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Yield events from one Redis Stream and release it on disconnect.

        Every SSE connection owns one asynchronous Redis client.  The previous
        implementation ran an endless synchronous ``XREAD`` inside
        ``asyncio.to_thread``.  Closing the browser connection cancelled only
        the outer coroutine, leaving the thread and Redis socket alive forever.
        Once the executor was exhausted, new SSE responses stayed HTTP 200 but
        never delivered a body.
        """
        settings = get_settings()
        stream_key = f"sse:stream:{task_id}"
        start_id = last_event_id or "0-0"
        event_count = 0
        client: Optional[redis.Redis] = None

        logger.info(
            "[SSE.connect] Connecting to stream: %s, last_event_id=%s",
            stream_key,
            last_event_id,
        )

        try:
            client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5.0,
                socket_timeout=15.0,
                health_check_interval=30,
            )
            # Fail the response promptly when Redis is unavailable instead of
            # exposing a silent, permanently open SSE connection.
            await client.ping()
            logger.info(
                "[SSE.connect] Redis reader ready: stream=%s, start_id=%s",
                stream_key,
                start_id,
            )

            while True:
                messages = await client.xread(
                    {stream_key: start_id},
                    count=10,
                    block=self._READ_BLOCK_MS,
                )

                if not messages:
                    # Application-level heartbeat lets the browser distinguish
                    # a healthy idle stream from an HTTP-200 connection whose
                    # Redis reader never started.
                    heartbeat = json.dumps(
                        {"type": "sse_heartbeat", "task_id": task_id},
                        ensure_ascii=False,
                    )
                    yield f"data: {heartbeat}\n\n"
                    continue

                for _stream, entries in messages:
                    for message_id, data in entries:
                        start_id = message_id
                        json_data = (
                            data.get("data", "")
                            if isinstance(data, dict)
                            else str(data)
                        )
                        if not json_data:
                            logger.warning(
                                "[SSE.connect] Empty event payload: stream=%s, "
                                "message_id=%s",
                                stream_key,
                                message_id,
                            )
                            continue

                        event_count += 1
                        logger.debug(
                            "[SSE.connect] Yielding event %s: stream=%s, "
                            "message_id=%s",
                            event_count,
                            stream_key,
                            message_id,
                        )
                        yield f"id: {message_id}\ndata: {json_data}\n\n"
        except asyncio.CancelledError:
            logger.info("[SSE.connect] Connection cancelled: stream=%s", stream_key)
            raise
        except Exception:
            logger.exception("[SSE.connect] Stream failed: stream=%s", stream_key)
            raise
        finally:
            if client is not None:
                await client.aclose()
            logger.info(
                "[SSE.connect] Connection closed: stream=%s, events=%s",
                stream_key,
                event_count,
            )


sse_manager = SSEConnectionManager()
