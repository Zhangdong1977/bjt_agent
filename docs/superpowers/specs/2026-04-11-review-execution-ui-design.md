# 标书审查执行页面 UI 设计文档

**日期**: 2026-04-11
**状态**: 已批准
**版本**: v1.0

---

## 1. 概述

### 1.1 项目背景

高保真模型 `bidding_review_todo_tasklist.html` 定义了深色主题的两栏布局审查执行界面。当前实现中，`TodoListCard.vue` 和 `SubAgentCard.vue` 组件样式已按高保真模型实现，但缺少整体页面布局和全局主题支持。

### 1.2 需求摘要

1. 新建独立审查执行页面 `/review-execution`
2. 全局深色/浅色模式切换
3. 子代理卡片增加"查看时间线"按钮，展开显示完整执行步骤

---

## 2. 路由设计

### 2.1 新增路由

| 路径 | 组件 | 描述 |
|------|------|------|
| `/projects/:id/review-execution` | `ReviewExecutionView.vue` | 审查执行页面（深色两栏布局）|

### 2.2 跳转逻辑

- `ProjectView.vue` 中"开始审查"按钮点击后，跳转到 `/projects/:id/review-execution`
- 传递 `projectId` 作为路由参数

---

## 3. 全局主题系统

### 3.1 主题变量

在 `frontend/src/assets/themes/` 下创建 `dark.css` 和 `light.css`，定义 CSS 变量：

**深色主题 (dark.css)**:
```css
:root {
  --bg: #0a0a0a;
  --bg1: #111111;
  --bg2: #181818;
  --bg3: #1e1e1e;
  --bg4: #242424;
  --line: #2a2a2a;
  --line2: #333333;
  --dim: #444444;
  --muted: #666666;
  --sub: #888888;
  --text: #cccccc;
  --bright: #eeeeee;
  --white: #f5f5f5;

  --green: #3dd68c;
  --green-dim: #1a4d35;
  --green-bg: #0d2318;
  --amber: #f0a429;
  --amber-dim: #4d3510;
  --amber-bg: #1e1500;
  --blue: #4da6ff;
  --blue-dim: #1a3a5c;
  --blue-bg: #0a1e30;
  --purple: #a78bfa;
  --purple-dim: #3b2f6b;
  --purple-bg: #1a1030;
  --red: #f87171;
  --red-dim: #4d1f1f;
  --red-bg: #200d0d;
  --teal: #2dd4bf;
  --teal-dim: #1a4040;
  --teal-bg: #0a2020;
}
```

**浅色主题 (light.css)**:
```css
:root {
  --bg: #f5f5f5;
  --bg1: #ffffff;
  --bg2: #fafafa;
  --bg3: #f0f0f0;
  --bg4: #e8e8e8;
  --line: #e0e0e0;
  --line2: #d0d0d0;
  --dim: #b0b0b0;
  --muted: #888888;
  --sub: #666666;
  --text: #333333;
  --bright: #111111;
  --white: #ffffff;

  --green: #52c41a;
  --green-dim: #d9f7be;
  --green-bg: #f6ffed;
  --amber: #faad14;
  --amber-dim: #ffe58f;
  --amber-bg: #fffbe6;
  --blue: #1890ff;
  --blue-dim: #91d5ff;
  --blue-bg: #e6f7ff;
  --purple: #722ed1;
  --purple-dim: #d3adf7;
  --purple-bg: #f9f0ff;
  --red: #ff4d4f;
  --red-dim: #ffccc7;
  --red-bg: #fff1f0;
  --teal: #13c2c2;
  --teal-dim: #87e8de;
  --teal-bg: #e6fffb;
}
```

### 3.2 useTheme Composable

创建 `frontend/src/composables/useTheme.ts`:

```typescript
// 状态: theme ('dark' | 'light')
// 方法: toggleTheme(), setTheme(theme)
// 持久化: localStorage key = 'app-theme'
```

