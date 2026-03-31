# Agent Step 序号简化设计

## 概述

修复 `_record_agent_step` 函数中 step_number 重复和跳跃的问题，采用简化序号方案：每个 LLM 响应作为一个 step，tool_calls 不独占 step_number。

## 问题描述

当前实现存在以下问题：
- Observation 和第一个 tool_call 被分配相同 step_number（重复）
- Tool_result 之后 step_counter 未正确递增（跳跃）
- 数据库 215 条记录只有 195 个唯一 step_number

## 设计方案

### 序号分配规则

**核心原则**: 每个 LLM 响应（assistant message）= 一个 step_number

```
LLM 响应 1 (content + tool_calls x 2):
  → step_number = 1
  → 1 条 AgentStep，记录 content + 2 个 tool_calls

LLM 响应 2 (content only):
  → step_number = 2
  → 1 条 AgentStep，记录 content

LLM 响应 3 (tool_calls x 1):
  → step_number = 3
  → 1 条 AgentStep，记录 1 个 tool_call
```

### 数据模型

`AgentStep` 模型保持不变：
- `step_number`: LLM 响应序号（同一 LLM 响应内的所有 tool_calls 共享）
- `step_type`: observation | thought | tool_call | tool_result
- `content`: LLM 响应内容（仅当 step_type 为 observation/thought 时）
- `tool_calls`: 工具调用列表（JSONB，包含该 LLM 响应的所有工具调用）
- `tool_results`: 工具结果列表（JSONB，与 tool_calls 配对）

### API 响应格式

GET `/api/projects/{project_id}/review/tasks/{task_id}/steps` 返回：

```json
[
  {
    "step_number": 1,
    "step_type": "observation",
    "content": "我将按照工作流程执行标书审查任务...",
    "tool_calls": [
      {"name": "search_tender_doc", "arguments": {...}},
      {"name": "rag_search", "arguments": {...}}
    ],
    "tool_results": [
      {"name": "search_tender_doc", "result": {...}},
      {"name": "rag_search", "result": {...}}
    ],
    "timestamp": "2026-03-31T22:54:55Z"
  }
]
```

## 实施步骤

### 1. 修改 `_record_agent_step` 函数

文件: `backend/tasks/review_tasks.py`

```python
def _record_agent_step(db, task_id: str, step_number: int, msg, tool_results: dict | None = None) -> int:
    """Record agent steps from message history.

    每个 LLM 响应（assistant message）作为一个 step。
    Tool_calls 内嵌在 step 中，不独占 step_number。
    """
    if msg.role != "assistant":
        return step_number

    tool_calls_data = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            func_name = tc.function.name
            tool_call_entry = {
                "name": func_name,
                "arguments": tc.function.arguments,
            }
            tool_calls_data.append(tool_call_entry)

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
        content=msg.content if msg.content else None,
        tool_name=None,
        tool_args={"tool_calls": tool_calls_data} if tool_calls_data else None,
        tool_result={"tool_results": tool_results_data} if tool_results_data else None,
    )
    db.add(step)

    # 返回下一个 step_number
    return step_number + 1
```

### 2. 修改 SSE 事件发送逻辑（可选）

文件: `backend/agent/bid_review_agent.py`

为保持一致性，SSE 事件也采用相同逻辑：
- 每次 LLM 响应发送一个 step 事件
- Tool_calls 和 tool_results 内嵌在事件中

### 3. 修改前端展示

文件: `frontend/src/components/ReviewTimeline.vue`

调整展示逻辑：
- 每个 step_number 为一个"节"
- 节内展开显示所有 tool_calls
- Tool_result 与 tool_call 配对展示

## 测试验证

### 测试用例

1. **新任务测试**: 运行新审查，验证 step_number 连续无重复
2. **历史数据兼容性**: 旧任务数据仍可正确展示
3. **页面对比**: 数据库 step_number 与页面节号一一对应

### 验证 SQL

```sql
-- 验证无重复
SELECT step_number, COUNT(*) as cnt
FROM agent_steps
WHERE task_id = 'xxx'
GROUP BY step_number
HAVING COUNT(*) > 1;

-- 验证连续性
SELECT step_number FROM agent_steps WHERE task_id = 'xxx' ORDER BY step_number;
```

## 影响范围

- `backend/tasks/review_tasks.py`: 修改 `_record_agent_step`
- `backend/agent/bid_review_agent.py`: 可能需要调整 SSE 事件格式
- `frontend/src/components/ReviewTimeline.vue`: 调整展示逻辑
- 已有历史数据不受影响（使用只读展示）

## 风险评估

- **低风险**: 核心逻辑简化，移除复杂的状态跟踪
- **兼容性**: 历史任务数据通过旧的 step_number 读取，不受影响
- **回滚**: 可通过 git revert 快速回滚