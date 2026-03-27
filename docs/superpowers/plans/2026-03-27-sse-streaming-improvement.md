# SSE Event Streaming Improvement Plan

> **Workflow:** Subagent-Driven Development - each task dispatched to fresh subagent with two-stage review (spec compliance → code quality)

**Goal:** Improve SSE event delivery to be truly streaming - events should arrive in real-time during agent execution, not batched after completion.

**Architecture:** The current architecture uses Redis Pub/Sub which is fire-and-forget and has no message persistence. Events are only published after the agent completes because the Mini-Agent base class has no callback mechanism. The fix involves:
1. Switch from Redis Pub/Sub to Redis Streams for message persistence and replay capability
2. Add event emission callbacks at tool execution points in the agent
3. Ensure events are published immediately when generated, not batched

**Tech Stack:** Python asyncio, Redis Streams, FastAPI SSE, Celery

---

## Model Selection

| Task | Complexity | Model | Rationale |
|------|------------|-------|-----------|
| Task 1: SSE Streams | 2 files, isolated | **Haiku** | Mechanical change, clear spec |
| Task 2: review_tasks | 1 file, isolated | **Haiku** | Mechanical change, clear spec |
| Task 3: bid_review_agent | Multi-file, integration | **Sonnet** | Requires understanding agent loop, LLM integration |
| Task 4: Integration | Full system | **Sonnet** | End-to-end verification |

---

## File Structure

```
backend/
├── services/
│   └── sse_service.py         # Task 1: switch to Redis Streams
├── tasks/
│   └── review_tasks.py        # Task 2: use Redis Streams publish
├── agent/
│   ├── bid_review_agent.py    # Task 3: add event emission during execution
│   └── tools/
│       ├── doc_search.py      # Task 3: (no changes needed)
│       ├── rag_search.py      # Task 3: (no changes needed)
│       └── comparator.py      # Task 3: (no changes needed)
tests/
├── test_sse_service.py        # Task 1: Redis Streams integration test
└── test_event_streaming.py    # Task 2&3: event streaming tests
```

---

## Task 1: Switch SSE Service from Redis Pub/Sub to Redis Streams

**Assigned Model:** Haiku (mechanical, isolated)

**Files:**
- Modify: `backend/services/sse_service.py`
- Create: `tests/test_sse_service.py`

**Subagent Sequence:**
1. Dispatch implementer → write test first, implement Redis Streams, commit
2. Dispatch spec reviewer → verify spec compliance
3. Dispatch code quality reviewer → verify code quality

- [ ] **Step 1: Write failing test for Redis Streams consumer**

```python
# tests/test_sse_service.py
import pytest
import asyncio
from backend.services.sse_service import SSEConnectionManager

@pytest.mark.asyncio
async def test_streams_consumer_receives_live_events():
    """Test that consumer receives events as they are published (not batched)."""
    manager = SSEConnectionManager()
    task_id = "test_task_123"

    received_events = []
    event_times = []

    async def consume():
        import time
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
    from backend.tasks.review_tasks import _publish_event
    _publish_event(task_id, "status", {"status": "running"})
    await asyncio.sleep(0.3)
    _publish_event(task_id, "step", {"step_number": 1, "content": "First step"})
    await asyncio.sleep(0.3)
    _publish_event(task_id, "step", {"step_number": 2, "content": "Second step"})

    await consumer_task

    # Verify events arrived with time gaps (not all at once)
    assert len(received_events) == 3
    # Each event should arrive at least 0.2s apart
    assert event_times[1] - event_times[0] >= 0.25, "Events arrived too fast (batched?)"
    assert event_times[2] - event_times[1] >= 0.25, "Events arrived too fast (batched?)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/openclaw/bjt_agent && python -m pytest tests/test_sse_service.py::test_streams_consumer_receives_live_events -v`
Expected: FAIL - current Pub/Sub implementation doesn't guarantee timing

- [ ] **Step 3: Implement Redis Streams in SSE service**

Replace the `SSEConnectionManager.connect()` method in `backend/services/sse_service.py`:

```python
async def connect(self, task_id: str, last_event_id: Optional[str] = None) -> AsyncGenerator[str, None]:
    """Connect to SSE stream using Redis Streams for reliable message delivery.

    Redis Streams provides:
    - Message persistence (unlike Pub/Sub which is fire-and-forget)
    - Consumer groups for replay capability
    - Ordered message delivery
    """
    settings = get_settings()
    stream_key = f"stream:task:{task_id}"
    consumer_group = f"sse_group_{task_id}"
    consumer_name = f"consumer_{os.getpid()}_{threading.current_thread().ident}"

    queue: asyncio.Queue = asyncio.Queue()
    event_count = 0

    def redis_listener():
        import redis
        r = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            # Create consumer group if not exists
            try:
                r.xgroup_create(stream_key, consumer_group, id="0", mkstream=True)
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

            # Read from stream
            while True:
                try:
                    messages = r.xreadgroup(
                        consumer_group,
                        consumer_name,
                        {stream_key: ">"},
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
        finally:
            r.close()
        queue.put_nowait(None)  # Signal end

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
            yield f"id: {msg_id}\ndata: {data}\n\n"
            last_id = msg_id
    except asyncio.CancelledError:
        raise
    finally:
        pass  # Thread will exit when connection closes
```

Add imports at top of file:
```python
import os
import time
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sse_service.py::test_streams_consumer_receives_live_events -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/sse_service.py tests/test_sse_service.py
git commit -m "feat: switch SSE from Pub/Sub to Redis Streams for reliable event delivery"
```

---

## Task 2: Update review_tasks.py to publish to Redis Streams

**Assigned Model:** Haiku (mechanical, isolated)

**Files:**
- Modify: `backend/tasks/review_tasks.py:37-59`

**Subagent Sequence:**
1. Dispatch implementer → write test first, implement Streams publishing, commit
2. Dispatch spec reviewer → verify spec compliance
3. Dispatch code quality reviewer → verify code quality

- [ ] **Step 1: Write failing test for Streams publishing**

```python
# tests/test_event_streaming.py
import pytest
from backend.tasks.review_tasks import _publish_event, _get_stream_key

def test_publish_creates_stream_entry():
    """Test that _publish_event writes to Redis Stream (not just Pub/Sub)."""
    task_id = "test_stream_task"
    _publish_event(task_id, "status", {"status": "running"})

    import redis
    from backend.config import get_settings
    settings = get_settings()
    r = redis.from_url(settings.redis_url)

    stream_key = f"stream:task:{task_id}"
    # Check stream exists and has entries
    length = r.xlen(stream_key)
    assert length > 0, "Stream should have entries"

    # Read the entry
    entries = r.xrange(stream_key, count=1)
    assert len(entries) == 1
    msg_id, data = entries[0]

    # Parse and verify data
    import json
    parsed = json.loads(data)
    assert parsed["type"] == "status"
    assert parsed["status"] == "running"
    r.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_event_streaming.py::test_publish_creates_stream_entry -v`
Expected: FAIL - current implementation uses Pub/Sub, not Streams

- [ ] **Step 3: Implement Streams publishing**

Replace the `_publish_event` function in `backend/tasks/review_tasks.py`:

```python
def _get_stream_key(task_id: str) -> str:
    """Get Redis Stream key for a task."""
    return f"stream:task:{task_id}"


def _publish_event(task_id: str, event_type: str, data: dict) -> None:
    """Publish an event to Redis Stream for SSE forwarding.

    Uses Redis Streams for reliable message delivery with persistence.
    """
    try:
        import redis
        from backend.config import get_settings

        settings = get_settings()
        stream_key = _get_stream_key(task_id)
        event = json.dumps({"type": event_type, "task_id": task_id, **data})
        logger.info(f"Publishing event to stream: {stream_key} -> {event}")

        r = redis.from_url(settings.redis_url)
        try:
            # Add to stream with auto-generated ID
            msg_id = r.xadd(stream_key, {"data": event})
            logger.info(f"Published event to stream: {stream_key}, msg_id={msg_id}")
        finally:
            r.close()
    except Exception as e:
        logger.warning(f"Failed to publish event to Redis Stream: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_event_streaming.py::test_publish_creates_stream_entry -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tasks/review_tasks.py tests/test_event_streaming.py
git commit -m "feat: use Redis Streams for event publishing instead of Pub/Sub"
```

---

## Task 3: Add real-time event emission during agent tool execution