### 3.3 主题切换按钮

放置在 `AppHeader.vue` 或各页面顶部右侧，用户点击切换全局主题。

---

## 4. 页面组件结构

### 4.1 组件树

```
ReviewExecutionView (两栏布局容器)
├── ExecutionHeader (顶部栏)
│   ├── BackButton (返回按钮)
│   ├── ProjectTitle (项目名称)
│   └── ThemeToggle (主题切换)
├── ExecutionStepper (四阶段步骤指示器)
├── LeftPane (左侧主区域)
│   ├── MasterAgentSection (主代理输出块)
│   ├── TodoListSection (待办列表)
│   ├── SubAgentsSection (子代理卡片组)
│   │   └── SubAgentCard (×N)
│   │       └── SubAgentTimeline (展开的时间线)
│   └── MergeSection (合并块)
└── RightSidebar (右侧边栏)
    ├── OverallProgress (整体进度)
    ├── StatsGrid (统计数字)
    ├── FindingsSummary (发现问题汇总)
    ├── Legend (图例)
    └── Actions (操作按钮)
```

### 4.2 组件详情

#### ReviewExecutionView.vue
- **职责**: 两栏布局容器，管理 SSE 连接，聚合 projectStore 数据
- **状态**: phase ('master' | 'todo' | 'sub_agents' | 'merging' | 'completed')
- **SSE**: 创建 EventSource 连接到 `/api/events/tasks/:taskId/stream`，调用 handleSSEEvent

#### ExecutionHeader.vue
- **职责**: 顶部标题栏
- **内容**: 返回按钮、项目名称、主題切換按鈕、状态徽章

#### ExecutionStepper.vue
- **职责**: 四阶段横向步骤指示器
- **阶段**: 解析规则库 → 生成待办 → 子代理执行 → 合并质检
- **状态**: 根据 phase 显示 s-done / s-active / s-wait

#### LeftPane.vue
- **职责**: 左侧主内容区，按 phase 顺序展示各区块
- **区块**: MasterAgentSection → TodoListSection → SubAgentsSection → MergeSection

#### SubAgentCard.vue (改造)
- **改造**: 增加"查看时间线"按钮
- **展开**: 点击按钮后，在卡片下方展开 SubAgentTimeline

#### SubAgentTimeline.vue (新增)
- **职责**: 显示子代理的完整执行步骤
- **内容**: 工具调用、思考过程、观察结果
- **复用**: 复用 ReviewTimeline 的 step 渲染逻辑

#### RightSidebar.vue
- **职责**: 右侧统计面板
- **内容**: 整体进度条、统计数字（规则文档数、检查项总数、已完成/进行中）、发现问题汇总、图例、操作按钮

---

## 5. 数据流

### 5.1 SSE 事件处理

```typescript
// ReviewExecutionView 中
interface SSEEvent {
  type: string
  // master_started | master_scan_completed | todo_created | todo_list_completed
  // | sub_agent_started | sub_agent_progress | sub_agent_completed | sub_agent_failed
  // | merging_started | merging_completed | step
  [key: string]: any
}

function handleSSEEvent(event: SSEEvent) {
  // 根据 event.type 更新本地状态
  // 聚合数据到 stats 对象供右侧面板使用
}
```

### 5.2 数据聚合

右侧面板数据来源：

| 数据项 | 来源 |
|--------|------|
| 规则文档数 | `event.master_scan_completed.total_docs` |
| 检查项总数 | `event.master_scan_completed.total_items` |
| 已完成/进行中/等待 | `todos` Map 状态统计 |
| 严重/一般/通过/未检查 | `todo.result.findings` 聚合 |

---

## 6. 交互设计

### 6.1 步骤指示器

纯展示，根据 phase 状态显示不同样式：
- s-done: 绿色背景、✓ 图标
- s-active: 紫色背景、数字
- s-wait: 灰色背景、数字

