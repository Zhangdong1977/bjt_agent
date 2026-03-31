# 时间线状态管理修复方案

> **Goal:** 修复历史标书查看和重新审查时时间线状态混乱的问题

## 问题描述

当前进入项目页面后：
1. 如果有已完成的历史任务，时间线显示旧数据
2. 点击"开始审查"后，旧时间线仍然显示
3. 历史记录下拉框不会恢复到初始状态

## 预期行为

| 动作 | 结果 |
|------|------|
| 进入项目页面 | 时间线为空，不显示任何历史时间线 |
| 选择历史任务 | 显示该任务的历史时间线 |
| 点击"开始审查" | 清理历史时间线，显示新时间线，下拉框恢复初始状态 |

## 实现方案

### 1. ReviewTimeline.vue - 添加 taskId 监听

**文件**: `frontend/src/components/ReviewTimeline.vue`

**问题**: 只在 `onMounted` 时连接 SSE，taskId 变化时不响应

**修复**: 添加 watch 监听 taskId 变化

```typescript
watch(() => props.taskId, (newTaskId, oldTaskId) => {
  if (!props.historicalMode && newTaskId && newTaskId !== oldTaskId) {
    steps.value = []  // 清理旧步骤
    disconnect()      // 断开旧连接
    connect(newTaskId) // 连接新 SSE
  }
})
```

### 2. ProjectView.vue - startReview 清理历史状态

**文件**: `frontend/src/views/ProjectView.vue`

**问题**: startReview 不清理历史时间线相关状态

**修复**: startReview 函数添加清理逻辑

```typescript
async function startReview() {
  try {
    // 清理历史时间线状态
    showHistoricalTimeline.value = false
    selectedHistoryTaskId.value = ''
    historicalSteps.value = []

    await projectStore.startReview()
    // ...
  }
}
```

### 3. ReviewTimeline.vue - historicalMode 切换处理

**文件**: `frontend/src/components/ReviewTimeline.vue`

**问题**: historicalMode 切换时未正确断开/连接 SSE

**修复**: 添加 historicalMode watch

```typescript
watch(() => props.historicalMode, (isHistorical) => {
  if (isHistorical) {
    disconnect()  // 断开 SSE 连接
  } else if (props.taskId) {
    steps.value = []  // 清理步骤
    connect(props.taskId)  // 连接 SSE
  }
})
```

## 涉及文件

- `frontend/src/components/ReviewTimeline.vue` - 时间线组件
- `frontend/src/views/ProjectView.vue` - 项目视图

## 测试验证

1. 进入有空历史任务的项目页面 → 时间线为空
2. 选择历史任务 → 显示历史时间线
3. 点击"开始审查" → 时间线清空，显示新任务 SSE 事件
4. 下拉框恢复到 `-- 选择历史任务 --`
