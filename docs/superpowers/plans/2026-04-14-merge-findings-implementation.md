# 合并结果功能修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复合并结果组件显示"0发现问题"的问题，通过在 SSE 事件中直接传递完整 findings 数据

**Architecture:** 后端 MasterAgent 在 `sub_agent_completed` 事件中携带完整 findings 数组，前端 ReviewExecutionView 接收并保存到 todo.result.findings，供 LeftPane 的 mergeStats 聚合计算

**Tech Stack:** Vue3, TypeScript, FastAPI, Python

---

## 文件修改清单

| 文件 | 修改内容 |
|------|----------|
| `backend/agent/master/master_agent.py:206-209` | 扩展 `sub_agent_completed` 事件 payload |
| `frontend/src/views/ReviewExecutionView.vue:108` | 修改 SSE 事件处理，传递 findings |
| `frontend/src/views/ReviewExecutionView.vue:259-271` | 修改 `updateTodoStatus` 函数签名和逻辑 |

---

## Task 1: 后端 - 扩展 sub_agent_completed 事件

**Files:**
- Modify: `backend/agent/master/master_agent.py:206-209`

- [ ] **Step 1: 修改 sub_agent_completed 事件**

找到 `backend/agent/master/master_agent.py` 第 206-209 行：

```python
# 当前代码（约第 206-209 行）：
self._send_event("sub_agent_completed", {
    "todo_id": todo.id,
    "findings_count": len(findings),
})
```

替换为：

```python
self._send_event("sub_agent_completed", {
    "todo_id": todo.id,
    "findings_count": len(findings),
    "findings": findings,
})
```

- [ ] **Step 2: 验证修改**

运行以下命令确认修改正确：
```bash
grep -n "sub_agent_completed" backend/agent/master/master_agent.py | head -5
```

预期输出应包含 `"findings": findings,`

---

## Task 2: 前端 - 修改 SSE 事件处理

**Files:**
- Modify: `frontend/src/views/ReviewExecutionView.vue:108`

- [ ] **Step 1: 修改 SSE 事件 case 分支**

找到 `frontend/src/views/ReviewExecutionView.vue` 约第 108 行的 `sub_agent_completed` 处理：

```typescript
// 当前代码（约第 106-109 行）：
case 'sub_agent_completed':
  // 子代理完成
  updateTodoStatus(event.todo_id, 'completed', event.findings_count)
  break
```

替换为：

```typescript
case 'sub_agent_completed':
  // 子代理完成
  updateTodoStatus(event.todo_id, 'completed', event.findings)
  break
```

- [ ] **Step 2: 验证修改**

```bash
grep -n "sub_agent_completed" frontend/src/views/ReviewExecutionView.vue
```

---

## Task 3: 前端 - 修改 updateTodoStatus 函数

**Files:**
- Modify: `frontend/src/views/ReviewExecutionView.vue:259-271`

- [ ] **Step 1: 修改 updateTodoStatus 函数签名和逻辑**

找到 `frontend/src/views/ReviewExecutionView.vue` 约第 259-271 行的 `updateTodoStatus` 函数：

```typescript
// 当前代码：
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

替换为：

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

- [ ] **Step 2: 验证 TypeScript 类型**

运行前端类型检查：
```bash
cd frontend && npm run build 2>&1 | head -30
```

预期：无类型错误

---

## Task 4: 提交更改

- [ ] **Step 1: 检查修改状态**

```bash
git status
```

- [ ] **Step 2: 提交后端修改**

```bash
git add backend/agent/master/master_agent.py
git commit -m "fix(backend): include findings in sub_agent_completed event

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: 提交前端修改**

```bash
git add frontend/src/views/ReviewExecutionView.vue
git commit -m "fix(frontend): pass findings to updateTodoStatus and save to todo.result

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 验证清单

实现完成后，验证以下场景：

1. **SSE 事件验证**：后端发送的 `sub_agent_completed` 事件包含 `findings` 数组字段
2. **前端数据流**：`updateTodoStatus` 正确保存 findings 到 `todo.result.findings`
3. **统计计算**：`mergeStats` 能正确聚合 todos 中的 findings（total, critical, major, minor, compliant）
4. **UI 显示**：LeftPane 组件中的合并结果统计显示实际数量（非零值）

---

## 相关文件参考

- 设计文档: `docs/superpowers/specs/2026-04-14-merge-findings-design.md`
- 后端 MasterAgent: `backend/agent/master/master_agent.py`
- 前端 ReviewExecutionView: `frontend/src/views/ReviewExecutionView.vue`
- 前端 LeftPane: `frontend/src/components/execution/LeftPane.vue` (mergeStats 计算属性)