### 6.2 子代理卡片

- 默认折叠状态
- 点击卡片头部切换展开/折叠
- "查看时间线"按钮点击后，在卡片底部展开 SubAgentTimeline
- "查看时间线"按钮样式: ghost button with icon

### 6.3 主题切换

- 切换按钮位于 ExecutionHeader 右侧
- 点击切换立即生效
- 主题状态持久化到 localStorage

---

## 7. 文件清单

### 7.1 新增文件

| 文件路径 | 描述 |
|----------|------|
| `frontend/src/assets/themes/dark.css` | 深色主题变量 |
| `frontend/src/assets/themes/light.css` | 浅色主题变量 |
| `frontend/src/composables/useTheme.ts` | 主题状态管理 |
| `frontend/src/views/ReviewExecutionView.vue` | 主页面组件 |
| `frontend/src/components/execution/ExecutionHeader.vue` | 顶部栏 |
| `frontend/src/components/execution/ExecutionStepper.vue` | 步骤指示器 |
| `frontend/src/components/execution/LeftPane.vue` | 左侧主区域 |
| `frontend/src/components/execution/RightSidebar.vue` | 右侧边栏 |
| `frontend/src/components/execution/SubAgentTimeline.vue` | 子代理时间线 |

### 7.2 修改文件

| 文件路径 | 修改内容 |
|----------|----------|
| `frontend/src/router/index.ts` | 添加 /projects/:id/review-execution 路由 |
| `frontend/src/App.vue` | 引入全局主题 CSS |
| `frontend/src/views/ProjectView.vue` | 修改"开始审查"按钮跳转到新页面 |
| `frontend/src/components/SubAgentCard.vue` | 增加"查看时间线"按钮 |

---

## 8. 测试计划

### 8.1 单元测试

- `useTheme` composable: 主题切换状态正确、localStorage 持久化
- `SubAgentTimeline` 组件: step 渲染正确、展开/折叠状态

### 8.2 集成测试

- SSE 事件正确更新各区块显示
- 主题切换影响所有页面
- 路由跳转正确传递 projectId

---

## 9. 依赖项

- Vue 3 Composition API
- vue-router
- pinia (projectStore)
- Ant Design Vue (Timeline, Button, Tag 等组件)
- Element Plus (Message, Progress 等组件)

---

## 10. 附录：高保真模型关键样式

### 10.1 CSS 变量 (深色主题)

```css
--bg: #0a0a0a;
--bg1: #111111;
--bg2: #181818;
--bg3: #1e1e1e;
--bg4: #242424;
--line: #2a2a2a;
--line2: #333333;
--dim: #444444;
--muted: #666666;
--sub: #888888;
--text: #cccccc;
--bright: #eeeeee;
--white: #f5f5f5;

--green: #3dd68c;
--green-dim: #1a4d35;
--green-bg: #0d2318;
--amber: #f0a429;
--amber-dim: #4d3510;
--amber-bg: #1e1500;
--blue: #4da6ff;
--blue-dim: #1a3a5c;
--blue-bg: #0a1e30;
--purple: #a78bfa;
--purple-dim: #3b2f6b;
--purple-bg: #1a1030;
--red: #f87171;
--red-dim: #4d1f1f;
--red-bg: #200d0d;
--teal: #2dd4bf;
--teal-dim: #1a4040;
--teal-bg: #0a2020;

--mono: 'JetBrains Mono', 'Geist Mono', 'Fira Code', monospace;
--r: 6px;
--r2: 10px;
```

### 10.2 布局结构

```
.shell (grid, 100vh)
├── .titlebar (40px, 深色背景)
├── .main (grid, 1fr 320px)
│   ├── .pane-left (左侧, overflow-y: auto)
│   │   ├── .run-header
│   │   ├── .stepper
│   │   ├── .phase (×4)
│   │   └── .merge-block
│   └── .pane-right (320px, 右侧边栏)
```
