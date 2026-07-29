"""Regression tests for SSE connection lifecycle and silent streams."""

import asyncio
import inspect
import json

import pytest

from backend.services import sse_service
from backend.services.sse_service import SSEConnectionManager


class FakeRedis:
    def __init__(self, reads):
        self._reads = iter(reads)
        self.ping_called = False
        self.closed = False

    async def ping(self):
        self.ping_called = True
        return True

    async def xread(self, *_args, **_kwargs):
        return next(self._reads)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_sse_yields_stream_event_and_closes_redis(monkeypatch):
    fake = FakeRedis(
        [[("sse:stream:task-1", [("123-0", {"data": '{"type":"status"}'})])]]
    )
    monkeypatch.setattr(sse_service.redis, "from_url", lambda *_a, **_kw: fake)

    stream = SSEConnectionManager().connect("task-1")
    event = await anext(stream)

    assert fake.ping_called
    assert "id: 123-0" in event
    assert 'data: {"type":"status"}' in event

    await stream.aclose()
    assert fake.closed


@pytest.mark.asyncio
async def test_sse_idle_stream_emits_application_heartbeat(monkeypatch):
    fake = FakeRedis([[]])
    monkeypatch.setattr(sse_service.redis, "from_url", lambda *_a, **_kw: fake)

    stream = SSEConnectionManager().connect("task-idle")
    event = await anext(stream)
    payload = json.loads(event.removeprefix("data: ").strip())

    assert payload == {"type": "sse_heartbeat", "task_id": "task-idle"}

    await stream.aclose()
    assert fake.closed


@pytest.mark.asyncio
async def test_sse_cancellation_closes_blocked_redis_reader(monkeypatch):
    read_started = asyncio.Event()
    read_cancelled = asyncio.Event()

    class BlockingRedis(FakeRedis):
        def __init__(self):
            super().__init__([])

        async def xread(self, *_args, **_kwargs):
            read_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                read_cancelled.set()
                raise

    fake = BlockingRedis()
    monkeypatch.setattr(sse_service.redis, "from_url", lambda *_a, **_kw: fake)

    stream = SSEConnectionManager().connect("task-blocked")
    next_event = asyncio.create_task(anext(stream))
    await asyncio.wait_for(read_started.wait(), timeout=1)
    next_event.cancel()

    with pytest.raises(asyncio.CancelledError):
        await next_event

    assert read_cancelled.is_set()
    assert fake.closed


def test_sse_does_not_use_unbounded_background_threads():
    source = inspect.getsource(SSEConnectionManager.connect)

    assert "asyncio.to_thread(" not in source
    assert "await client.xread" in source
    assert "await client.aclose()" in source
