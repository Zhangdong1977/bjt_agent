# Timeline Step Number Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix duplicate event emission causing step number gaps and missing content in timeline display.

**Architecture:** The root cause is double emission of SSE events: `BidReviewAgent.run_review()` sends events during execution, then `_run_agent_review()` sends the same events again from message history. The fix is to separate concerns - `BidReviewAgent` handles SSE emission, while `_run_agent_review()` only persists steps to the database without re-emitting events.

**Tech Stack:** Python (FastAPI/Celery), Vue3, SSE, Redis Streams

---

## File Structure

```
backend/
├── agent/bid_review_agent.py     # REMOVE event emission from run_review()
├── tasks/review_tasks.py          # REMOVE _record_agent_step() call from _run_agent_review()
├── models/agent_step.py           # (read-only) AgentStep model
└── services/sse_service.py        # (read-only) SSE streaming
```

---

## Root Cause Analysis

| Location | Behavior | Problem |
|----------|----------|---------|
| `BidReviewAgent.run_review()` L93-211 | Sends SSE events with step_number 1, 2, 3... | These are real-time events to frontend |
| `_run_agent_review()` L311-325 | Calls `agent.get_history()` and `_record_agent_step()` | Re-sends the same events with different step_numbers |

**Evidence from test output:**
- Step 1: "Initializing bid review agent..." (correct)
- Step 3, 5, 7...: "Calling search_tender_doc..." (odd numbers from run_review)
- Step 2, 4, 6...: "Called search_tender_doc" (even numbers from _record_agent_step in review_tasks.py)

---

## Task 1: Remove Event Emission from BidReviewAgent.run_review()

**Files:**
- Modify: `backend/agent/bid_review_agent.py:93-211`

**Changes:**
1. Remove all `self._send_event()` calls from `run_review()` method
2. The method should only execute the agent logic and update internal state
3. Event emission will be handled exclusively by `_run_agent_review()` in review_tasks.py

**Why:** `BidReviewAgent` should only handle agent execution logic, not SSE concerns. SSE events should be emitted from one place only.

- [ ] **Step 1: Read the current run_review() method**

Locate all `self._send_event()` calls in `run_review()` (lines 117-122, 154-158, 170-176, 183-189, 201, 204).

- [ ] **Step 2: Remove SSE event emission from run_review()**

Replace the entire method body to remove all `self._send_event()` calls. The method should add messages to `self.messages` and return findings but not emit any events.

```python
async def run_review(self) -> list[dict]:
    """Run the bid review process.

    Returns:
        List of findings with requirement, bid content, compliance status, etc.
    """
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

    # Run the agent loop
    tool_list = list(self.tools.values())

    while len(self.messages) - 1 < self.max_steps:
        if self.cancel_event and self.cancel_event.is_set():
            break

        await self._summarize_messages()

        try:
            response = await self.llm.generate(messages=self.messages, tools=tool_list)
        except Exception as e:
            break

        assistant_msg = Message(
            role="assistant",
            content=response.content,
            thinking=response.thinking,
            tool_calls=response.tool_calls,
        )
        self.messages.append(assistant_msg)

        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            function_name = tool_call.function.name

            if function_name in self.tools:
                try:
                    result = await self.tools[function_name].execute(**tool_call.function.arguments)
                    tool_msg = Message(
                        role="tool",
                        content=result.content if result.success else f"Error: {result.error}",
                        tool_call_id=tool_call.id,
                        name=function_name,
                    )
                    self.messages.append(tool_msg)
                except Exception as e:
                    break
            else:
                break

    findings = self._extract_findings_from_messages()
    if not findings:
        findings = self._parse_findings_from_text(self.messages[-1].content if self.messages else "")

    return findings
```

- [ ] **Step 3: Run type check**

```bash
cd /home/openclaw/bjt_agent && python -c "from backend.agent.bid_review_agent import BidReviewAgent; print('Import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/agent/bid_review_agent.py
git commit -m "refactor(agent): remove SSE event emission from BidReviewAgent.run_review()

Event emission is now handled exclusively by _run_agent_review() in
review_tasks.py to prevent double-emission of step events"
```

---

## Task 2: Consolidate Event Emission in _run_agent_review()

**Files:**
- Modify: `backend/tasks/review_tasks.py:193-231` (_record_agent_step function)
- Modify: `backend/tasks/review_tasks.py:269-325` (_run_agent_review function)

