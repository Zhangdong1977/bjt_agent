# Agent Step 序号修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 step_number 重复和跳跃问题，采用简化序号方案：每个 LLM 响应作为一个 step

**Architecture:** 重写 `_record_agent_step` 函数，使其按 LLM 响应（而非内部 tool_call）分配序号。同时更新 SSE 事件格式和前端展示逻辑以保持一致。

**Tech Stack:** Python (FastAPI/Celery), Vue3, PostgreSQL

---

## 文件变更概览

| 文件 | 变更 |
|------|------|
| `backend/tasks/review_tasks.py` | 重写 `_record_agent_step` |
| `backend/agent/bid_review_agent.py` | 调整 SSE 事件格式 |
| `backend/models/agent_step.py` | 更新字段注释 |
| `frontend/src/components/ReviewTimeline.vue` | 调整展示逻辑 |

---

## Task 1: 修改 `_record_agent_step` 函数

**文件:**
- Modify: `backend/tasks/review_tasks.py:193-264`

- [ ] **Step 1: 读取当前实现**

读取 `backend/tasks/review_tasks.py` 第 193-264 行，理解当前 `_record_agent_step` 函数的实现。

- [ ] **Step 2: 编写新实现**

替换 `_record_agent_step` 函数为新实现：

```python
def _record_agent_step(db, task_id: str, step_number: int, msg, tool_results: dict | None = None) -> int:
    """Record agent steps from message history.

    每个 LLM 响应（assistant message）作为一个 step。
    Tool_calls 内嵌在 step 中，不独占 step_number。
    """
    if msg.role != "assistant":
        return step_number

    # 收集 tool_calls 数据
    tool_calls_data = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            func_name = tc.function.name
            tool_calls_data.append({
                "name": func_name,
                "arguments": tc.function.arguments,
            })

    # 收集 tool_results 数据
    tool_results_data = []
    if tool_calls_data and tool_results:
        for tc_data in tool_calls_data:
            func_name = tc_data["name"]
            if func_name in tool_results:
                tool_results_data.append({
                    "name": func_name,
                    "result": tool_results[func_name]
                })

    # 确定 step_type
    if msg.content and tool_calls_data:
        step_type = "observation"
    elif msg.content:
        step_type = "thought"
    elif tool_calls_data:
        step_type = "tool_call"
    else:
        return step_number  # 跳过空消息

    # 记录一条 AgentStep
    step = AgentStep(
        task_id=task_id,
        step_number=step_number,
        step_type=step_type,
        content=str(msg.content)[:500] if msg.content else None,
        tool_name=None,
        tool_args={"tool_calls": tool_calls_data} if tool_calls_data else None,
        tool_result={"tool_results": tool_results_data} if tool_results_data else None,
    )
    db.add(step)

    # 返回下一个 step_number
    return step_number + 1
```

- [ ] **Step 3: 运行测试验证**

```bash
cd /home/openclaw/bjt_agent/backend
python3 -c "from tasks.review_tasks import _record_agent_step; print('_record_agent_step imported successfully')"
```

预期: 导入成功，无错误

- [ ] **Step 4: 提交代码**

```bash
git add backend/tasks/review_tasks.py
git commit -m "fix: rewrite _record_agent_step for simplified step numbering

Each LLM response now maps to one step_number.
Tool calls are embedded within the step, not assigned separate numbers.
Fixes duplicate and skipped step numbers in historical timeline.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: 更新 AgentStep 模型注释

**文件:**
- Modify: `backend/models/agent_step.py`

- [ ] **Step 1: 读取当前模型**

读取 `backend/models/agent_step.py`

- [ ] **Step 2: 更新字段注释**

在 `AgentStep` 类的 docstring 中添加说明：

```python
class AgentStep(Base):
    """Agent step model - stores agent execution steps for timeline display.

    Step numbering follows Mini-Agent pattern:
    - Each LLM response (assistant message) = one step_number
    - tool_calls are embedded within the step, not assigned separate numbers
    - Multiple tool_calls in one LLM response share the same step_number
    """
```

- [ ] **Step 3: 提交代码**

```bash
git add backend/models/agent_step.py
git commit -m "docs: add step numbering pattern comment to AgentStep model

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: 更新 SSE 事件格式 (BidReviewAgent)

**文件:**
- Modify: `backend/agent/bid_review_agent.py:154-204`

- [ ] **Step 1: 读取当前 SSE 实现**

读取 `backend/agent/bid_review_agent.py` 第 90-231 行，理解 `run_review` 和 `_send_event` 逻辑

- [ ] **Step 2: 修改 SSE 事件发送逻辑**

修改 `run_review` 方法中的 SSE 事件发送部分，使每个 LLM 响应发送一个事件：

```python
# 在 run_review 方法中，替换原有的多事件发送逻辑

# 发送 LLM 响应事件（包含 content + tool_calls + tool_results）
content_preview = response.content[:200] if response.content else ""
step_type = "observation" if response.content else ("thought" if not response.tool_calls else "tool_call")

event_data = {
    "step_number": step_counter,
    "step_type": step_type,
    "tool_name": None,
    "content": content_preview,
    "tool_calls": [
        {"name": tc.function.name, "arguments": tc.function.arguments}
        for tc in response.tool_calls
    ] if response.tool_calls else [],
    "tool_results": [
        {"name": name, "result": result}
        for name, result in self._tool_results.items()
    ] if self._tool_results else [],
}
self._send_event("step", event_data)
step_counter += 1
```