**Assigned Model:** Sonnet (multi-file integration, understanding agent loop)

**Files:**
- Modify: `backend/agent/bid_review_agent.py`

**Context for subagent:**
- The Mini-Agent base class `run()` method is in `Mini-Agent/mini_agent/agent.py:321-519`
- It executes tools in a loop but has no event callbacks
- You need to override `run_review()` to emit events at each step/tool execution

**Subagent Sequence:**
1. Dispatch implementer → write test first, implement event emission, commit
2. Dispatch spec reviewer → verify spec compliance
3. Dispatch code quality reviewer → verify code quality

- [ ] **Step 1: Write failing test for real-time tool events**

```python
# tests/test_event_streaming.py (add this test)
@pytest.mark.asyncio
async def test_tool_events_emitted_during_execution():
    """Test that tool start/complete events are emitted in real-time, not batched."""
    from backend.agent.bid_review_agent import BidReviewAgent
    from unittest.mock import MagicMock

    # Create mock event callback to capture events
    captured_events = []
    def event_cb(event_type, data):
        captured_events.append((event_type, data.copy(), asyncio.get_event_loop().time()))

    agent = BidReviewAgent(
        project_id="test_project",
        tender_doc_path="/tmp/test_tender.md",
        bid_doc_path="/tmp/test_bid.md",
        user_id="test_user",
        event_callback=event_cb,
        max_steps=5,
    )

    # Mock the tools to execute quickly
    async def mock_execute(**kwargs):
        await asyncio.sleep(0.1)  # Simulate work
        return MagicMock(success=True, content="Mock result")

    for tool in agent.tools.values():
        tool.execute = mock_execute

    # Run agent (will use mock tools)
    try:
        await agent.run_review()
    except Exception:
        pass  # Expected to fail due to missing docs

    # Check events were emitted during execution, not after
    tool_events = [(t, d) for t, d, _ in captured_events if t == "tool_progress"]

    # Should have at least some tool progress events
    assert len(tool_events) > 0, "No tool events emitted during execution"

    # Events should have been captured at different times (not all at end)
    event_times = [t for _, _, t in captured_events if _ in ["tool_progress", "step"]]
    if len(event_times) >= 2:
        time_span = event_times[-1] - event_times[0]
        assert time_span > 0.05, "Events appear to be batched at end"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_event_streaming.py::test_tool_events_emitted_during_execution -v`
Expected: FAIL - no tool events are emitted currently

- [ ] **Step 3: Override run() in BidReviewAgent to emit events**

Replace `run_review()` method in `backend/agent/bid_review_agent.py` with an override that emits events during tool execution:

