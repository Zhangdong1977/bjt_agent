# 审查结果与时间线分离设计方案

## 1. 设计目标

将项目详情页中的"审查结果"和"时间线"按数据粒度分离：
- **审查结果**：项目级别，始终显示本项目最新的审查结果
- **时间线**：任务级别，随用户选择的历史任务变化

## 2. 架构设计

```
ProjectView.vue
├── DocumentsSection (文档管理 - 不变)
├── ReviewSection (重构为两个独立子区域)
│   ├── ReviewResultsArea      ← 新增：项目级别审查结果
│   │   └── 获取最新任务的审查结果
│   └── TimelineArea           ← 重构：任务级别时间线
│       ├── TaskSelector        ← 新增：历史任务下拉选择器
│       ├── ReviewTimeline      ← 现有：时间线组件
│       └── StartReviewButton  ← 现有：开始审查按钮
```

## 3. 数据流设计

| 操作 | 审查结果（项目级别） | 时间线（任务级别） |
|------|---------------------|-------------------|
| **进入页面** | 获取最新任务的审查结果 | 获取最新任务的 steps |
| **选择历史任务** | 不变（仍显示最新） | 加载选中任务的 steps |
| **开始新审查** | 新任务完成后自动更新 | 实时显示新任务 steps |

### API 调用

- `GET /api/projects/{id}/review` → 获取最新审查结果
- `GET /api/projects/{id}/review/tasks` → 获取任务列表（填充下拉选择器）
- `GET /api/projects/{id}/review/tasks/{task_id}/steps` → 获取选中任务的时间线

## 4. 组件接口设计

### TimelineArea 组件

```typescript
// Props
interface TimelineAreaProps {
  projectId: string
}

// 内部管理：
// - selectedTaskId: 当前选中的任务ID
// - steps: 时间线步骤数据

// Events
interface TimelineAreaEvents {
  onTaskComplete: (taskId: string) => void  // 新任务完成时触发
}
```

### ReviewResultsArea 组件

```typescript
// Props
interface ReviewResultsAreaProps {
  projectId: string  // 只用于获取最新审查结果
}
```

## 5. TimelineArea 内部逻辑

### 状态管理

```typescript
// TimelineArea 内部状态
const selectedTaskId = ref<string | null>(null)  // 当前选中的任务ID
const taskOptions = ref<TaskOption[]>([])         // 下拉选择器选项
const steps = ref<TimelineStep[]>([])             // 时间线步骤

// 监听 selectedTaskId 变化，重新获取时间线
watch(selectedTaskId, async (newTaskId) => {
  if (newTaskId) {
    steps.value = await fetchTaskSteps(projectId, newTaskId)
  }
})
```

### 开始审查流程

1. 点击开始审查 → 创建新任务
2. SSE 连接获取实时 steps → 更新 `steps` 数组
3. 任务完成 → `selectedTaskId` 设为新任务 ID → 触发外部刷新审查结果

## 6. 实施步骤

### 阶段 1：数据层准备
1. 确认后端 `GET /api/projects/{id}/review/tasks` 接口返回任务列表
2. 确认前端 API 客户端有对应方法

### 阶段 2：组件创建
1. 创建 `ReviewResultsArea.vue` 组件
2. 创建 `TimelineArea.vue` 组件（含 TaskSelector）

### 阶段 3：集成与测试
1. 在 `ProjectView.vue` 中替换原有 ReviewSection
2. 处理开始审查按钮的回调
3. 测试各交互场景

## 7. 预期效果

- 进入项目详情页 → 显示最新审查结果 + 最新任务时间线
- 选择历史任务 → 时间线更新，审查结果不变
- 开始新审查 → 实时时间线，结束后审查结果自动更新