- [ ] **Step 3: 移除原有的多事件发送逻辑**

删除原有的：
- `tool_call` 事件发送（第 186-194 行）
- `tool_result` 事件发送（第 196-204 行）

- [ ] **Step 4: 提交代码**

```bash
git add backend/agent/bid_review_agent.py
git commit -m "fix: align SSE event format with simplified step numbering

SSE events now send one event per LLM response with embedded tool_calls.
Matches database storage pattern.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: 更新前端展示逻辑 (ReviewTimeline)

**文件:**
- Modify: `frontend/src/components/ReviewTimeline.vue`

- [ ] **Step 1: 读取当前实现**

读取 `frontend/src/components/ReviewTimeline.vue` 理解当前 timeline 展示逻辑

- [ ] **Step 2: 理解新的数据格式**

新的 API 响应格式：
```json
{
  "step_number": 1,
  "step_type": "observation",
  "content": "...",
  "tool_args": {"tool_calls": [...]},
  "tool_result": {"tool_results": [...]}
}
```

- [ ] **Step 3: 修改 TimelineStep 接口**

更新 `TimelineStep` 接口以匹配新格式：
```typescript
interface TimelineStep {
  step_number: number
  step_type: string
  content: string
  timestamp: Date
  tool_args?: {
    tool_calls?: Array<{name: string, arguments: Record<string, any>}>
  }
  tool_result?: {
    tool_results?: Array<{name: string, result: any}>
  }
}
```

- [ ] **Step 4: 修改展示模板**

调整模板以展示内嵌的 tool_calls：
- 每个 step_number 为一个"节"
- 节内展示 content
- 节内展开显示所有 tool_calls（从 tool_args.tool_calls 获取）
- Tool_results 与 tool_calls 配对展示（从 tool_result.tool_results 获取）

- [ ] **Step 5: 提交代码**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "fix: update ReviewTimeline for simplified step numbering

Timeline now displays one card per LLM response.
Tool calls are expanded within each card.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: 端到端测试验证

**测试环境:** 项目 "测试-26"，账号 zhangdong

- [ ] **Step 1: 启动/检查后端服务**

```bash
cd /home/openclaw/bjt_agent
./scripts/bjt.sh status
```

确保服务运行正常。

- [ ] **Step 2: 登录并进入项目**

在浏览器中：
1. 登录账号 zhangdong (密码 7745duck)
2. 进入项目 "测试-26"

- [ ] **Step 3: 运行新审查任务**

1. 点击"重新审查"按钮
2. 等待审查完成

- [ ] **Step 4: 验证数据库序号**

在新审查完成后，运行验证 SQL：

```bash
cd /home/openclaw/bjt_agent/backend
python3 << 'EOF'
import psycopg2
conn = psycopg2.connect(
    host="183.66.37.186", port=7004, database="bjt_agent",
    user="ssirs_user", password="y6+YufO6njlzxXiaNj6rA4xZaT3ofwT6"
)
cur = conn.cursor()

# 获取最新完成的任务
cur.execute("""
    SELECT id FROM review_tasks
    WHERE status = 'completed'
    ORDER BY completed_at DESC LIMIT 1
""")
task_id = cur.fetchone()[0]

# 检查 step_number 重复
cur.execute("""
    SELECT step_number, COUNT(*) as cnt
    FROM agent_steps
    WHERE task_id = %s
    GROUP BY step_number
    HAVING COUNT(*) > 1
""", (task_id,))
dups = cur.fetchall()

# 检查 step_number 连续性
cur.execute("""
    SELECT step_number FROM agent_steps
    WHERE task_id = %s
    ORDER BY step_number
""", (task_id,))
steps = cur.fetchall()
step_nums = [s[0] for s in steps]
expected = list(range(1, len(step_nums) + 1))
missing = set(expected) - set(step_nums)

print(f"Task ID: {task_id}")
print(f"Total steps: {len(step_nums)}")
print(f"Duplicate step_numbers: {len(dups)}")
print(f"Missing step_numbers: {sorted(missing) if missing else 'None'}")
print(f"First 10 step_numbers: {step_nums[:10]}")

cur.close()
conn.close()
EOF
```

预期结果：
- Duplicate step_numbers: 0
- Missing step_numbers: None
- 序号连续无跳跃

- [ ] **Step 5: 验证页面展示**

1. 在浏览器中选择历史任务
2. 点击"加载历史时间线"
3. 验证页面显示的节号连续无重复

- [ ] **Step 6: 最终提交**

如果测试通过，提交所有变更：

```bash
git add -A
git commit -m "fix: complete agent step numbering simplification

All changes for simplified step numbering:
- Backend: _record_agent_step rewritten
- SSE events: aligned with simplified format
- Frontend: ReviewTimeline updated for new format
- Model: documentation improved

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 验证检查清单

- [ ] 数据库无重复 step_number
- [ ] 数据库无跳跃 step_number
- [ ] 页面展示序号与数据库一致
- [ ] 新审查任务正常运行
- [ ] 历史时间线加载正常

---

## 风险与回滚

**风险:** 低 - 核心逻辑简化，移除复杂状态跟踪

**回滚:** `git revert <commit>` 可快速回滚所有变更