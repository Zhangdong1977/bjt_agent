"""Tests for SSE connection cleanup."""

import pytest
import inspect


class TestSSECleanup:
    """Verify SSE connections are properly cleaned up."""

    def test_sse_cleanup_properly_closes_redis(self):
        """Check that SSE connect method properly closes redis connection."""
        from backend.services import sse_service

        # Get the source code of connect method
        source = inspect.getsource(sse_service.SSEConnectionManager.connect)

        # Check for proper cleanup sequence
        has_unsubscribe = "unsubscribe" in source
        has_close = "close()" in source or ".close()" in source
        has_aclose = "aclose()" in source

        assert has_unsubscribe and has_close and has_aclose, \
            "SSE connect should properly cleanup: unsubscribe, close pubsub, close redis"

    def test_sse_cleanup_uses_await_for_aclose(self):
        """Verify aclose is awaited, not just called."""
        from backend.services import sse_service

        source = inspect.getsource(sse_service.SSEConnectionManager.connect)

        # Check that aclose is awaited (has await keyword before it)
        # Look for pattern like "await r.aclose()" not just "r.aclose()"
        has_await_aclose = "await" in source and "aclose()" in source

        assert has_await_aclose, \
            "aclose() should be awaited to properly close connection"

    def test_sse_cleanup_order_correct(self):
        """Verify cleanup happens in correct order: unsubscribe -> close -> aclose."""
        from backend.services import sse_service

        source = inspect.getsource(sse_service.SSEConnectionManager.connect)

        # Find positions
        unsub_pos = source.find("unsubscribe")
        close_pos = source.find("close()")
        aclose_pos = source.find("aclose()")

        # All should exist
        assert unsub_pos != -1, "Should call unsubscribe"
        assert close_pos != -1, "Should call close on pubsub"
        assert aclose_pos != -1, "Should call aclose on redis"

        # Order should be: unsubscribe -> close -> aclose
        assert unsub_pos < close_pos < aclose_pos, \
            "Cleanup order should be: unsubscribe -> close -> aclose"
