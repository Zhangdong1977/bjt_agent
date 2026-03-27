"""Tests for Redis Streams usage in Celery tasks."""

import pytest
import inspect


class TestRedisStreamsPublish:
    """Verify Redis Streams publish is handled correctly."""

    def test_publish_event_uses_redis_streams(self):
        """Check if _publish_event uses Redis Streams (XADD)."""
        from backend.tasks import review_tasks

        # Get the source code of _publish_event
        source = inspect.getsource(review_tasks._publish_event)

        # Check for Redis Streams patterns
        uses_xadd = "XADD" in source
        uses_eval = "eval" in source  # Lua script for atomicity

        # Should use XADD with Lua script for atomicity
        assert uses_xadd and uses_eval, \
            "_publish_event should use XADD with Lua script for atomic stream operations"

    def test_publish_event_uses_stream_key_pattern(self):
        """Verify _publish_event uses the correct stream key pattern."""
        from backend.tasks import review_tasks

        source = inspect.getsource(review_tasks._publish_event)

        # Should use sse:stream:{task_id} pattern
        assert "sse:stream:" in source, \
            "_publish_event should use 'sse:stream:{task_id}' key pattern"
