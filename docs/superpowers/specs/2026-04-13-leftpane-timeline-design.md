# LeftPane.vue 重新设计 - 垂直嵌套时间线

## 概述

将 LeftPane.vue 从平铺式布局改为垂直嵌套时间线布局，清晰展示 MasterAgent → SubAgentExecutor → BidReviewAgent 的父子调用关系，以及 BidReviewAgent 内部的 LLM 调用时间线。

## 架构

```
MasterAgent (垂直时间线)
    │
    ├── 步骤节点 (master 类型)
    │       └── 工具调用详情
    │
    ├── SubAgentExecutorBlock
    │       │
    │       └── BidReviewAgentBlock A1
    │       │       ├── 创建节点
    │       │       ├── LLM 调用节点 #N
    │       │       │       ├── 工具调用 (tool_call)
    │       │       │       ├── 观察 (observation)
    │       │       │       └── 思考 (thought)
    │       │       └── Findings
    │       │
    │       └── BidReviewAgentBlock A2
    │       └── ...
    │
    └── 汇总节点
```

## 组件设计

### 1. AgentTimelineItem

单个时间线节点组件。

**Props:**
```typescript
interface Props {
  stepNumber: number
  stepType: 'master' | 'observation' | 'tool_call' | 'thought' | 'tool_result'
  content: string
  timestamp: Date
  toolCalls?: ToolCall[]
  toolResults?: ToolResult[]
  status?: 'pending' | 'running' | 'completed' | 'error'
  duration?: number
}
```

**视觉:**
- 左侧圆点 + 连接线
- 卡片背景色根据 `stepType` 变化
  - `master`: `--purple-bg`, 左边框 `--purple`
  - `observation`: `--green-bg`, 左边框 `--green`
  - `tool_call`: `--amber-bg`, 左边框 `--amber`
  - `thought`: `--blue-bg`, 左边框 `--blue`
- 时间戳显示在右上角
- 工具调用内嵌在卡片内

### 2. BidReviewAgentBlock

BidReviewAgent 执行块，可折叠。

**Props:**
```typescript
interface Props {
  agentId: string
  title: string
  ruleFile: string
  status: 'done' | 'running' | 'wait'
  steps: TimelineStep[]  // 内部 LLM 调用时间线
  findings: Finding[]
}
```

**视觉:**
- 卡片头部：Agent ID + 标题 + 状态 + 进度条
- 可折叠的身体：显示内部时间线
- Findings 栏位显示审查结果标签

### 3. SubAgentExecutorBlock

包含多个 BidReviewAgentBlock 的容器。

**Props:**
```typescript
interface Props {
  agents: SubAgentTimelineData[]
}
```

**视觉:**
- 左侧缩进 + 连接线表示层级
- 垂直排列 BidReviewAgentBlock

### 4. FindingsBar

Findings 结果展示。

**Props:**
```typescript
interface Props {
  findings: Finding[]
}
interface Finding {
  type: 'crit' | 'major' | 'pass'
  text: string
}
```

**视觉:**
- 标签样式，颜色根据 type
  - `crit`: `--red-bg`, `--red-dim`, `--red`
  - `major`: `--amber-bg`, `--amber-dim`, `--amber`
  - `pass`: `--green-bg`, `--green-dim`, `--green`

## 数据接口

### Props (LeftPane)

```typescript
interface Props {
  phase: 'pending' | 'running' | 'completed' | 'failed'
  steps: TimelineStep[]  // MasterAgent 步骤 + 观察
  todos?: TodoItemState[]  // SubAgentExecutor 的子代理列表
  subAgentTimelines?: SubAgentTimelineData[]  // BidReviewAgent 内部时间线
  errorMessage?: string | null
}
```

### 新增类型

```typescript
interface SubAgentTimelineData {
  agentId: string
  title: string
  ruleFile: string
  status: 'done' | 'running' | 'wait'
  checkItems: CheckItem[]
  steps: TimelineStep[]  // BidReviewAgent 内部时间线
  findings: Finding[]
  runningLog?: string
}

interface ToolCall {
  name: string
  arguments: Record<string, any>
}

interface ToolResult {
  name: string
  result: { status: string; content?: string; error?: string }
}

interface TimelineStep {
  step_number: number
  step_type: 'master' | 'observation' | 'tool_call' | 'thought' | 'tool_result'
  content: string
  timestamp: Date
  tool_args?: { tool_calls?: ToolCall[] }
  tool_result?: { tool_results?: ToolResult[] }
  duration?: number
}
```

## 时间线顺序

1. **MasterAgent 阶段**
   - `master_started` → 显示 MasterAgent 开始
   - `master_scan_completed` → 显示扫描结果 + 工具调用

2. **SubAgentExecutor 阶段**
   - `todo_created` → 创建 SubAgentExecutorBlock
   - `sub_agent_started` → 展开对应 BidReviewAgentBlock
   - `step` (observation/thought) → BidReviewAgentBlock 内部节点
   - `sub_agent_completed` → BidReviewAgentBlock 完成，显示 Findings

3. **汇总阶段**
   - `merging_completed` → MasterAgent 汇总节点

## 视觉保留

保持现有视觉元素：
- CSS 变量：`--bg1`, `--bg2`, `--bg3`, `--line`, `--purple`, `--green`, `--amber`, `--blue`, `--red`
- 卡片样式：`output-block`, `phase-block`
- Chip 样式
- 折叠/展开交互

## 文件变更

- `frontend/src/components/execution/LeftPane.vue` - 重写
- `frontend/src/components/execution/AgentTimelineItem.vue` - 新增
- `frontend/src/components/execution/BidReviewAgentBlock.vue` - 新增
- `frontend/src/components/execution/SubAgentExecutorBlock.vue` - 新增
- `frontend/src/components/execution/FindingsBar.vue` - 新增
