# Timeline Step Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make historical timeline view match live SSE view by aligning step_number assignment.

**Architecture:** When an assistant response has tool_calls, both the observation (assistant content) and each tool_call should share the same step_number in the database, matching the SSE behavior. The frontend groups by step_number for display.

**Tech Stack:** Python (backend), TypeScript/Vue (frontend)

---

## Problem Analysis

**Live SSE View:**
```
step_number: 2, step_type: 'observation'  → 第 2 节
step_number: 2, step_type: 'tool_call'     → 第 2 节 (different type)
step_number: 3, step_type: 'tool_call'    → 第 3 节
```

**Current Historical DB View:**
```
DB only stores tool_call steps with sequential step_number:
step_number: 1, step_type: 'tool_call'   → 第 1 节
step_number: 2, step_type: 'tool_call'    → 第 2 节
step_number: 3, step_type: 'tool_call'    → 第 3 节
```

**Root Cause:** `_record_agent_step` doesn't record observation steps, only tool_call steps with sequential numbering.

---

## File Structure

- Modify: `backend/tasks/review_tasks.py:193-231` — `_record_agent_step` function
- Modify: `backend/agent/bid_review_agent.py:141-167` — SSE event sending logic
- Modify: `frontend/src/stores/project.ts:336-346` — `loadHistoricalSteps` function

---

## Tasks

### Task 1: Modify _record_agent_step to Record Observation Steps

**Files:**
- Modify: `backend/tasks/review_tasks.py:193-231`

- [ ] **Step 1: Read current _record_agent_step implementation**

```python
def _record_agent_step(db, task_id: str, step_number: int, msg) -> int:
    """Record an agent step from message history to database only.

    Note: SSE events are already sent by BidReviewAgent.run_review() during execution.
    This function only persists steps to the database after the fact.

    Returns the next step number.
    """
    if msg.tool_calls:
        for tc in msg.tool_calls:
            step = AgentStep(
                task_id=task_id,
                step_number=step_number,
                step_type="tool_call",
                content=f"Called {tc.function.name}",
                tool_name=tc.function.name,
                tool_args=tc.function.arguments,
            )
            db.add(step)
            step_number += 1
    elif msg.content:
        step = AgentStep(
            task_id=task_id,
            step_number=step_number,
            step_type="thought",
            content=str(msg.content)[:500],
            tool_name=None,
        )
        db.add(step)
        step_number += 1
    return step_number
```

- [ ] **Step 2: Replace with new implementation that records observation with same step_number as tool_calls**

```python
def _record_agent_step(db, task_id: str, step_number: int, msg) -> int:
    """Record agent steps from message history to database.

    Records both observation (assistant content) and tool_call steps.
    When tool_calls exist, observation and first tool_call share the same step_number
    to match the SSE event pattern.

    Note: SSE events are already sent by BidReviewAgent.run_review() during execution.
    This function only persists steps to the database after the fact.

    Returns the next step number.
    """
    if msg.tool_calls:
        # Record observation step with the step_number (same as SSE pattern)
        if msg.content:
            observation = AgentStep(
                task_id=task_id,
                step_number=step_number,
                step_type="observation",
                content=str(msg.content)[:500],
                tool_name=None,
            )
            db.add(observation)

        # Record each tool_call with sequential step_number AFTER the observation
        first_tool_step_number = step_number + 1 if msg.content else step_number
        for tc in msg.tool_calls:
            step = AgentStep(
                task_id=task_id,
                step_number=first_tool_step_number,
                step_type="tool_call",
                content=f"Called {tc.function.name}",
                tool_name=tc.function.name,
                tool_args=tc.function.arguments,
            )
            db.add(step)
            first_tool_step_number += 1

        # Return the next available step_number (after all tool_calls)
        return first_tool_step_number
    elif msg.content:
        # No tool_calls, record as thought step
        step = AgentStep(
            task_id=task_id,
            step_number=step_number,
            step_type="thought",
            content=str(msg.content)[:500],
            tool_name=None,
        )
        db.add(step)
        step_number += 1
        return step_number

    return step_number
```

