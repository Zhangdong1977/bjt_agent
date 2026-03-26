"""Tests for async Redis usage in Celery tasks."""

import pytest
import inspect


class TestRedisPublishAsync:
    """Verify Redis publish is handled asynchronously."""

    def test_publish_event_uses_thread_pool_or_async(self):
        """Check if _publish_event uses thread pool or async redis."""
        from backend.tasks import review_tasks

        # Get the source code of _publish_event
        source = inspect.getsource(review_tasks._publish_event)

        # Check for proper async patterns
        uses_thread_pool = "ThreadPoolExecutor" in source
        uses_async_redis = "redis.asyncio" in source or "from redis.asyncio" in source

        # Should use either thread pool or async redis
        assert uses_thread_pool or uses_async_redis, \
            "_publish_event should use ThreadPoolExecutor or async redis"

    def test_publish_event_no_direct_blocking_call(self):
        """Verify _publish_event doesn't make direct blocking redis calls."""
        from backend.tasks import review_tasks

        source = inspect.getsource(review_tasks._publish_event)

        # If using redis.from_url directly (sync) without thread pool, it's blocking
        uses_direct_sync = "redis.from_url(" in source and "ThreadPoolExecutor" not in source

        assert not uses_direct_sync, \
            "_publish_event makes direct synchronous redis call without thread pool"
