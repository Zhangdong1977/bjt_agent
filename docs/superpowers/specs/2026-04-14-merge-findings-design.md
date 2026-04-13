# 合并结果功能修复设计

## 概述

**问题**：`sub_agent_completed` 事件仅发送 `findings_count`，未携带实际 findings 数组，导致前端合并统计始终显示 0。

**解决方案**：扩展 SSE 事件 payload，在 `sub_agent_completed` 事件中直接传递完整 findings 数据。

---

## 数据流

```
后端 MasterAgent
    │
    ├── sub_agent_completed 事件
    │   └── { todo_id, findings_count, findings: [...] }
    │
    ▼
前端 ReviewExecutionView
    │
    ├── handleSSEEvent()
    │   └── updateTodoStatus(todoId, status, findings)
    │
    ▼
LeftPane
    │
    └── mergeStats computed
        └── 聚合 todos 中的 findings → 显示统计
```

---

## 后端修改

**文件**: `backend/agent/master/master_agent.py`

### 第 206-209 行

**当前代码**：
```python
self._send_event("sub_agent_completed", {
    "todo_id": todo.id,
    "findings_count": len(findings),
})
```

**修改为**：
```python
self._send_event("sub_agent_completed", {
    "todo_id": todo.id,
    "findings_count": len(findings),
    "findings": findings,
})
```

---

## 前端修改

**文件**: `frontend/src/views/ReviewExecutionView.vue`

### 1. SSE 事件处理 (约第 108 行)

**当前代码**：
```typescript
case 'sub_agent_completed':
  updateTodoStatus(event.todo_id, 'completed', event.findings_count)
  break
```

**修改为**：
```typescript
case 'sub_agent_completed':
  updateTodoStatus(event.todo_id, 'completed', event.findings)
  break
```

### 2. updateTodoStatus 函数 (约第 259 行)

**当前代码**：
```typescript
function updateTodoStatus(todoId: string, status: TodoItemState['status'], _findingsCount?: number, error?: string) {
  const todo = todos.value.get(todoId)
  if (todo) {
    todo.status = status
    if (status === 'completed') {
      todo.result = { findings: [] }
    }
    if (error) {
      todo.error_message = error
    }
    todos.value.set(todoId, { ...todo })
  }
}
```

**修改为**：
```typescript
function updateTodoStatus(todoId: string, status: TodoItemState['status'], findings?: any[], error?: string) {
  const todo = todos.value.get(todoId)
  if (todo) {
    todo.status = status
    if (status === 'completed' && findings) {
      todo.result = { findings }
    }
    if (error) {
      todo.error_message = error
    }
    todos.value.set(todoId, { ...todo })
  }
}
```

---

## Findings 数据结构

后端发送的 `findings` 数组包含以下字段（来自 `ReviewResult` 模型）：

| 字段 | 类型 | 说明 |
|------|------|------|
| requirement_key | string | 需求标识 |
| requirement_content | string | 需求内容 |
| bid_content | string \| null | 投标内容 |
| is_compliant | boolean | 是否合规 |
| severity | string | 严重程度 (critical/major/minor) |
| location_page | number \| null | 页码位置 |
| location_line | number \| null | 行号位置 |
| suggestion | string \| null | 建议 |
| explanation | string \| null | 说明 |

---

## 验证要点

1. `sub_agent_completed` 事件 payload 包含 `findings` 数组
2. `updateTodoStatus` 正确保存 findings 到 `todo.result.findings`
3. `mergeStats` 正确聚合 todos 中的 findings
4. LeftPane 组件正确显示统计数字（非零值）