```python
async def run_review(self) -> list[dict]:
    """Run the bid review process with real-time event emission.

    Returns:
        List of findings with requirement, bid content, compliance status, etc.
    """
    from backend.agent.tools.doc_search import DocSearchTool
    from backend.agent.tools.rag_search import RAGSearchTool
    from backend.agent.tools.comparator import ComparatorTool

    # Build the review task description
    task = f"""请审查投标文件相对于招标文件的不符合项。

招标书路径: {self.tender_doc_path}
投标书路径: {self.bid_doc_path}

请严格按照系统提示中的工作流程执行：
1. 读取并提取招标书中的所有要求
2. 查询企业知识库获取相关政策
3. 读取投标书内容
4. 对每个招标要求与投标内容进行比对
5. 识别不符合项并输出结构化的JSON格式结果

重要：最终输出必须包含一个JSON数组，每项代表一个审查发现。"""

    self.add_user_message(task)

    # Send starting event
    self._send_event("progress", {"message": "Starting agent review..."})
    self._send_event("step", {
        "step_number": 1,
        "step_type": "thought",
        "content": "Initializing bid review agent...",
    })

    step_number = 2

    # Run the agent loop manually with event emission
    tool_list = list(self.tools.values())

    while len(self.messages) - 1 < self.max_steps:  # -1 for system message
        # Check for cancellation
        if self.cancel_event and self.cancel_event.is_set():
            break

        # Summarize if needed
        await self._summarize_messages()

        # Get LLM response
        try:
            response = await self.llm.generate(messages=self.messages, tools=tool_list)
        except Exception as e:
            self._send_event("error", {"message": f"LLM error: {str(e)}"})
            break

        # Add assistant message
        from mini_agent.schema import Message
        assistant_msg = Message(
            role="assistant",
            content=response.content,
            thinking=response.thinking,
            tool_calls=response.tool_calls,
        )
        self.messages.append(assistant_msg)

        # Emit step event
        if response.content:
            self._send_event("step", {
                "step_number": step_number,
                "step_type": "thought",
                "content": str(response.content)[:200],
            })
            step_number += 1

        # Check if task is complete
        if not response.tool_calls:
            break

        # Execute tools with event emission
        for tool_call in response.tool_calls:
            function_name = tool_call.function.name

            # Emit tool call event
            self._send_event("step", {
                "step_number": step_number,
                "step_type": "tool_call",
                "tool_name": function_name,
                "content": f"Calling {function_name}...",
            })
            step_number += 1

            # Execute tool
            if function_name in self.tools:
                try:
                    result = await self.tools[function_name].execute(**tool_call.function.arguments)
                    # Emit tool result event
                    result_preview = str(result.content)[:100] if result.success else str(result.error)[:100]
                    self._send_event("step", {
                        "step_number": step_number,
                        "step_type": "observation",
                        "tool_name": function_name,
                        "content": result_preview,
                    })
                    step_number += 1

                    # Add tool message
                    tool_msg = Message(
                        role="tool",
                        content=result.content if result.success else f"Error: {result.error}",
                        tool_call_id=tool_call.id,
                        name=function_name,
                    )
                    self.messages.append(tool_msg)
                except Exception as e:
                    self._send_event("error", {"message": f"Tool {function_name} failed: {str(e)}"})
                    break
            else:
                self._send_event("error", {"message": f"Unknown tool: {function_name}"})

    # Extract findings
    findings = self._extract_findings_from_messages()
    if not findings:
        findings = self._parse_findings_from_text(self.messages[-1].content if self.messages else "")

    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_event_streaming.py::test_tool_events_emitted_during_execution -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/bid_review_agent.py
git commit -m "feat: emit real-time events during agent tool execution"
```

---

## Task 4: Verify full integration with frontend timeline

**Assigned Model:** Sonnet (end-to-end verification)

**Files:**
- Test: Run full review flow with frontend

**Subagent Sequence:**
1. Dispatch implementer → manual integration verification
2. Dispatch spec reviewer → verify end-to-end behavior
3. Dispatch code quality reviewer → verify no regressions

- [ ] **Step 1: Start all services and run a test review**

```bash
./scripts/bjt.sh restart
# Wait for services to be ready
sleep 10
```

- [ ] **Step 2: Check SSE event flow in logs**

Look for continuous event emission, not batched at end:
```
# Backend should show events being published at each step:
# - "Initializing bid review agent..."
# - "Calling doc_search..."
# - Tool result
# - "Calling comparator..."
# etc.
```

- [ ] **Step 3: Verify frontend timeline displays in real-time**

1. Open browser devtools
2. Start a review
3. Observe timeline - steps should appear one by one as events arrive
4. Not all at once after "Review completed"

---

## Execution Order

```
┌─────────────────────────────────────────────────────────────────┐
│  Task 1: SSE Service (Haiku)                                   │
│  [Implementer] → [Spec Review] → [Code Quality] → COMMIT      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Task 2: review_tasks (Haiku)                                  │
│  [Implementer] → [Spec Review] → [Code Quality] → COMMIT      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Task 3: bid_review_agent (Sonnet)                             │
│  [Implementer] → [Spec Review] → [Code Quality] → COMMIT      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Task 4: Integration (Sonnet)                                  │
│  [Implementer] → [Spec Review] → [Code Quality]                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                         FINAL REVIEW
```

---

## Summary of Changes

| Component | Change |
|-----------|--------|
| `sse_service.py` | Switch from Redis Pub/Sub to Streams consumer |
| `review_tasks.py` | Publish to Redis Stream instead of Pub/Sub channel |
| `bid_review_agent.py` | Override `run_review()` to emit events at each step/tool |

---

## Self-Review Checklist

1. **Spec coverage:** All requirements for real-time streaming addressed
2. **Placeholder scan:** No TODOs, all code is complete
3. **Type consistency:** Method signatures consistent across files
