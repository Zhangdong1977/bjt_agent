"""Tests for SSE service Redis Streams consumer."""

import asyncio
import pytest
import time
import uuid

from backend.services.sse_service import SSEConnectionManager
from backend.tasks.review_tasks import _publish_event


class TestSSEStreamsConsumer:
    """Test SSE service Redis Streams consumer."""

    @pytest.mark.asyncio
    async def test_streams_consumer_receives_live_events(self):
        """Test that consumer receives events as they are published (not batched)."""
        manager = SSEConnectionManager()
        task_id = f"test_task_{uuid.uuid4().hex[:8]}"

        received_events = []
        event_times = []

        async def consume():
            async for event in manager.connect(task_id):
                received_events.append(event)
                event_times.append(time.time())
                if len(received_events) >= 3:
                    break

        # Start consumer first
        consumer_task = asyncio.create_task(consume())

        # Give consumer time to subscribe
        await asyncio.sleep(0.2)

        # Publish events with delays
        _publish_event(task_id, "status", {"status": "running"})
        await asyncio.sleep(0.3)
        _publish_event(task_id, "step", {"step_number": 1, "content": "First step"})
        await asyncio.sleep(0.3)
        _publish_event(task_id, "step", {"step_number": 2, "content": "Second step"})

        await consumer_task

        # Verify events arrived with time gaps (not all at once)
        # Pub/Sub would deliver instantly; Streams with delays should show gaps
        assert len(received_events) == 3
        # Each event should arrive at least 0.15s apart (accounting for thread scheduling)
        assert event_times[1] - event_times[0] >= 0.15, f"Events arrived too fast (batched?): {event_times}"
        assert event_times[2] - event_times[1] >= 0.15, f"Events arrived too fast (batched?): {event_times}"
