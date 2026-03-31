# 时间线状态管理修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复历史标书查看和重新审查时时间线状态混乱的问题

**Architecture:** 通过在 ReviewTimeline.vue 组件中添加 taskId 和 historicalMode 的 watch 监听，以及在 ProjectView.vue 的 startReview 函数中清理历史状态，确保时间线状态正确转换。

**Tech Stack:** Vue 3 Composition API, TypeScript, Pinia Store

---

## 文件结构

- `frontend/src/components/ReviewTimeline.vue` - 时间线组件，添加 taskId 和 historicalMode 监听
- `frontend/src/views/ProjectView.vue` - 项目视图，修复 startReview 清理逻辑

---

## Task 1: ReviewTimeline.vue - 添加 taskId watch

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue:169-175` (connect 函数附近)
- Modify: `frontend/src/components/ReviewTimeline.vue:114-119` (existing watch initialSteps 附近)

- [ ] **Step 1: 在 connect 函数后添加 taskId watch**

在 `ReviewTimeline.vue` 第 169 行 `function connect(taskId: string)` 函数后，添加新的 watch：

```typescript
// Watch for taskId changes (for live review mode - reconnects to new SSE stream)
watch(() => props.taskId, (newTaskId, oldTaskId) => {
  if (!props.historicalMode && newTaskId && newTaskId !== oldTaskId) {
    steps.value = []  // 清理旧步骤
    disconnect()      // 断开旧连接
    connect(newTaskId) // 连接新 SSE
  }
})
```

- [ ] **Step 2: 验证代码位置正确**

检查 watch 是否在 `watch(() => props.initialSteps` 之后添加。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "fix(timeline): add taskId watch for SSE reconnection"
```

---

## Task 2: ReviewTimeline.vue - 添加 historicalMode watch

**Files:**
- Modify: `frontend/src/components/ReviewTimeline.vue` (在 taskId watch 之后)

- [ ] **Step 1: 添加 historicalMode watch**

在刚才添加的 taskId watch 之后，添加 historicalMode watch：

```typescript
// Watch for historicalMode changes
watch(() => props.historicalMode, (isHistorical) => {
  if (isHistorical) {
    disconnect()  // 断开 SSE 连接
  } else if (props.taskId) {
    steps.value = []  // 清理步骤
    connect(props.taskId)  // 连接 SSE
  }
})
```

- [ ] **Step 2: 验证逻辑正确**

确保 historicalMode=true 时断开 SSE，切换到 live 模式时重新连接。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/ReviewTimeline.vue
git commit -m "fix(timeline): add historicalMode watch for SSE lifecycle"
```

---

## Task 3: ProjectView.vue - 修复 startReview 清理历史状态

**Files:**
- Modify: `frontend/src/views/ProjectView.vue:93-104` (startReview 函数)

- [ ] **Step 1: 修改 startReview 函数**

将 `ProjectView.vue` 第 93-104 行的 `startReview` 函数修改为：

```typescript
async function startReview() {
  try {
    // 清理历史时间线状态
    showHistoricalTimeline.value = false
    selectedHistoryTaskId.value = ''
    historicalSteps.value = []

    await projectStore.startReview()
    ElMessage.info('审查已启动，正在连接事件流...')
    // 连接 ReviewTimeline 组件
    if (projectStore.currentTask?.id) {
      timelineRef.value?.connect(projectStore.currentTask.id)
    }
  } catch {
    ElMessage.error('启动审查失败')
  }
}
```

关键变更：在 `await projectStore.startReview()` 之前添加清理逻辑。

- [ ] **Step 2: 验证清理逻辑存在**

确认 `showHistoricalTimeline.value = false` 在新任务启动前执行。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "fix(view): clear historical timeline state on startReview"
```

---

## Task 4: 手动测试验证

- [ ] **Step 1: 测试进入项目页面**

进入有空历史任务的项目页面，确认时间线为空。

- [ ] **Step 2: 测试查看历史记录**

选择历史任务，确认显示历史时间线。

- [ ] **Step 3: 测试重新审查**

点击"开始审查"或"重新审查"，确认：
- 时间线清空并显示新任务 SSE 事件
- 下拉框恢复到 `-- 选择历史任务 --`

---

## 验证命令

```bash
# 启动前端开发服务器
cd frontend && npm run dev

# 访问项目页面
# http://localhost:3000/projects/{project_id}
```

## 预期结果

| 场景 | 预期行为 |
|------|---------|
| 进入项目页面 | 时间线为空，不显示历史时间线 |
| 选择历史任务 | 显示该任务的历史时间线 |
| 点击"开始审查" | 清理历史时间线，显示新时间线，下拉框恢复初始状态 |