- [ ] **Step 3: Run syntax check**

Run: `cd /home/openclaw/bjt_agent && python -m py_compile backend/tasks/review_tasks.py`
Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
cd /home/openclaw/bjt_agent
git add backend/tasks/review_tasks.py
git commit -m "fix: record observation steps with same step_number as tool_calls in DB"
```

---

### Task 2: Update Frontend loadHistoricalSteps to Handle Grouped Display

**Files:**
- Modify: `frontend/src/stores/project.ts:336-346`

- [ ] **Step 1: Read current loadHistoricalSteps function**

```typescript
async function loadHistoricalSteps(taskId: string) {
  if (!currentProject.value) return
  const steps = await reviewApi.getSteps(currentProject.value.id, taskId)
  agentSteps.value = steps.map(s => ({
    step_number: s.step_number,
    step_type: s.step_type,
    tool_name: s.tool_name || undefined,
    content: s.content,
    timestamp: new Date(s.created_at),
  }))
}
```

- [ ] **Step 2: Replace with function that properly handles observation + tool_call grouping**

```typescript
async function loadHistoricalSteps(taskId: string) {
  if (!currentProject.value) return
  const steps = await reviewApi.getSteps(currentProject.value.id, taskId)

  // Group steps by step_number, then interleave observation before tool_calls
  const groupedSteps: typeof agentSteps.value = []

  // Group by step_number
  const byNumber = new Map<number, typeof steps>()
  for (const s of steps) {
    const existing = byNumber.get(s.step_number)
    if (existing) {
      existing.push(s)
    } else {
      byNumber.set(s.step_number, [s])
    }
  }

  // For each step_number, sort: observation/thought first, then tool_call
  const sortOrder = { observation: 0, thought: 1, tool_call: 2 }
  const sortedNumbers = Array.from(byNumber.keys()).sort((a, b) => a - b)

  for (const num of sortedNumbers) {
    const group = byNumber.get(num)!
    // Sort within group: observation/thought before tool_call
    group.sort((a, b) => (sortOrder[a.step_type] ?? 99) - (sortOrder[b.step_type] ?? 99))

    for (const s of group) {
      groupedSteps.push({
        step_number: s.step_number,
        step_type: s.step_type,
        tool_name: s.tool_name || undefined,
        content: s.content,
        timestamp: new Date(s.created_at),
      })
    }
  }

  agentSteps.value = groupedSteps
}
```

- [ ] **Step 3: TypeScript check**

Run: `cd /home/openclaw/bjt_agent/frontend && npx tsc --noEmit 2>&1 | head -30`
Expected: No type errors related to our changes

- [ ] **Step 4: Commit**

```bash
cd /home/openclaw/bjt_agent
git add frontend/src/stores/project.ts
git commit -m "fix: properly group observation and tool_call steps by step_number in historical view"
```

---

### Task 3: Verify Live and Historical Alignment

**Files:**
- Test with actual rerun review to verify both views show same step numbers

- [ ] **Step 1: Start the application and navigate to a project**

- [ ] **Step 2: Start a new review and observe the live timeline step numbers**

Expected: Steps should be numbered 1, 1, 2, 2, 3, 3... (observation shares number with its tool_calls)

- [ ] **Step 3: After review completes, refresh and load historical view**

Expected: Same step numbering pattern as live view

- [ ] **Step 4: Commit verification**

```bash
cd /home/openclaw/bjt_agent
git add -A
git commit -m "verify: timeline step alignment between live and historical views"
```

---

## Self-Review Checklist

- [ ] `_record_agent_step` records observation when tool_calls exist
- [ ] Observation and first tool_call share the same step_number
- [ ] Frontend `loadHistoricalSteps` properly groups and sorts by step_number
- [ ] Both live and historical views show consistent step numbering
- [ ] No TypeScript errors introduced
- [ ] No Python syntax errors introduced