**Changes:**
1. Modify `_record_agent_step()` to handle BOTH database persistence AND SSE event emission (since we removed it from run_review)
2. Remove the separate event emission in `BidReviewAgent` - it no longer exists after Task 1

**Why:** `_record_agent_step()` already sends events via `event_cb`. After Task 1, it will be the sole source of truth for step events.

- [ ] **Step 1: Read the _record_agent_step and _run_agent_review functions**

Locate lines 193-231 and 269-325 in `backend/tasks/review_tasks.py`.

- [ ] **Step 2: Update _record_agent_step to send SSE events**

The function already sends events via `event_cb()`. After Task 1, it will be the only place sending events. No structural changes needed to this function - just verify the event_cb calls are correct.

- [ ] **Step 3: Verify _run_agent_review calls _record_agent_step correctly**

In `_run_agent_review()`, the loop at lines 316-318:
```python
for msg in agent.get_history():
    if msg.role == "assistant":
        step_number = _record_agent_step(db, task_id, step_number, msg, event_cb)
```

This is correct. The `event_cb` callback is `_publish_event` which sends SSE events.

- [ ] **Step 4: Run type check**

```bash
cd /home/openclaw/bjt_agent && python -c "from backend.tasks.review_tasks import run_review, _run_agent_review; print('Import OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/tasks/review_tasks.py
git commit -m "refactor(tasks): _run_agent_review now solely responsible for event emission

_record_agent_step() sends both DB records and SSE events for each step.
BidReviewAgent.run_review() no longer emits events (removed in previous commit)."
```

---

## Task 3: Add Initial Step Event in _run_agent_review()

**Files:**
- Modify: `backend/tasks/review_tasks.py:291-307`

**Changes:**
After Task 1, `BidReviewAgent.run_review()` no longer sends the "Initializing bid review agent..." event (step_number=1). We need to send it in `_run_agent_review()` before calling `agent.run_review()`.

- [ ] **Step 1: Add initial step event before calling agent.run_review()**

In `_run_agent_review()`, before `result = await agent.run_review()`, add:

```python
# Send initial step event
event_cb("step", {
    "step_number": 1,
    "step_type": "thought",
    "content": "Initializing bid review agent...",
})
step_number = 2
```

- [ ] **Step 2: Run type check**

```bash
cd /home/openclaw/bjt_agent && python -c "from backend.tasks.review_tasks import run_review; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/tasks/review_tasks.py
git commit -m "feat(tasks): send initial step event before agent.run_review()

Previously this was sent by BidReviewAgent.run_review() which we removed.
Now _run_agent_review() sends it before calling the agent."
```

---

## Task 4: Verify Timeline Step Numbering

**Testing:** Manual VNC test per the test case in docs/questiontobefixed.md

**Expected Results:**
1. Timeline node numbers start at 1 and increment consecutively (1, 2, 3, 4...)
2. No duplicate content entries
3. No gaps in numbering

**Verification:**
- [ ] **Step 1: Run the full test case**

Follow the test steps in `docs/questiontobefixed.md`:
1. Use VNC remote desktop, open browser, login: zhangdong / 7745duck
2. Create test project
3. Upload tender and bid documents from testdocuments
4. Wait for parsing, click "Start Analysis"
5. View timeline content

**Expected timeline should show:**
- Step 1: 💭 "Initializing bid review agent..."
- Step 2: 🔧 "Calling search_tender_doc..." (first call)
- Step 3: 👁 Observation result
- Step 4: 🔧 "Calling search_tender_doc..." (second call)
- ...continuing consecutively...

- [ ] **Step 2: Commit final verification**

```bash
git add -A
git commit -m "fix: resolve timeline step number gaps and duplicate content

Root cause: BidReviewAgent.run_review() and _run_agent_review() were
both emitting SSE events, causing double-emission with conflicting
step_numbers.

Solution:
1. Removed SSE event emission from BidReviewAgent.run_review()
2. _run_agent_review() now solely responsible for all event emission
3. Initial step event (step 1) sent before agent.run_review()

Timeline now shows consecutive step numbers without gaps or duplicates."
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** All requirements from `docs/questiontobefixed.md` are addressed
- [ ] **Placeholder scan:** No TBD/TODO/placeholder content in tasks
- [ ] **Type consistency:** Method signatures match across tasks (no `clearLayers()` vs `clearFullLayers()`)
- [ ] **File paths:** All exact, no vague references
- [ ] **Commands:** All commands are executable with expected output
